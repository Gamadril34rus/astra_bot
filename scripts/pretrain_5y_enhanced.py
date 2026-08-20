#!/usr/bin/env python3
"""Research-first pretrain executed as resumable calendar-month stages."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
from astra_bot.ml import self_play as sp
from astra_bot.ml.market_memory import MarketMemory
from astra_bot.ml.market_understanding import compute_market_features
from astra_bot.ml.news_features import NewsFeatureService
from astra_bot.ml.research_engine import research_history_v2
from astra_bot.ml.weekly_learner import train_weekly
from dateutil.relativedelta import relativedelta
from scripts import pretrain_5y

PROGRESS = Path("models/pretrain_progress.json")
TIMEFRAMES = (("1h", 1), ("4h", 4), ("1d", 24))
WARMUP_DAYS = 300
FORWARD_DAYS = 90


def install_enhanced_self_play() -> None:
    original_snapshot = sp._feature_snapshot
    news_service = NewsFeatureService(Path("models/news_cache.json"))

    def enhanced_snapshot(strategy, candles, cross_snapshot=None):
        base = original_snapshot(strategy, candles, cross_snapshot)
        enhanced = compute_market_features(
            candles[-260:],
            timeframe=getattr(candles[-1], "timeframe", "1h"),
            extra_features=base,
        )
        enhanced.update(
            news_service.cached_historical(candles[-1].symbol, candles[-1].open_time).to_features()
        )
        return enhanced

    sp._feature_snapshot = enhanced_snapshot

    def dynamic_ml_approves(self, features):
        if self._ml_model is None:
            return True
        try:
            names = list(getattr(self._ml_model, "feature_names", []) or [])
            if not names:
                return True
            vector = np.asarray([[float(features.get(name, 0.0)) for name in names]], dtype=float)
            return (
                float(self._ml_model.predict_probability(vector)) >= self.config.ml_min_probability
            )
        except Exception:
            return True

    sp.SelfPlayEngine._ml_approves = dynamic_ml_approves


def _default_progress(years: int) -> dict:
    start = datetime.now(tz=UTC) - relativedelta(years=years)
    return {"years": years, "next_month": start.strftime("%Y-%m"), "completed_months": []}


def _month_has_complete_research(month: str) -> bool:
    """A completed month must contain observations for every configured TF."""
    for timeframe, _hours in TIMEFRAMES:
        path = Path(f"models/research_observations_{month}_{timeframe}.jsonl")
        if not path.exists() or path.stat().st_size == 0:
            return False
    return True


def load_progress(years: int) -> dict:
    state = _default_progress(years)
    if PROGRESS.exists():
        try:
            loaded = json.loads(PROGRESS.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded
        except (OSError, json.JSONDecodeError):
            pass

    # Older jobs truncated warm-up to 260 hourly bars. As a result they marked
    # months complete while producing empty 4h/1d artifacts. Rewind to the
    # first incomplete month so it is repaired automatically.
    completed = list(state.get("completed_months") or [])
    for index, month in enumerate(completed):
        if not _month_has_complete_research(month):
            state["completed_months"] = completed[:index]
            state["next_month"] = month
            save_progress(state)
            break
    return state


def save_progress(state: dict) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def month_bounds(value: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(value, "%Y-%m").replace(tzinfo=UTC)
    return start, start + relativedelta(months=1)


def merge_jsonl(pattern: str, target: Path) -> None:
    with target.open("w", encoding="utf-8") as out:
        for path in sorted(Path("models").glob(pattern)):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.write(line + "\n")


def _research_is_complete(stats: dict[str, dict[str, int]]) -> bool:
    return all(
        values.get("observations", 0) > 0 and values.get("validation_observations", 0) > 0
        for values in stats.values()
    )


async def main() -> int:
    pretrain_5y.setup_logging()
    parser = argparse.ArgumentParser(description="ASTRA monthly historical pretrain")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--target-trades", type=int, default=5000)
    parser.add_argument("--min-samples", type=int, default=2000)
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--with-news", action="store_true")
    args = parser.parse_args()

    install_enhanced_self_play()
    state = load_progress(args.years)
    month = state["next_month"]
    start, end = month_bounds(month)
    now = datetime.now(tz=UTC)
    if start >= now:
        print(f"PRETRAIN COMPLETE: {len(state['completed_months'])} months")
        return 0
    required_context_end = end + relativedelta(days=FORWARD_DAYS)
    if required_context_end > now:
        print(
            f"PRETRAIN WAITING: {month} needs forward labels through "
            f"{required_context_end.date()}",
            flush=True,
        )
        return 0

    is_partial = False
    target_end = end
    # Research needs history before the target month for indicators and data
    # after it for forward labels. Never use future context as an observation.
    context_end = min(end + relativedelta(days=FORWARD_DAYS), now)
    context_days = (context_end - (start - relativedelta(days=WARMUP_DAYS))).days + 1
    print(
        f"MONTH START {month}: {start.date()} -> {target_end.date()} | "
        f"warmup={WARMUP_DAYS}d forward={FORWARD_DAYS}d",
        flush=True,
    )
    history = await pretrain_5y.fetch_history(
        context_days, end_time_ms=int(context_end.timestamp() * 1000)
    )
    usable = {symbol: bars for symbol, bars in history.items() if len(bars) >= 240}
    if not usable:
        raise RuntimeError("Не удалось получить достаточную историю за месяц")

    start_ms = int(start.timestamp() * 1000)
    end_ms = int(target_end.timestamp() * 1000)
    context_start_ms = int((start - relativedelta(days=WARMUP_DAYS)).timestamp() * 1000)
    research_history = {
        symbol: [
            bar
            for bar in bars
            if context_start_ms <= bar.open_time < int(context_end.timestamp() * 1000)
        ]
        for symbol, bars in usable.items()
    }
    trade_history = {
        symbol: [bar for bar in bars if bar.open_time < end_ms]
        for symbol, bars in research_history.items()
    }

    news = NewsFeatureService(Path("models/news_cache.json"))
    if args.with_news:
        await pretrain_5y.build_monthly_news_cache(
            Path("models/news_cache.json"), args.years, start, target_end
        )
        news = NewsFeatureService(Path("models/news_cache.json"))

    summary: dict[str, dict[str, int]] = {}
    for timeframe, hours in TIMEFRAMES:
        tf_research = {
            symbol: bars if hours == 1 else pretrain_5y.resample_candles(bars, hours)
            for symbol, bars in research_history.items()
        }
        tf_trades = {
            symbol: bars if hours == 1 else pretrain_5y.resample_candles(bars, hours)
            for symbol, bars in trade_history.items()
        }
        observations = Path(f"models/research_observations_{month}_{timeframe}.jsonl")
        hypotheses = Path(f"models/research_hypotheses_{month}_{timeframe}.json")
        stats = research_history_v2(
            tf_research,
            output=observations,
            hypotheses_output=hypotheses,
            sample_every={"1h": 6, "4h": 1, "1d": 1},
            validation_fraction=0.30,
            min_samples=30,
            news_service=news,
            observation_start_ms=start_ms,
            observation_end_ms=end_ms,
        )
        summary[timeframe] = stats
        print(
            f"Research {month} {timeframe}: symbols={stats['symbols']} "
            f"observations={stats['observations']} events={stats['events']} "
            f"oos={stats['validation_observations']}",
            flush=True,
        )

        config = sp.SelfPlayConfig(
            timeframe=timeframe,
            symbols=tuple(tf_trades.keys()),
            initial_capital=Decimal(str(args.capital)),
            target_trades=args.target_trades,
            position_fraction=Decimal("0.05"),
            ml_min_probability=0.60,
            lessons_output=Path(f"models/lessons_{month}_{timeframe}.jsonl"),
        )
        report = await sp.SelfPlayEngine(config).run(history=tf_trades, append=False)
        print(
            f"Paper {month} {timeframe}: trades={report.total_trades} wins={report.wins} "
            f"losses={report.losses} pnl={report.total_pnl:.2f} "
            f"drawdown={report.max_drawdown_pct:.2f}%",
            flush=True,
        )

    if not _research_is_complete(summary):
        raise RuntimeError(f"Неполное исследование {month}: {summary}")

    merge_jsonl("research_observations_????-??_*.jsonl", Path("models/research_observations.jsonl"))
    merge_jsonl("lessons_????-??_*.jsonl", Path("models/lessons.jsonl"))
    memory = MarketMemory()
    research_count = memory.import_research(Path("models/research_observations.jsonl"))
    lesson_count = memory.build_from_lessons(Path("models/lessons.jsonl"))
    memory.save()
    result = train_weekly(
        lessons_path=Path("models/lessons.jsonl"),
        model_path=Path("models/current.pkl"),
        min_samples=args.min_samples,
    )
    Path("models/research_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    state["completed_months"].append(month)
    state["next_month"] = (start + relativedelta(months=1)).strftime("%Y-%m")
    save_progress(state)
    print(
        f"MONTH COMPLETE {month}: research={research_count} lessons={lesson_count}; "
        f"model={result.message}; next={state['next_month']}; partial={is_partial}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
