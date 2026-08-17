#!/usr/bin/env python3
"""Five-year ASTRA pretrain with research-first market understanding."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import numpy as np

from astra_bot.ml import self_play as sp
from astra_bot.ml.market_memory import MarketMemory
from astra_bot.ml.market_understanding import compute_market_features
from astra_bot.ml.news_features import NewsFeatureService
from astra_bot.ml.research_engine import research_history_v2
from scripts import pretrain_5y


def install_enhanced_self_play() -> None:
    original_snapshot = sp._feature_snapshot
    news_service = NewsFeatureService(Path("models/news_cache.json"))

    def enhanced_snapshot(strategy, candles, cross_snapshot=None):
        base = original_snapshot(strategy, candles, cross_snapshot)
        enhanced = compute_market_features(
            candles,
            timeframe=getattr(candles[-1], "timeframe", "1h"),
            extra_features=base,
        )
        symbol = candles[-1].symbol
        ts = candles[-1].open_time
        snap = news_service.cached_historical(symbol, ts)
        enhanced.update(snap.to_features())
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
            probability = float(self._ml_model.predict_probability(vector))
            return probability >= self.config.ml_min_probability
        except Exception:
            return True

    sp.SelfPlayEngine._ml_approves = dynamic_ml_approves


def install_news_cache_compat() -> None:
    if not hasattr(NewsFeatureService, "save_cache"):
        NewsFeatureService.save_cache = NewsFeatureService._save_cache


async def main() -> int:
    install_news_cache_compat()
    install_enhanced_self_play()

    parser = pretrain_5y.argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--target-trades", type=int, default=5000)
    parser.add_argument("--min-samples", type=int, default=2000)
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--with-news", action="store_true")
    args = parser.parse_args()

    pretrain_5y.setup_logging()
    days = args.years * 365
    history = await pretrain_5y.fetch_history(days)
    usable = {s: bars for s, bars in history.items() if len(bars) >= 240}
    if not usable:
        raise RuntimeError("Не удалось получить достаточную историю")

    if args.with_news:
        await pretrain_5y.build_monthly_news_cache(Path("models/news_cache.json"), args.years)

    news = NewsFeatureService(Path("models/news_cache.json"))
    research_summary = {}
    research_files: list[Path] = []

    # Research is the primary learning stage. It does not need trades and
    # separates discovery from a final out-of-sample period.
    for tf, hours in (("1h", 1), ("4h", 4), ("1d", 24)):
        tf_history = {
            symbol: (bars if hours == 1 else pretrain_5y.resample_candles(bars, hours))
            for symbol, bars in usable.items()
        }
        obs_path = Path(f"models/research_observations_{tf}.jsonl")
        hyp_path = Path(f"models/research_hypotheses_{tf}.json")
        stats = research_history_v2(
            tf_history,
            output=obs_path,
            hypotheses_output=hyp_path,
            sample_every={"1h": 6, "4h": 1, "1d": 1},
            validation_fraction=0.30,
            min_samples=30,
            news_service=news,
        )
        research_summary[tf] = stats
        research_files.append(obs_path)
        print(
            f"Research {tf}: symbols={stats['symbols']} "
            f"observations={stats['observations']} events={stats['events']} "
            f"oos={stats['validation_observations']}"
        )

        config = sp.SelfPlayConfig(
            timeframe=tf,
            symbols=tuple(tf_history.keys()),
            initial_capital=__import__("decimal").Decimal(str(args.capital)),
            target_trades=args.target_trades,
            position_fraction=__import__("decimal").Decimal("0.05"),
            ml_min_probability=0.60,
            lessons_output=Path(f"models/lessons_{tf}.jsonl"),
        )
        engine = sp.SelfPlayEngine(config)
        report = await engine.run(history=tf_history, append=False)
        print(
            f"{tf}: trades={report.total_trades} wins={report.wins} "
            f"losses={report.losses} pnl={report.total_pnl:.2f} "
            f"drawdown={report.max_drawdown_pct:.2f}%"
        )

        path = config.lessons_output
        if path.exists():
            rows = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                snap = news.cached_historical(row.get("symbol", ""), int(row.get("entry_time", 0)))
                row.setdefault("features", {}).update(snap.to_features())
                row["training_phase"] = "five_year_walk_forward"
                row["feature_engine"] = "market_understanding_v1"
                rows.append(row)
            path.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
                encoding="utf-8",
            )

    merged_research = Path("models/research_observations.jsonl")
    with merged_research.open("w", encoding="utf-8") as out:
        for path in research_files:
            if path.exists():
                out.write(path.read_text(encoding="utf-8"))

    Path("models/research_summary.json").write_text(
        json.dumps(research_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    merged = Path("models/lessons.jsonl")
    with merged.open("w", encoding="utf-8") as out:
        for tf in ("1h", "4h", "1d"):
            path = Path(f"models/lessons_{tf}.jsonl")
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                row["timeframe"] = tf
                out.write(json.dumps(row, ensure_ascii=False) + "\n")

    memory = MarketMemory()
    count = memory.build_from_lessons(merged)
    print(
        f"Market memory: {count} lessons aggregated into "
        f"{len(memory.data.get('patterns', {}))} patterns"
    )

    result = __import__("astra_bot.ml.weekly_learner", fromlist=["train_weekly"]).train_weekly(
        lessons_path=merged,
        model_path=Path("models/current.pkl"),
        min_samples=args.min_samples,
    )
    print(result.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
