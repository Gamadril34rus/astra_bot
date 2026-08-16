#!/usr/bin/env python3
"""Exhaustive historical market-learning pass.

Research only: no money, no exchange orders, no capital constraints.
History is requested with a generous upper bound and pagination continues
until the exchange stops returning older candles. Each instrument therefore
uses the oldest data actually available for that instrument.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from decimal import Decimal
from pathlib import Path

from astra_bot.core import models
from astra_bot.core.instruments import TRADING_UNIVERSE
from astra_bot.ml import self_play as sp
from astra_bot.ml.historical_training import fetch_historical_candles
from astra_bot.ml.market_understanding import compute_market_features
from astra_bot.ml.news_features import NewsFeatureService
from astra_bot.ml.weekly_learner import train_weekly
from astra_bot.strategies import MeanReversionStrategy, MomentumStrategy, PullbackStrategy

MAX_LESSONS = 500_000
MAX_HISTORY_YEARS = 25


async def _fetch_one(client, symbol: str):
    try:
        bars = await fetch_historical_candles(
            client=client,
            symbol=symbol.replace("/", "-"),
            timeframe="1h",
            # Large safety bound; the loader stops at the oldest candle the API exposes.
            lookback_days=MAX_HISTORY_YEARS * 365,
            sleep_between_requests=0.0,
        )
        for bar in bars:
            bar.symbol = symbol
        return symbol, bars
    except Exception:
        return symbol, []


async def fetch_history() -> dict[str, list[models.Candle]]:
    client = sp.OKXClient({
        "api_key": "", "api_secret": "", "sandbox": False,
        "enabled": True, "rate_limit_qps": 8,
    })
    await client.initialize()
    try:
        results = await asyncio.gather(*[_fetch_one(client, symbol) for symbol in TRADING_UNIVERSE])
        return {symbol: bars for symbol, bars in results if bars}
    finally:
        await client.close()


def _lesson_from_signal(signal, strategy, window, future, cross, news):
    features = compute_market_features(
        window,
        timeframe=window[-1].timeframe,
        extra_features=sp._feature_snapshot(strategy, window, cross),
    )
    news_snapshot = news.cached_historical(window[-1].symbol, window[-1].open_time)
    features.update(news_snapshot.to_features())

    direction = signal.direction.value
    entry = Decimal(str(signal.entry_price))
    stop = Decimal(str(signal.stop_loss))
    take = Decimal(str(signal.take_profit))
    qty = Decimal("1")
    exit_price = entry
    exit_time = window[-1].open_time

    for bar in future[:48]:
        if direction == "long":
            if bar.low <= stop:
                exit_price = stop
                exit_time = bar.open_time
                break
            if bar.high >= take:
                exit_price = take
                exit_time = bar.open_time
                break
        else:
            if bar.high >= stop:
                exit_price = stop
                exit_time = bar.open_time
                break
            if bar.low <= take:
                exit_price = take
                exit_time = bar.open_time
                break
    else:
        if future:
            last = future[min(47, len(future) - 1)]
            exit_price = last.close
            exit_time = last.open_time

    gross = (exit_price - entry) * qty if direction == "long" else (entry - exit_price) * qty
    fee = entry * qty * Decimal("0.0005")
    pnl = gross - fee
    outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven"
    recommendation = sp._recommend(outcome, features, direction)
    influencing = sp._influencing_factor(features, outcome)
    regime = sp._classify_regime(window)

    return {
        "trade_id": f"hist-{uuid.uuid4()}",
        "symbol": window[-1].symbol,
        "direction": direction,
        "entry_time": window[-1].open_time,
        "exit_time": exit_time,
        "entry_price": float(entry),
        "exit_price": float(exit_price),
        "qty": 1.0,
        "pnl": float(pnl),
        "pnl_pct": float(pnl / max(entry, Decimal("1e-12")) * 100),
        "outcome": outcome,
        "strategy": strategy.name,
        "confidence": float(signal.confidence),
        "features": {k: float(v) for k, v in features.items()},
        "market_regime": regime,
        "news_impulse": abs(float(news_snapshot.shock)) > 0.5,
        "news_source": news_snapshot.source,
        "news_articles": news_snapshot.articles,
        "influencing_factor": influencing,
        "counterfactual": sp._counterfactual(outcome, direction, features),
        "takeaway": f"{window[-1].symbol} {direction.upper()} {outcome}; regime={regime}",
        "recommendation": recommendation,
        "training_phase": "max_available_history_exhaustive_walk_forward",
        "feature_engine": "market_understanding_v1",
    }


async def run(args) -> int:
    history = await fetch_history()
    usable = {s: bars for s, bars in history.items() if len(bars) >= 250}
    if len(usable) < 10:
        raise RuntimeError(f"Недостаточно истории: {len(usable)}/{len(TRADING_UNIVERSE)} инструментов")

    news = NewsFeatureService(Path("models/news_cache.json"))
    if args.with_news:
        from scripts.pretrain_5y import build_monthly_news_cache
        # NewsAPI historical depth is provider-limited; keep the provider's own
        # available range rather than inventing older news.
        await build_monthly_news_cache(Path("models/news_cache.json"), 5)
        news = NewsFeatureService(Path("models/news_cache.json"))

    strategies = [PullbackStrategy(), MomentumStrategy(), MeanReversionStrategy()]
    lessons: list[dict] = []
    limit = min(args.max_lessons, MAX_LESSONS)

    timestamps = sorted(set.intersection(*(set(c.open_time for c in bars) for bars in usable.values())))
    indexes = {s: {c.open_time: i for i, c in enumerate(bars)} for s, bars in usable.items()}

    for step, ts in enumerate(timestamps):
        if step < 250:
            continue
        cross = {}
        for symbol, bars in usable.items():
            i = indexes[symbol].get(ts)
            if i is not None and i >= 1:
                prev = float(bars[i - 1].close)
                curr = float(bars[i].close)
                cross[f"{symbol}_1h"] = curr / prev - 1 if prev else 0.0

        for symbol, bars in usable.items():
            idx = indexes[symbol].get(ts)
            if idx is None or idx < 250:
                continue
            window = bars[: idx + 1]
            future = bars[idx + 1 : idx + 49]
            if not future:
                continue

            regime = sp._classify_regime(window)
            for strategy in strategies:
                try:
                    signal = await strategy.evaluate(
                        symbol=symbol,
                        candles=window,
                        current_price=float(window[-1].close),
                        market_regime=regime,
                    )
                except Exception:
                    continue
                if not signal or signal.risk_reward_ratio < 0.5:
                    continue
                lessons.append(_lesson_from_signal(signal, strategy, window, future, cross, news))
                if len(lessons) >= limit:
                    break
            if len(lessons) >= limit:
                break
        if len(lessons) >= limit:
            break

    path = Path("models/lessons.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in lessons:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    from astra_bot.ml.market_memory import MarketMemory
    memory = MarketMemory(Path("models/market_memory.json"))
    memory.build_from_lessons(path)

    result = train_weekly(
        lessons_path=path,
        model_path=Path("models/current.pkl"),
        min_samples=args.min_samples,
    )
    print(json.dumps({
        "lessons": len(lessons),
        "symbols": len(usable),
        "history_mode": "max_available_per_instrument",
        "max_history_years_safety_bound": MAX_HISTORY_YEARS,
        "timestamps": len(timestamps),
        "model_trained": result.trained,
        "model_message": result.message,
        "model_auc": result.roc_auc,
        "model_accuracy": result.accuracy,
        "memory_patterns": len(memory.data.get("patterns", {})),
    }, ensure_ascii=False, indent=2))
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-lessons", type=int, default=500000)
    parser.add_argument("--min-samples", type=int, default=2000)
    parser.add_argument("--with-news", action="store_true")
    return await run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
