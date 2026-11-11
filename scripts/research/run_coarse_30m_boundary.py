#!/usr/bin/env python3
"""Coarse 30m boundary proxy study; not a quarter-hour microstructure test."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.research.quarter_hour_boundary import (
    BingXPublicDownloader,
    FEE_BPS_PER_SIDE,
    ROUND_TRIP_COST_BPS,
    STRESS_ROUND_TRIP_COST_BPS,
    _funding_for_window,
    _metrics,
    _split_for_interval,
)

SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
BAR_MS = 30 * 60 * 1000
IMPULSE_MIN = 0.003
IMPULSE_MAX = 0.015
VOLUME_MULTIPLE = 1.5
HORIZONS = {"30m": 1, "1h": 2, "2h": 4, "4h": 8}
SPLIT_END_MS = int(pd.Timestamp("2026-11-11T00:00:00Z").timestamp() * 1000)
LOG = logging.getLogger("quarter_hour_boundary_coarse")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_30m(path: Path, expected: dict[str, Any]) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if frame.empty:
        raise ValueError(f"empty 30m archive: {path}")
    frame["open_time"] = pd.to_numeric(frame["open_time"], errors="raise").astype("int64")
    if frame["open_time"].duplicated().any():
        raise ValueError(f"duplicate 30m open_time: {path}")
    frame = frame.sort_values("open_time").reset_index(drop=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    gaps = int((frame["open_time"].diff().dropna() != BAR_MS).sum())
    checks = {
        "first_open_time": int(frame["open_time"].iloc[0]),
        "last_open_time": int(frame["open_time"].iloc[-1]),
        "bars": len(frame),
        "gaps": gaps,
    }
    for key in ("first_open_time", "last_open_time", "bars", "gaps"):
        expected_value = expected.get(key)
        if expected_value is not None and checks[key] != int(expected_value):
            raise ValueError(
                f"{path.name} manifest mismatch {key}: "
                f"csv={checks[key]} manifest={expected_value}"
            )
    if gaps:
        raise ValueError(f"{path.name} has {gaps} missing 30m intervals")
    return frame


def _four_hour_trend(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["time"] = pd.to_datetime(work["open_time"], unit="ms", utc=True)
    bars = work.set_index("time").resample(
        "4h", origin="epoch", label="left", closed="left"
    ).agg(close=("close", "last"), count=("close", "count"))
    bars = bars.loc[bars["count"] == 8].copy()
    bars["ema20"] = bars["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    bars["ema50"] = bars["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    bars["trend"] = np.select(
        [
            (bars["ema20"] > bars["ema50"]) & (bars["close"] > bars["ema20"]),
            (bars["ema20"] < bars["ema50"]) & (bars["close"] < bars["ema20"]),
        ],
        [1, -1],
        default=0,
    )
    bars["available_at"] = bars.index + pd.Timedelta(hours=4)
    return bars[["available_at", "trend"]].reset_index(drop=True)


def build_events(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy().sort_values("open_time").reset_index(drop=True)
    data["time"] = pd.to_datetime(data["open_time"], unit="ms", utc=True)
    data["bar_return"] = data["close"] / data["open"] - 1.0
    data["prior_volume_median"] = (
        data["volume"].shift(1).rolling(20, min_periods=20).median()
    )
    data["volume_ratio"] = data["volume"] / data["prior_volume_median"]
    data["decision_time"] = data["time"] + pd.Timedelta(minutes=30)
    trend = _four_hour_trend(data)
    data = pd.merge_asof(
        data.sort_values("decision_time"),
        trend.sort_values("available_at"),
        left_on="decision_time",
        right_on="available_at",
        direction="backward",
    )
    boundary = data["time"].dt.minute.isin((0, 30))
    magnitude = data["bar_return"].abs()
    selected = (
        boundary
        & (magnitude >= IMPULSE_MIN)
        & (magnitude <= IMPULSE_MAX)
        & (data["volume_ratio"] >= VOLUME_MULTIPLE)
        & data["trend"].isin((-1, 1))
    )
    events = data.loc[selected].copy()
    events["impulse"] = np.sign(events["bar_return"]).astype(int)
    events["variant"] = np.where(
        events["trend"] == -events["impulse"], "REVERSAL", "CONTINUATION"
    )
    events["side"] = np.where(
        events["variant"] == "REVERSAL", -events["impulse"], events["impulse"]
    )
    return events.reset_index(drop=True)


def event_outcomes(
    frame: pd.DataFrame, events: pd.DataFrame, funding: pd.DataFrame
) -> pd.DataFrame:
    ordered = frame.sort_values("open_time").reset_index(drop=True)
    index_by_time = {
        int(value): index for index, value in enumerate(ordered["open_time"])
    }
    funding = funding.sort_values("funding_time_ms")
    funding_start = (
        int(funding["funding_time_ms"].min()) if not funding.empty else None
    )
    funding_end = (
        int(funding["funding_time_ms"].max()) if not funding.empty else None
    )
    funding_gap = False
    if len(funding) > 2:
        intervals = funding["funding_time_ms"].diff().dropna()
        funding_gap = bool((intervals > float(intervals.median()) * 1.5).any())

    rows: list[dict[str, Any]] = []
    for event in events.itertuples(index=False):
        event_idx = index_by_time[int(event.open_time)]
        entry_idx = event_idx + 1
        if entry_idx >= len(ordered):
            continue
        entry = ordered.iloc[entry_idx]
        if int(entry["open_time"]) != int(event.open_time) + BAR_MS:
            continue
        entry_ms = int(entry["open_time"])
        entry_price = float(entry["open"])
        side = int(event.side)
        symbol = str(event.symbol)
        for horizon, bars in HORIZONS.items():
            exit_idx = entry_idx + bars - 1
            if exit_idx >= len(ordered):
                continue
            exit_row = ordered.iloc[exit_idx]
            if int(exit_row["open_time"]) != entry_ms + (bars - 1) * BAR_MS:
                continue
            exit_ms = int(exit_row["open_time"]) + BAR_MS
            gross_bps = side * (float(exit_row["close"]) / entry_price - 1.0) * 10_000
            funding_complete = (
                funding_start is not None
                and funding_end is not None
                and funding_start <= entry_ms
                and funding_end >= exit_ms
                and not funding_gap
            )
            funding_bps = (
                -side * _funding_for_window(funding, entry_ms, exit_ms) * 10_000
                if funding_complete
                else np.nan
            )
            base_cost = ROUND_TRIP_COST_BPS[symbol]
            stress_cost = STRESS_ROUND_TRIP_COST_BPS[symbol]
            rows.append(
                {
                    "symbol": symbol,
                    "decision_time": event.decision_time,
                    "exit_time": pd.to_datetime(exit_ms, unit="ms", utc=True),
                    "variant": event.variant,
                    "horizon": horizon,
                    "gross_bps": gross_bps,
                    "cost_only_net_bps": gross_bps - base_cost,
                    "stress_net_bps": gross_bps - stress_cost,
                    "funded_net_bps": (
                        gross_bps - base_cost + funding_bps
                        if funding_complete
                        else np.nan
                    ),
                    "funded_stress_net_bps": (
                        gross_bps - stress_cost + funding_bps
                        if funding_complete
                        else np.nan
                    ),
                    "funding_complete": funding_complete,
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "symbol", "decision_time", "exit_time", "variant", "horizon",
            "gross_bps", "cost_only_net_bps", "stress_net_bps",
            "funded_net_bps", "funded_stress_net_bps", "funding_complete",
        ],
    )


async def _download_funding(
    manifest: dict[str, Any], output_dir: Path, end_ms: int
) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    async with BingXPublicDownloader() as client:
        for symbol in SYMBOLS:
            start_ms = int(manifest["archives"][f"{symbol}:30m"]["first_open_time"])
            rows = await client.funding(symbol, start_ms, end_ms)
            funding = pd.DataFrame(
                rows, columns=["symbol", "funding_time_ms", "funding_rate"]
            )
            path = output_dir / f"{symbol.replace('-', '')}_funding.csv"
            funding.to_csv(path, index=False)
            result[symbol] = funding
    return result


def run_study(
    data_dir: Path,
    manifest_path: Path,
    output_dir: Path,
    report_path: Path,
    end_utc: str,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive_meta = manifest["archives"]
    output_dir.mkdir(parents=True, exist_ok=True)
    end = pd.Timestamp(end_utc)
    if end.tzinfo is None:
        end = end.tz_localize("UTC")
    else:
        end = end.tz_convert("UTC")
    end_ms = int(min(end.timestamp() * 1000, datetime.now(UTC).timestamp() * 1000))
    funding_by_symbol = asyncio.run(_download_funding(manifest, output_dir, end_ms))

    outcomes_all = []
    coverage: dict[str, Any] = {}
    for symbol in SYMBOLS:
        stem = symbol.replace("-", "")
        path = data_dir / f"{stem}_30m.csv"
        meta = archive_meta[f"{symbol}:30m"]
        frame = _load_30m(path, meta)
        frame["symbol"] = symbol
        events = build_events(frame)
        outcomes = event_outcomes(frame, events, funding_by_symbol[symbol])
        outcomes_all.append(outcomes)
        taker = (
            pd.to_numeric(frame["taker_buy_volume"], errors="coerce")
            if "taker_buy_volume" in frame
            else pd.Series(dtype=float)
        )
        coverage[symbol] = {
            "bars_30m": len(frame),
            "first_utc": pd.to_datetime(
                frame["open_time"].min(), unit="ms", utc=True
            ).isoformat(),
            "last_utc": pd.to_datetime(
                frame["open_time"].max(), unit="ms", utc=True
            ).isoformat(),
            "gap_count": int((frame["open_time"].diff().dropna() != BAR_MS).sum()),
            "taker_buy_rows": int(taker.notna().sum()),
            "funding_rows": len(funding_by_symbol[symbol]),
            "events": len(events),
            "csv_sha256": _sha256(path),
        }

    outcomes = pd.concat(outcomes_all, ignore_index=True)
    outcomes["decision_time"] = pd.to_datetime(outcomes["decision_time"], utc=True)
    outcomes["exit_time"] = pd.to_datetime(outcomes["exit_time"], utc=True)
    outcomes["split"] = outcomes.apply(
        lambda row: _split_for_interval(row["decision_time"], row["exit_time"]),
        axis=1,
    )
    outcomes = outcomes.loc[outcomes["split"].notna()].copy()
    by_symbol = []
    keys = ["split", "variant", "symbol", "horizon"]
    for key, group in outcomes.groupby(keys, dropna=False):
        item = dict(zip(keys, key, strict=True))
        item.update(_metrics(group))
        by_symbol.append(item)

    pooled = []
    for key, group in outcomes.groupby(["split", "variant", "horizon"]):
        item = dict(
            zip(["split", "variant", "horizon"], key, strict=True)
        )
        item.update(_metrics(group))
        daily = group.assign(day=group["decision_time"].dt.date).groupby("day")[
            "cost_only_net_bps"
        ].mean().to_numpy(dtype=float)
        if len(daily) >= 2:
            rng = np.random.default_rng(20261111)
            samples = rng.choice(daily, size=(2_000, len(daily)), replace=True).mean(axis=1)
            item["daily_cluster_ci95_bps"] = [
                float(np.quantile(samples, 0.025)),
                float(np.quantile(samples, 0.975)),
            ]
        else:
            item["daily_cluster_ci95_bps"] = None
        item["positive_symbols"] = sum(
            _metrics(part).get("mean_cost_only_bps", -1) > 0
            for _, part in group.groupby("symbol")
        )
        pooled.append(item)

    result = {
        "study": "coarse 30m half-hour boundary proxy",
        "status": "COARSE_PROXY_ONLY_NOT_THE_15M_OR_5M_HYPOTHESIS",
        "signal": (
            "Every completed 30m candle opening at UTC :00 or :30; "
            "absolute return 0.30%-1.50%; volume >= 1.5x prior 20-bar median; "
            "last fully closed 4h EMA20/EMA50 trend"
        ),
        "variants": {
            "REVERSAL": "fade impulse only when completed 4h trend opposes it",
            "CONTINUATION": "follow impulse only when completed 4h trend confirms it",
        },
        "entry_proxy": "open of next 30m candle",
        "horizons": HORIZONS,
        "costs": {
            "fee_bps_per_side": FEE_BPS_PER_SIDE,
            "round_trip_bps_by_symbol": ROUND_TRIP_COST_BPS,
            "round_trip_stress_1_5x_bps_by_symbol": STRESS_ROUND_TRIP_COST_BPS,
        },
        "splits": [
            {"name": "IS", "start": "2025-07-09", "end": "2025-12-31"},
            {"name": "VALIDATION", "start": "2026-01-01", "end": "2026-05-31"},
            {"name": "OOS", "start": "2026-06-01", "end": "2026-11-10"},
        ],
        "coverage": coverage,
        "by_symbol": by_symbol,
        "pooled": pooled,
        "limitations": [
            "30m bars observe only UTC :00 and :30, not :15 and :45 boundaries.",
            "A 30m return is not the first 5m/10s impulse from the research paper.",
            "The existing archive has no usable taker-buy history; price return is only a proxy.",
            "No historical BBO/depth: slippage is a fixed proxy, not a verified fill.",
            "This is a fixed-horizon event study, not a stop/TP trading-strategy backtest.",
            "Funding is UNKNOWN outside verified historical coverage.",
            "A positive coarse result does not validate the original quarter-hour hypothesis.",
        ],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("reports/quarter_hour_boundary/data"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/quarter_hour_boundary/data"))
    parser.add_argument("--report", type=Path, default=Path("reports/quarter_hour_boundary/coarse_30m_report.json"))
    parser.add_argument("--end-utc", default="2026-11-12T00:00:00Z")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_study(
        args.data_dir, args.manifest, args.output_dir, args.report, args.end_utc
    )
    LOG.info("coarse study complete: %s", json.dumps(result["pooled"], sort_keys=True))


if __name__ == "__main__":
    main()
