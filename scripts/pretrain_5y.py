#!/usr/bin/env python3
"""Пятилетнее pretrain ASTRA.

1. Загружает до 5 лет 1h OHLCV по 35 активам с OKX.
2. Декомпозирует 1h историю в 4h и 1d.
3. Прогоняет walk-forward paper trading на каждом горизонте.
4. Записывает каждую виртуальную сделку как урок.
5. При NEWS_API_KEY добавляет исторический news sentiment из NewsAPI.
6. Обучает LightGBM и сохраняет models/current.pkl.

Реальные ордера не используются.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from astra_bot.adapters.okx import OKXClient
from astra_bot.core.instruments import TRADING_UNIVERSE
from astra_bot.core.logger import setup_logging
from astra_bot.ml.news_features import ASSET_ALIASES, NewsFeatureService, NewsSnapshot, _score_text
from astra_bot.ml.self_play import SelfPlayConfig, SelfPlayEngine
from astra_bot.ml.weekly_learner import train_weekly
from astra_bot.ml.historical_training import OKXRateLimiter, fetch_historical_candles

LOGGER = logging.getLogger("pretrain_5y")


def resample_candles(candles, hours: int):
    """Агрегировать 1h свечи в N-часовые бары."""
    from collections import OrderedDict
    from astra_bot.core import models

    buckets: OrderedDict[int, list] = OrderedDict()
    bucket_ms = hours * 60 * 60 * 1000
    for candle in candles:
        bucket = (int(candle.open_time) // bucket_ms) * bucket_ms
        buckets.setdefault(bucket, []).append(candle)

    out = []
    for ts, rows in buckets.items():
        if not rows:
            continue
        out.append(models.Candle(
            exchange="okx",
            symbol=rows[0].symbol,
            timeframe=f"{hours}h" if hours < 24 else "1d",
            open_time=ts,
            open=rows[0].open,
            high=max(r.high for r in rows),
            low=min(r.low for r in rows),
            close=rows[-1].close,
            volume=sum((r.volume for r in rows), Decimal("0")),
            quote_volume=sum((r.quote_volume for r in rows), Decimal("0")),
            trades_count=sum(getattr(r, "trades_count", 0) or 0 for r in rows),
        ))
    return out


async def _fetch_symbol(client: OKXClient, symbol: str, days: int, limiter: OKXRateLimiter):
    try:
        bars = await fetch_historical_candles(
            client=client,
            symbol=symbol.replace("/", "-"),
            timeframe="1h",
            lookback_days=days,
            sleep_between_requests=0.25,
            limiter=limiter,
        )
        for bar in bars:
            bar.symbol = symbol
        LOGGER.info("history %s: %d candles", symbol, len(bars))
        return symbol, bars
    except Exception as exc:
        LOGGER.exception("history failed %s: %s", symbol, exc)
        return symbol, []


async def fetch_history(days: int) -> dict[str, list]:
    """Fetch all symbols through one shared limiter.

    The previous implementation created a separate limiter per symbol and then
    launched all 35 downloads concurrently. That defeated throttling entirely
    and produced OKX 50011 bursts. A single limiter serializes requests while
    allowing symbol-level tasks to overlap their waiting periods.
    """
    client = OKXClient({
        "api_key": "", "api_secret": "", "sandbox": False,
        "enabled": True, "rate_limit_qps": 1.2,
    })
    limiter = OKXRateLimiter(0.25)
    await client.initialize()
    try:
        results = await asyncio.gather(*[
            _fetch_symbol(client, symbol, days, limiter) for symbol in TRADING_UNIVERSE
        ])
        return {symbol: bars for symbol, bars in results}
    finally:
        await client.close()


def _news_query_text() -> str:
    return "crypto bitcoin ethereum blockchain regulation ETF"


async def build_monthly_news_cache(path: Path, years: int) -> None:
    """Собрать грубый исторический market/asset news cache.

    Используется только при наличии NEWS_API_KEY. Один запрос на месяц,
    чтобы не превращать обучение в бессмысленный API DDOS.
    """
    api_key = os.getenv("NEWS_API_KEY", "").strip()
    if not api_key:
        LOGGER.warning("NEWS_API_KEY не задан: историческое news обучение пропущено")
        return

    import aiohttp
    from dateutil.relativedelta import relativedelta

    aliases = {asset: set(words) for asset, words in ASSET_ALIASES.items()}
    start = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0) - relativedelta(years=years)
    end = datetime.now(tz=UTC)
    cursor = start
    news = NewsFeatureService(path)
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        while cursor < end:
            month_end = min(cursor + relativedelta(months=1), end)
            params = {
                "q": f'({_news_query_text()})',
                "from": cursor.isoformat(),
                "to": month_end.isoformat(),
                "language": "en",
                "sortBy": "relevancy",
                "pageSize": 100,
            }
            try:
                async with session.get(
                    "https://newsapi.org/v2/everything",
                    params=params,
                    headers={"X-Api-Key": api_key},
                ) as resp:
                    if resp.status != 200:
                        LOGGER.warning("NewsAPI %s for %s", resp.status, cursor.date())
                        cursor = month_end
                        continue
                    data = await resp.json()
            except Exception as exc:
                LOGGER.warning("NewsAPI error %s: %s", cursor.date(), exc)
                cursor = month_end
                continue

            articles = data.get("articles") or []
            asset_scores: dict[str, list[float]] = {asset: [] for asset in aliases}
            global_scores: list[float] = []
            for article in articles:
                text = f"{article.get('title', '')} {article.get('description', '')}".lower()
                score = _score_text(text)
                global_scores.append(score)
                for asset, words in aliases.items():
                    if any(word in text for word in words):
                        asset_scores[asset].append(score)

            global_snapshot = NewsSnapshot(
                sentiment=(sum(global_scores) / len(global_scores)) if global_scores else 0.0,
                volume=min(1.0, len(global_scores) / 100.0),
                confidence=min(1.0, len(global_scores) / 30.0),
                shock=0.0,
                source="newsapi",
                articles=len(global_scores),
            )
            key_month = cursor.strftime("%Y-%m")
            for symbol in TRADING_UNIVERSE:
                asset = symbol.split("/")[0]
                scores = asset_scores.get(asset) or []
                snapshot = NewsSnapshot(
                    sentiment=(sum(scores) / len(scores)) if scores else global_snapshot.sentiment,
                    volume=min(1.0, len(scores) / 30.0) if scores else global_snapshot.volume,
                    confidence=min(1.0, len(scores) / 10.0) if scores else global_snapshot.confidence,
                    shock=0.0,
                    source="newsapi",
                    articles=len(scores),
                )
                news._cache[f"{symbol}:{key_month}"] = snapshot.__dict__
            cursor = month_end
            if len(news._cache) % 100 < len(TRADING_UNIVERSE):
                LOGGER.info("news cache through %s", key_month)

    news.save_cache()


def augment_lessons_with_news(path: Path, news: NewsFeatureService) -> None:
    if not path.exists():
        return
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = int(row.get("entry_time") or row.get("timestamp") or 0)
            symbol = row.get("symbol", "")
            snap = news.cached_historical(symbol, ts) if ts else NewsSnapshot()
            row.setdefault("features", {}).update(snap.to_features())
            row["news_source"] = snap.source
            row["news_articles"] = snap.articles
            rows.append(row)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--target-trades", type=int, default=5000)
    parser.add_argument("--min-samples", type=int, default=2000)
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--with-news", action="store_true")
    args = parser.parse_args()

    setup_logging()
    days = args.years * 365
    history = await fetch_history(days)
    usable = {s: bars for s, bars in history.items() if len(bars) >= 200}
    LOGGER.info("История готова: %d/%d инструментов", len(usable), len(TRADING_UNIVERSE))

    if not usable:
        raise RuntimeError("Не удалось получить достаточную историю")

    news = NewsFeatureService()
    if args.with_news:
        await build_monthly_news_cache(Path("models/news_cache.json"), args.years)
        news = NewsFeatureService()

    for tf, hours in (("1h", 1), ("4h", 4), ("1d", 24)):
        tf_history = {
            symbol: (bars if hours == 1 else resample_candles(bars, hours))
            for symbol, bars in usable.items()
        }
        config = SelfPlayConfig(
            timeframe=tf,
            symbols=tuple(tf_history.keys()),
            initial_capital=Decimal(str(args.capital)),
            target_trades=args.target_trades,
            position_fraction=Decimal("0.05"),
            ml_min_probability=0.60,
            lessons_output=Path(f"models/lessons_{tf}.jsonl"),
        )
        engine = SelfPlayEngine(config)
        report = await engine.run(history=tf_history, append=False)
        LOGGER.info(
            "%s: trades=%d wins=%d losses=%d pnl=%.2f dd=%.2f%%",
            tf, report.total_trades, report.wins, report.losses,
            report.total_pnl, report.max_drawdown_pct,
        )
        augment_lessons_with_news(config.lessons_output, news)

    # Объединяем все исторические уроки.
    merged = Path("models/lessons.jsonl")
    with merged.open("w", encoding="utf-8") as out:
        for tf in ("1h", "4h", "1d"):
            path = Path(f"models/lessons_{tf}.jsonl")
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    row["timeframe"] = tf
                    row["training_phase"] = "five_year_walk_forward"
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")

    result = train_weekly(
        lessons_path=merged,
        model_path=Path("models/current.pkl"),
        min_samples=args.min_samples,
    )
    LOGGER.info("PRETRAIN RESULT: %s", result.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
