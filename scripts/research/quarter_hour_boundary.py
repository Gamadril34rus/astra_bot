#!/usr/bin/env python3
"""BingX 15-minute-boundary event study. Research only; never places orders."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp
import numpy as np
import pandas as pd

API = "https://open-api.bingx.com"
KLINE_PATH = "/openApi/swap/v3/quote/klines"
FUNDING_PATH = "/openApi/swap/v2/quote/fundingRate"
SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
INTERVAL_MS = 300_000
BOUNDARY_MS = 900_000
PAGE_LIMIT = 500
IMPULSE_RETURN_MIN = 0.003
IMPULSE_RETURN_MAX = 0.015
VOLUME_MULTIPLE = 1.5
HORIZONS = {"30m": 6, "1h": 12, "2h": 24, "4h": 48}
FEE_BPS_PER_SIDE = 4.0
SLIPPAGE_BPS_BY_SYMBOL = {
    "BTC-USDT": 2.0,
    "ETH-USDT": 2.0,
    "SOL-USDT": 5.0,
    "HYPE-USDT": 5.0,
    "INJ-USDT": 5.0,
}
ROUND_TRIP_COST_BPS = {
    symbol: 2 * (FEE_BPS_PER_SIDE + slippage)
    for symbol, slippage in SLIPPAGE_BPS_BY_SYMBOL.items()
}
STRESS_ROUND_TRIP_COST_BPS = {
    symbol: cost * 1.5 for symbol, cost in ROUND_TRIP_COST_BPS.items()
}
SPLITS = (
    ("IS", "2025-07-09", "2025-12-31"),
    ("VALIDATION", "2026-01-01", "2026-05-31"),
    ("OOS", "2026-06-01", "2026-11-10"),
)
LOG = logging.getLogger("quarter_hour_boundary")


def validate_config(path: Path) -> None:
    """Fail closed if the checked-in frozen protocol drifts from the runner."""
    config = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "exchange": "BingX",
        "market": "USDT-M perpetual",
        "symbols": list(SYMBOLS),
        "signal_timeframe": "5m",
        "event_boundaries_utc_minutes": [0, 15, 30, 45],
        "impulse_return_abs_min": IMPULSE_RETURN_MIN,
        "impulse_return_abs_max": IMPULSE_RETURN_MAX,
        "volume_multiple_vs_prior_20_bar_median": VOLUME_MULTIPLE,
        "horizons_bars": HORIZONS,
        "splits_utc_inclusive": {
            name: [start, end] for name, start, end in SPLITS
        },
        "bootstrap": {
            "method": "UTC-day cluster bootstrap",
            "replicates": 2000,
            "seed": 20261111,
        },
    }
    for key, value in expected.items():
        if config.get(key) != value:
            raise ValueError(
                f"frozen research config mismatch for {key}: "
                f"expected {value!r}, got {config.get(key)!r}"
            )
    costs = config.get("costs_bps_per_side", {})
    if costs != {
        "taker_fee": FEE_BPS_PER_SIDE,
        "slippage_proxy_by_symbol": SLIPPAGE_BPS_BY_SYMBOL,
        "stress_multiplier": 1.5,
    }:
        raise ValueError(f"frozen research cost config mismatch: {costs!r}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows_from_kline(data: list[Any], symbol: str) -> list[dict[str, Any]]:
    rows = []
    for item in data:
        if isinstance(item, dict):
            get = item.get
            open_ms = int(get("time", get("openTime", get("open_time", 0))))
            row = {
                "open_time": open_ms,
                "open": get("open"),
                "high": get("high"),
                "low": get("low"),
                "close": get("close"),
                "volume": get("volume"),
                "quote_volume": get("quoteVolume", get("quote_volume")),
                "trades_count": get("count", get("trades", get("tradesCount", 0))),
                "taker_buy_volume": get("takerBuyVol", get("taker_buy_volume")),
                "taker_buy_quote_volume": get(
                    "takerBuyQuoteVol", get("taker_buy_quote_volume")
                ),
            }
        else:
            if len(item) < 10:
                raise ValueError(f"{symbol}: malformed kline row: {item!r}")
            row = {
                "open_time": int(item[0]),
                "open": item[1],
                "high": item[2],
                "low": item[3],
                "close": item[4],
                "volume": item[5],
                "quote_volume": item[7],
                "trades_count": item[8],
                "taker_buy_volume": item[9],
                "taker_buy_quote_volume": item[10] if len(item) > 10 else None,
            }
        row["symbol"] = symbol
        rows.append(row)
    return rows


class BingXPublicDownloader:
    """Conservative one-request-per-second public endpoint client."""

    def __init__(self, qps: float = 0.8) -> None:
        self.interval = 1.0 / qps
        self.last_request = 0.0
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> BingXPublicDownloader:
        timeout = aiohttp.ClientTimeout(total=45)
        self.session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self.session is not None:
            await self.session.close()

    async def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.session is None:
            raise RuntimeError("downloader is not initialized")
        delay = self.interval - (time.monotonic() - self.last_request)
        if delay > 0:
            await asyncio.sleep(delay)
        self.last_request = time.monotonic()
        url = API + path
        for attempt in range(4):
            async with self.session.get(url, params=params) as response:
                response.raise_for_status()
                payload = await response.json()
            if str(payload.get("code", "0")) == "0":
                return payload
            code = str(payload.get("code"))
            if code == "100410" and attempt < 3:
                wait = (300, 300, 300)[attempt]
                LOG.warning("BingX 100410; cooling down %ss before retry", wait)
                await asyncio.sleep(wait)
                continue
            raise RuntimeError(f"BingX {path} code={code}: {payload.get('msg')}")
        raise RuntimeError(f"BingX {path}: retry budget exhausted")

    async def candles(
        self, symbol: str, start_ms: int, end_ms: int
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor = ((start_ms + INTERVAL_MS - 1) // INTERVAL_MS) * INTERVAL_MS
        last_closed = (end_ms // INTERVAL_MS) * INTERVAL_MS - INTERVAL_MS
        while cursor <= last_closed:
            page_end = min(
                cursor + (PAGE_LIMIT - 1) * INTERVAL_MS,
                last_closed,
            )
            payload = await self.get(
                KLINE_PATH,
                {
                    "symbol": symbol,
                    "interval": "5m",
                    "startTime": cursor,
                    "endTime": page_end + INTERVAL_MS - 1,
                    "limit": PAGE_LIMIT,
                },
            )
            data = payload.get("data") or []
            if isinstance(data, dict):
                data = data.get("klines", data.get("data", [])) or []
            page = _rows_from_kline(data, symbol)
            expected = list(range(cursor, page_end + 1, INTERVAL_MS))
            actual = sorted(int(row["open_time"]) for row in page)
            if actual != expected:
                missing = sorted(set(expected) - set(actual))
                raise RuntimeError(
                    f"{symbol} 5m coverage mismatch: expected={len(expected)} "
                    f"actual={len(actual)} missing={missing[:5]}"
                )
            rows.extend(page)
            cursor = page_end + INTERVAL_MS
        return rows

    async def funding(
        self, symbol: str, start_ms: int, end_ms: int
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        # 90-day windows stay below the 1,000-settlement response cap.
        window_ms = 30 * 24 * 60 * 60 * 1000
        cursor = start_ms
        while cursor <= end_ms:
            window_end = min(cursor + window_ms - 1, end_ms)
            payload = await self.get(
                FUNDING_PATH,
                {
                    "symbol": symbol,
                    "startTime": cursor,
                    "endTime": window_end,
                    "limit": 1_000,
                },
            )
            data = payload.get("data") or []
            if isinstance(data, dict):
                data = data.get("fundingRates", data.get("data", [])) or []
            for item in data:
                stamp = int(item.get("fundingTime", item.get("time", 0)) or 0)
                if stamp and cursor <= stamp <= window_end:
                    rows.append(
                        {
                            "symbol": symbol,
                            "funding_time_ms": stamp,
                            "funding_rate": float(item["fundingRate"]),
                        }
                    )
            cursor = window_end + 1
        unique = {
            (row["symbol"], row["funding_time_ms"]): row for row in rows
        }
        return sorted(unique.values(), key=lambda row: row["funding_time_ms"])


async def fetch_dataset(
    reference_manifest: Path, output_dir: Path, end_utc: str
) -> dict[str, Any]:
    manifest = json.loads(reference_manifest.read_text(encoding="utf-8"))
    archive_meta = manifest["archives"]
    requested_end = pd.Timestamp(end_utc)
    if requested_end.tzinfo is None:
        requested_end = requested_end.tz_localize("UTC")
    else:
        requested_end = requested_end.tz_convert("UTC")
    end_ms = min(
        int(requested_end.timestamp() * 1000),
        int(datetime.now(UTC).timestamp() * 1000),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"exchange": "BingX", "timeframe": "5m"}
    async with BingXPublicDownloader() as client:
        for symbol in SYMBOLS:
            meta = archive_meta[f"{symbol}:30m"]
            start_ms = int(meta["first_open_time"])
            candles = await client.candles(symbol, start_ms, end_ms)
            if not candles:
                raise RuntimeError(f"{symbol}: BingX returned no 5m candles")
            funding = await client.funding(symbol, start_ms, end_ms)
            candle_path = output_dir / f"{symbol.replace('-', '')}_5m.csv"
            funding_path = output_dir / f"{symbol.replace('-', '')}_funding.csv"
            pd.DataFrame(candles).to_csv(candle_path, index=False)
            pd.DataFrame(
                funding,
                columns=["symbol", "funding_time_ms", "funding_rate"],
            ).to_csv(funding_path, index=False)
            candle_times = sorted(int(row["open_time"]) for row in candles)
            gap_count = sum(
                1
                for index in range(1, len(candle_times))
                if candle_times[index] - candle_times[index - 1] != INTERVAL_MS
            )
            result[symbol] = {
                "first_open_time": candle_times[0],
                "last_open_time": candle_times[-1],
                "bars": len(candles),
                "gaps": gap_count,
                "funding_rows": len(funding),
                "funding_first_ms": min(
                    (row["funding_time_ms"] for row in funding), default=None
                ),
                "funding_last_ms": max(
                    (row["funding_time_ms"] for row in funding), default=None
                ),
                "candles_sha256": _sha256(candle_path),
                "funding_sha256": _sha256(funding_path),
            }
    result["generated_at_utc"] = datetime.now(UTC).isoformat()
    (output_dir / "download_manifest.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return result


def _load_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if frame.empty:
        raise ValueError(f"empty dataset: {path}")
    frame["open_time"] = pd.to_numeric(frame["open_time"], errors="raise").astype("int64")
    if frame["open_time"].duplicated().any():
        raise ValueError(f"duplicate open_time in {path}")
    frame = frame.sort_values("open_time").reset_index(drop=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


def _four_hour_trend(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["time"] = pd.to_datetime(work["open_time"], unit="ms", utc=True)
    work = work.set_index("time")
    bars = work.resample("4h", origin="epoch", label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        count=("close", "count"),
    )
    bars = bars.loc[bars["count"] == 48].copy()
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
    return bars[["available_at", "trend", "ema20", "ema50"]].reset_index(drop=True)


def build_events(frame: pd.DataFrame) -> pd.DataFrame:
    """Select fixed 15m-boundary impulse events with no future information."""
    data = frame.copy().sort_values("open_time").reset_index(drop=True)
    data["time"] = pd.to_datetime(data["open_time"], unit="ms", utc=True)
    data["bar_return"] = data["close"] / data["open"] - 1.0
    data["prior_volume_median"] = (
        data["volume"].shift(1).rolling(20, min_periods=20).median()
    )
    data["volume_ratio"] = data["volume"] / data["prior_volume_median"]
    data["decision_time"] = data["time"] + pd.Timedelta(minutes=5)
    trend = _four_hour_trend(data)
    data = pd.merge_asof(
        data.sort_values("decision_time"),
        trend.sort_values("available_at"),
        left_on="decision_time",
        right_on="available_at",
        direction="backward",
    )
    boundary = (data["open_time"] % BOUNDARY_MS) == 0
    magnitude = data["bar_return"].abs()
    selected = (
        boundary
        & (magnitude >= IMPULSE_RETURN_MIN)
        & (magnitude <= IMPULSE_RETURN_MAX)
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


def _funding_for_window(
    funding: pd.DataFrame, entry_ms: int, exit_ms: int
) -> float:
    selected = funding.loc[
        (funding["funding_time_ms"] > entry_ms)
        & (funding["funding_time_ms"] <= exit_ms),
        "funding_rate",
    ]
    return float(selected.sum())


def event_outcomes(
    frame: pd.DataFrame, events: pd.DataFrame, funding: pd.DataFrame
) -> pd.DataFrame:
    """Calculate fixed-horizon candle-path returns, not executable fills."""
    ordered = frame.sort_values("open_time").reset_index(drop=True)
    index_by_time = {int(value): index for index, value in enumerate(ordered["open_time"])}
    rows: list[dict[str, Any]] = []
    funding = funding.sort_values("funding_time_ms")
    funding_start = int(funding["funding_time_ms"].min()) if not funding.empty else None
    funding_end = int(funding["funding_time_ms"].max()) if not funding.empty else None
    funding_gap = False
    if len(funding) > 2:
        intervals = funding["funding_time_ms"].diff().dropna()
        typical_interval = float(intervals.median())
        funding_gap = bool((intervals > typical_interval * 1.5).any())

    for event in events.itertuples(index=False):
        event_idx = index_by_time[int(event.open_time)]
        entry_idx = event_idx + 1
        if entry_idx >= len(ordered):
            continue
        entry = ordered.iloc[entry_idx]
        if int(entry["open_time"]) != int(event.open_time) + INTERVAL_MS:
            continue
        entry_price = float(entry["open"])
        entry_ms = int(entry["open_time"])
        for horizon, bars in HORIZONS.items():
            exit_idx = entry_idx + bars - 1
            if exit_idx >= len(ordered):
                continue
            exit_row = ordered.iloc[exit_idx]
            if int(exit_row["open_time"]) != entry_ms + (bars - 1) * INTERVAL_MS:
                continue
            exit_ms = int(exit_row["open_time"]) + INTERVAL_MS
            side = int(event.side)
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
            round_trip_cost = ROUND_TRIP_COST_BPS[event.symbol]
            stress_round_trip_cost = STRESS_ROUND_TRIP_COST_BPS[event.symbol]
            rows.append(
                {
                    "symbol": event.symbol,
                    "decision_time": event.decision_time,
                    "exit_time": pd.to_datetime(exit_ms, unit="ms", utc=True),
                    "variant": event.variant,
                    "horizon": horizon,
                    "side": side,
                    "gross_bps": gross_bps,
                    "cost_only_net_bps": gross_bps - round_trip_cost,
                    "stress_net_bps": gross_bps - stress_round_trip_cost,
                    "funding_bps": funding_bps,
                    "funded_net_bps": (
                        gross_bps - round_trip_cost + funding_bps
                        if funding_complete
                        else np.nan
                    ),
                    "funded_stress_net_bps": (
                        gross_bps - stress_round_trip_cost + funding_bps
                        if funding_complete
                        else np.nan
                    ),
                    "funding_complete": funding_complete,
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "symbol", "decision_time", "exit_time", "variant", "horizon", "side",
            "gross_bps", "cost_only_net_bps", "stress_net_bps", "funding_bps",
            "funded_net_bps", "funded_stress_net_bps", "funding_complete",
        ],
    )


def _split_for_interval(
    decision_time: pd.Timestamp, exit_time: pd.Timestamp
) -> str | None:
    """Assign an event only if its complete outcome stays inside one split."""
    for name, start, end in SPLITS:
        period_start = pd.Timestamp(start, tz="UTC")
        period_end = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
        if period_start <= decision_time < period_end and exit_time <= period_end:
            return name
    return None


def _metrics(frame: pd.DataFrame) -> dict[str, Any]:
    values = frame["cost_only_net_bps"].dropna().to_numpy(dtype=float)
    funded = frame["funded_net_bps"].dropna().to_numpy(dtype=float)
    funded_stress = frame["funded_stress_net_bps"].dropna().to_numpy(dtype=float)
    stress = frame["stress_net_bps"].dropna().to_numpy(dtype=float)
    if len(values) == 0:
        return {"n": 0}
    wins = values[values > 0]
    losses = values[values < 0]
    return {
        "n": len(values),
        "n_funding_complete": len(funded),
        "mean_cost_only_bps": float(np.mean(values)),
        "median_cost_only_bps": float(np.median(values)),
        "hit_rate_cost_only": float(np.mean(values > 0)),
        "pf_cost_only": (
            float(wins.sum() / abs(losses.sum())) if len(losses) else None
        ),
        "mean_stress_net_bps": float(np.mean(stress)) if len(stress) else None,
        "mean_funded_bps": float(np.mean(funded)) if len(funded) else None,
        "median_funded_bps": float(np.median(funded)) if len(funded) else None,
        "mean_funded_stress_bps": (
            float(np.mean(funded_stress)) if len(funded_stress) else None
        ),
    }


def run_study(data_dir: Path, report_path: Path) -> dict[str, Any]:
    all_outcomes = []
    coverage: dict[str, Any] = {}
    for symbol in SYMBOLS:
        stem = symbol.replace("-", "")
        candles_path = data_dir / f"{stem}_5m.csv"
        funding_path = data_dir / f"{stem}_funding.csv"
        candles = _load_csv(candles_path)
        funding = pd.read_csv(funding_path)
        if funding.empty or not {"funding_time_ms", "funding_rate"} <= set(funding):
            funding = pd.DataFrame(columns=["funding_time_ms", "funding_rate"])
        else:
            funding["funding_time_ms"] = pd.to_numeric(
                funding["funding_time_ms"], errors="raise"
            ).astype("int64")
            funding["funding_rate"] = pd.to_numeric(
                funding["funding_rate"], errors="raise"
            )
        candles["symbol"] = symbol
        events = build_events(candles)
        outcomes = event_outcomes(candles, events, funding)
        all_outcomes.append(outcomes)
        coverage[symbol] = {
            "bars_5m": len(candles),
            "first_utc": pd.to_datetime(
                candles["open_time"].min(), unit="ms", utc=True
            ).isoformat(),
            "last_utc": pd.to_datetime(
                candles["open_time"].max(), unit="ms", utc=True
            ).isoformat(),
            "gap_count": int(
                (candles["open_time"].diff().dropna() != INTERVAL_MS).sum()
            ),
            "taker_buy_rows": int(
                pd.to_numeric(candles.get("taker_buy_volume"), errors="coerce")
                .notna()
                .sum()
            )
            if "taker_buy_volume" in candles
            else 0,
            "events": len(events),
        }

    outcomes = pd.concat(all_outcomes, ignore_index=True)
    outcomes["decision_time"] = pd.to_datetime(outcomes["decision_time"], utc=True)
    outcomes["exit_time"] = pd.to_datetime(outcomes["exit_time"], utc=True)
    outcomes["split"] = outcomes.apply(
        lambda row: _split_for_interval(row["decision_time"], row["exit_time"]),
        axis=1,
    )
    outcomes = outcomes.loc[outcomes["split"].notna()].copy()
    grouped = []
    keys = ["split", "variant", "symbol", "horizon"]
    for key, group in outcomes.groupby(keys, dropna=False):
        item = dict(zip(keys, key, strict=True))
        item.update(_metrics(group))
        grouped.append(item)

    pooled = []
    for key, group in outcomes.groupby(["split", "variant", "horizon"]):
        item = dict(zip(["split", "variant", "horizon"], key, strict=True))
        item.update(_metrics(group))
        # Daily cluster bootstrap: resample days, not overlapping trades.
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
        item["positive_symbols"] = int(
            sum(
                _metrics(part).get("mean_cost_only_bps", -1) > 0
                for _, part in group.groupby("symbol")
            )
        )
        pooled.append(item)

    result = {
        "study": "quarter-hour boundary event study",
        "status": "EVENT_STUDY_ONLY_NOT_A_TRADING_STRATEGY",
        "signal": (
            "First closed 5m candle at each UTC 00/15/30/45 boundary; "
            "absolute return 0.30%-1.50%; volume >= 1.5x prior 20-bar median; "
            "completed 4h EMA20/EMA50 trend filter"
        ),
        "variants": {
            "REVERSAL": "fade impulse only when completed 4h trend opposes it",
            "CONTINUATION": "follow impulse only when completed 4h trend confirms it",
        },
        "entry_proxy": "open of next 5m candle; no BBO/size history, not an executable fill",
        "costs": {
            "fee_bps_per_side": FEE_BPS_PER_SIDE,
            "slippage_bps_per_side_by_symbol": SLIPPAGE_BPS_BY_SYMBOL,
            "round_trip_bps_by_symbol": ROUND_TRIP_COST_BPS,
            "round_trip_stress_1_5x_bps_by_symbol": STRESS_ROUND_TRIP_COST_BPS,
        },
        "funding": "actual archived settlements when coverage brackets the whole event; otherwise UNKNOWN",
        "horizons": HORIZONS,
        "splits": [
            {"name": name, "start": start, "end": end} for name, start, end in SPLITS
        ],
        "coverage": coverage,
        "by_symbol": grouped,
        "pooled": pooled,
        "limitations": [
            "5m candle data cannot reproduce the paper's first-10-second signed trade flow.",
            "No historical BingX BBO/depth tape: costs are fixed proxies, not verified fills.",
            "This is a fixed-horizon event study, not a stop/TP strategy backtest.",
            "Overlapping events are dependent; confidence intervals resample UTC days.",
            "No result authorizes production, VST arming, or real orders.",
        ],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument(
        "--config", type=Path, default=Path("config/research/quarter_hour_boundary.json")
    )
    parser.add_argument("--reference-manifest", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path("reports/quarter_hour_boundary/data"))
    parser.add_argument("--report", type=Path, default=Path("reports/quarter_hour_boundary/report.json"))
    parser.add_argument("--end-utc", default="2026-11-12T00:00:00Z")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    validate_config(args.config)
    if args.fetch:
        if args.reference_manifest is None:
            parser.error("--reference-manifest is required with --fetch")
        manifest = asyncio.run(
            fetch_dataset(args.reference_manifest, args.data_dir, args.end_utc)
        )
        LOG.info("download complete: %s", json.dumps(manifest, sort_keys=True))
    result = run_study(args.data_dir, args.report)
    LOG.info("study complete: %s", json.dumps(result["pooled"], sort_keys=True))


if __name__ == "__main__":
    main()
