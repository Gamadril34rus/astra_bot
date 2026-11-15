#!/usr/bin/env python3
"""IS/validation-only research for BingX 15m volume-impact regimes.

The 1m BingX USDT-M OHLCV archive is used only to construct 15m/1h bars.
Signals and entries are restricted to closed 15m candles with closed 1h context.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scripts import research_adaptive_perp_portfolio as core

SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
BASE_COSTS = {"fee_bps_per_side": 5.0, "spread_bps_per_side": 1.25, "slippage_bps_per_side": 2.0}
STRESS_MULTIPLIER = 1.5
MIN_SCREEN_TRADES_PER_PERIOD = 30
FROZEN_WINDOW_START_UTC = "2025-11-13T00:00:00+00:00"
VALIDATION_START_UTC = "2026-06-20T00:00:00+00:00"
VALIDATION_END_UTC = "2026-09-01T00:00:00+00:00"
FRESH_OOS_START_UTC = "2026-11-13T00:00:00+00:00"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ms(value: str) -> int:
    return int(pd.Timestamp(value).timestamp() * 1000)


def verify_inputs(data_dir: Path, funding_dir: Path) -> dict[str, Any]:
    candle_manifest_path = data_dir / "manifest.json"
    funding_manifest_path = funding_dir / "funding_manifest.json"
    candle = json.loads(candle_manifest_path.read_text(encoding="utf-8"))
    funding = json.loads(funding_manifest_path.read_text(encoding="utf-8"))
    if candle.get("exchange") != "BingX" or candle.get("market") != "USDT-M perpetual swaps":
        raise ValueError("candle source is not BingX USDT-M perpetual swaps")
    if funding.get("exchange") != "BingX" or funding.get("market") != "USDT-M perpetual swaps":
        raise ValueError("funding source is not BingX USDT-M perpetual swaps")
    if candle.get("source_endpoint") != "/openApi/swap/v3/quote/klines":
        raise ValueError("unexpected BingX candle endpoint")
    if funding.get("source_endpoint") != "/openApi/swap/v2/quote/fundingRate":
        raise ValueError("unexpected BingX funding endpoint")
    if candle.get("source_interval") != "1m" or int(candle.get("complete_utc_days", 0)) < 365:
        raise ValueError("BingX source must contain 365 complete 1m UTC days")
    if candle.get("requested_start_utc") != funding.get("requested_start_utc"):
        raise ValueError("candle and funding start edges differ")
    if candle.get("exclusive_end_utc") != funding.get("requested_end_utc"):
        raise ValueError("candle and funding end edges differ")
    if set(funding.get("promotion_core_symbols", [])) != set(SYMBOLS):
        raise ValueError("funding core symbol cohort differs from the required five symbols")
    common = funding.get("core_common_window", {})
    if not common.get("covers_required_days") or not common.get("all_symbols_cadence_and_edges_ok"):
        raise ValueError("funding common-window cadence/edge gate failed")
    if common.get("start_utc") != candle.get("requested_start_utc"):
        raise ValueError("funding common-window start differs from candles")
    if common.get("end_utc") != candle.get("exclusive_end_utc"):
        raise ValueError("funding common-window end differs from candles")
    candle_symbols = candle.get("symbols", {})
    funding_reports = {item["symbol"]: item for item in funding.get("reports", [])}
    hashes: dict[str, dict[str, str]] = {}
    for symbol in SYMBOLS:
        stem = symbol.replace("-", "")
        meta = candle_symbols.get(symbol)
        funding_meta = funding_reports.get(symbol)
        if meta is None or funding_meta is None:
            raise ValueError(f"{symbol}: missing manifest coverage")
        if int(meta.get("one_minute_rows", 0)) != 525_600:
            raise ValueError(f"{symbol}: 1m source row count is incomplete")
        if int(meta.get("missing_minute_bars", -1)) != 0 or int(meta.get("duplicate_minute_bars", -1)) != 0:
            raise ValueError(f"{symbol}: 1m source contains gaps or duplicates")
        if int(meta.get("aggregated_15m_rows", 0)) != 35_040 or int(meta.get("aggregated_1h_rows", 0)) != 8_760:
            raise ValueError(f"{symbol}: aggregated 15m/1h row count is incomplete")
        if not funding_meta.get("cadence_ok") or not funding_meta.get("requested_window_edges_ok"):
            raise ValueError(f"{symbol}: funding cadence or window edges failed")
        if float(funding_meta.get("coverage_days_in_requested_window", 0)) < 365:
            raise ValueError(f"{symbol}: funding coverage shorter than 365 days")
        paths = {
            "1m": data_dir / f"{stem}_1m.csv",
            "15m_aggregated": data_dir / f"{stem}_15m_aggregated.csv",
            "1h_aggregated": data_dir / f"{stem}_1h_aggregated.csv",
            "funding": funding_dir / f"funding_{stem}.csv",
        }
        expected = {
            "1m": meta["one_minute_sha256"],
            "15m_aggregated": meta["aggregated_15m_sha256"],
            "1h_aggregated": meta["aggregated_1h_sha256"],
            "funding": funding_meta["sha256"],
        }
        for label, path in paths.items():
            if not path.is_file():
                raise FileNotFoundError(f"{symbol}: missing {label} file {path}")
            actual = sha256(path)
            if actual != expected[label]:
                raise ValueError(f"{symbol}: {label} SHA-256 differs from manifest")
        hashes[symbol] = {f"{label}_sha256": digest for label, digest in expected.items()}
    return {
        "candle_manifest": candle,
        "funding_manifest": funding,
        "funding_reports": funding_reports,
        "candle_manifest_sha256": sha256(candle_manifest_path),
        "funding_manifest_sha256": sha256(funding_manifest_path),
        "input_hashes": hashes,
    }


def add_volume_features(m15: pd.DataFrame, h1: pd.DataFrame) -> pd.DataFrame:
    """Create causal volume/price-impact features with closed-hour alignment."""
    x = core.add_features(m15, h1)
    atr = x["atr14"].replace(0, np.nan)
    prior_volume = x["volume"].shift(1).rolling(20, min_periods=20).mean()
    x["volume_ratio_prior"] = x["volume"] / prior_volume.replace(0, np.nan)
    x["body_atr"] = (x["close"] - x["open"]).abs() / atr
    candle_range = (x["high"] - x["low"]).replace(0, np.nan)
    x["range_atr"] = candle_range / atr
    x["close_location"] = ((x["close"] - x["low"]) / candle_range).clip(0, 1)
    x["upper_wick_atr"] = (x["high"] - x[["open", "close"]].max(axis=1)) / atr
    x["lower_wick_atr"] = (x[["open", "close"]].min(axis=1) - x["low"]) / atr

    h = h1.copy()
    previous_close = h["close"].shift(1)
    true_range = pd.concat(
        [h["high"] - h["low"], (h["high"] - previous_close).abs(), (h["low"] - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    h["h1_atr14"] = true_range.rolling(14, min_periods=14).mean()
    prior_h1_volume = h["volume"].shift(1).rolling(24, min_periods=24).mean()
    h["h1_volume_ratio_prior"] = h["volume"] / prior_h1_volume.replace(0, np.nan)
    h["h1_price_move_atr"] = (h["close"] - h["open"]) / h["h1_atr14"].replace(0, np.nan)
    h["h1_source_decision_time"] = h["decision_time"]
    hourly = h[["decision_time", "h1_volume_ratio_prior", "h1_price_move_atr", "h1_source_decision_time"]]
    x = pd.merge_asof(
        x.sort_values("decision_time"), hourly.sort_values("decision_time"),
        on="decision_time", direction="backward", allow_exact_matches=True,
    )
    if (x["h1_source_decision_time"] > x["decision_time"]).fillna(False).any():
        raise RuntimeError("lookahead detected: a 15m row used a future 1h candle")
    return x


def _ready(row: pd.Series, fields: tuple[str, ...]) -> bool:
    return not any(pd.isna(row.get(field)) for field in fields)


def choose_volume_impact_continuation(x: pd.DataFrame, i: int) -> dict[str, Any] | None:
    """Join high-volume 15m range escape to a directional, expanding 1h trend."""
    if i < 4 or i + 1 >= len(x):
        return None
    row = x.iloc[i]
    required = (
        "atr14", "h1_adx14", "h1_ema20", "h1_ema50", "h1_ema20_rising", "h1_ema20_falling",
        "h1_volume_ratio_prior", "h1_price_move_atr", "volume_ratio_prior", "body_atr", "close_location",
    )
    if not _ready(row, required) or float(row["atr14"]) <= 0:
        return None
    prior = x.iloc[i - 4 : i]
    swing = x.iloc[i - 3 : i + 1]
    atr = float(row["atr14"])
    if (
        float(row["h1_adx14"]) >= 22
        and row["h1_ema20"] > row["h1_ema50"]
        and bool(row["h1_ema20_rising"])
        and float(row["h1_volume_ratio_prior"]) >= 1.10
        and float(row["h1_price_move_atr"]) >= 0.10
        and float(row["volume_ratio_prior"]) >= 1.50
        and float(row["body_atr"]) >= 0.20
        and float(row["close_location"]) >= 0.75
        and float(row["close"]) > float(prior["high"].max())
    ):
        return {"family": "VOLUME_IMPACT_CONTINUATION", "side": 1,
                "stop": float(swing["low"].min()) - 0.15 * atr, "target_rr": 1.5}
    if (
        float(row["h1_adx14"]) >= 22
        and row["h1_ema20"] < row["h1_ema50"]
        and bool(row["h1_ema20_falling"])
        and float(row["h1_volume_ratio_prior"]) >= 1.10
        and float(row["h1_price_move_atr"]) <= -0.10
        and float(row["volume_ratio_prior"]) >= 1.50
        and float(row["body_atr"]) >= 0.20
        and float(row["close_location"]) <= 0.25
        and float(row["close"]) < float(prior["low"].min())
    ):
        return {"family": "VOLUME_IMPACT_CONTINUATION", "side": -1,
                "stop": float(swing["high"].max()) + 0.15 * atr, "target_rr": 1.5}
    return None


def choose_volume_absorption_release(x: pd.DataFrame, i: int) -> dict[str, Any] | None:
    """Treat high 1h volume with little price displacement as an absorption proxy."""
    if i < 3 or i + 1 >= len(x):
        return None
    row = x.iloc[i]
    required = (
        "atr14", "h1_adx14", "h1_volume_ratio_prior", "h1_price_move_atr",
        "volume_ratio_prior", "body_atr", "close_location",
    )
    if not _ready(row, required) or float(row["atr14"]) <= 0:
        return None
    if (
        float(row["h1_adx14"]) > 18
        or float(row["h1_volume_ratio_prior"]) < 1.50
        or abs(float(row["h1_price_move_atr"])) > 0.25
        or float(row["volume_ratio_prior"]) < 1.50
        or float(row["body_atr"]) < 0.20
    ):
        return None
    prior = x.iloc[i - 3 : i]
    swing = x.iloc[i - 3 : i + 1]
    atr = float(row["atr14"])
    if float(row["close"]) > float(prior["high"].max()) and float(row["close_location"]) >= 0.75:
        return {"family": "VOLUME_ABSORPTION_RELEASE", "side": 1,
                "stop": float(swing["low"].min()) - 0.15 * atr, "target_rr": 1.5}
    if float(row["close"]) < float(prior["low"].min()) and float(row["close_location"]) <= 0.25:
        return {"family": "VOLUME_ABSORPTION_RELEASE", "side": -1,
                "stop": float(swing["high"].max()) + 0.15 * atr, "target_rr": 1.5}
    return None


def choose_volume_regime_router(x: pd.DataFrame, i: int) -> dict[str, Any] | None:
    """Route only to a qualified module; ADX 18-22 is an intentional no-trade band."""
    if i < 1 or pd.isna(x.iloc[i].get("h1_adx14")):
        return None
    adx = float(x.iloc[i]["h1_adx14"])
    if adx >= 22:
        return choose_volume_impact_continuation(x, i)
    if adx <= 18:
        return choose_volume_absorption_release(x, i)
    return None


def _period_picker(signal_fn: Callable[[pd.DataFrame, int], dict[str, Any] | None], start: int, end: int):
    def pick(x: pd.DataFrame, i: int) -> dict[str, Any] | None:
        entry_time = int(x.iloc[i + 1]["open_time"])
        if not start <= entry_time < end:
            return None
        return signal_fn(x, i)
    return pick


def _population(frame: pd.DataFrame) -> list[tuple[Any, ...]]:
    if frame.empty:
        return []
    cols = ["symbol", "side", "entry_time", "exit_time"]
    return sorted(tuple(row) for row in frame[cols].itertuples(index=False, name=None))


def _summary(frame: pd.DataFrame, start: int, end: int) -> dict[str, Any]:
    first = pd.to_datetime(start, unit="ms", utc=True).to_period("M")
    last = pd.to_datetime(end - 1, unit="ms", utc=True).to_period("M")
    result = core.summarize(frame, first, last)
    result["by_symbol"] = {
        str(symbol): core.summarize(group, first, last)
        for symbol, group in frame.groupby("symbol", sort=True)
    } if not frame.empty else {}
    result["by_side"] = {
        str(side): core.summarize(group, first, last)
        for side, group in frame.groupby("side", sort=True)
    } if not frame.empty else {}
    result["by_symbol_side"] = {
        f"{symbol}:{side}": core.summarize(group, first, last)
        for (symbol, side), group in frame.groupby(["symbol", "side"], sort=True)
    } if not frame.empty else {}
    return result


def _simulate(prepared, signal_fn, start, end, costs):
    rows, diagnostics = [], {}
    picker = _period_picker(signal_fn, start, end)
    for symbol, features, funding in prepared:
        # Clip at split end. The core engine requires a full 9h horizon, so it
        # cannot create a trade whose exit would inspect the next split.
        x = features.loc[features["decision_time"] <= end].copy().reset_index(drop=True)
        f = funding.loc[funding["funding_time"] <= end].copy().reset_index(drop=True)
        local = {}
        trades = core.simulate_symbol(
            symbol, x, f,
            costs["fee_bps_per_side"], costs["spread_bps_per_side"], costs["slippage_bps_per_side"],
            diagnostics=local, bar_minutes=15, signal_fn=picker,
        )
        rows.extend(t for t in trades if start <= int(t["entry_time"]) and int(t["exit_time"]) < end)
        diagnostics[symbol] = local
    return pd.DataFrame(rows), diagnostics


def _screen(summary: dict[str, Any]) -> list[str]:
    failures = []
    if int(summary.get("trades", 0)) < MIN_SCREEN_TRADES_PER_PERIOD:
        failures.append(f"trades<{MIN_SCREEN_TRADES_PER_PERIOD}")
    pf = summary.get("pf", 0.0)
    pf_value = float("inf") if pf == "inf" else float(pf)
    if pf_value < 1.0:
        failures.append("net_pf<1.0")
    if float(summary.get("net_expectancy_pct", 0.0)) <= 0:
        failures.append("net_expectancy<=0")
    return failures


def run(args: argparse.Namespace) -> dict[str, Any]:
    provenance = verify_inputs(args.data_dir, args.funding_dir)
    candle = provenance["candle_manifest"]
    funding_manifest = provenance["funding_manifest"]
    prepared = []
    coverage = {}
    for symbol in SYMBOLS:
        stem = symbol.replace("-", "")
        # Use internally consistent 1m-derived 15m/1h bars. The merged native
        # 1h overlap has one 0.1-price-unit close mismatch in the source
        # manifest; that native splice is deliberately excluded from research.
        m15 = core.load_bars(args.data_dir / f"{stem}_15m_aggregated.csv", "15m")
        h1 = core.load_bars(args.data_dir / f"{stem}_1h_aggregated.csv", "1h")
        funding = core.load_funding(args.funding_dir / f"funding_{stem}.csv", symbol)
        funding, excluded = core.latest_contiguous_funding_segment(funding)
        if funding.empty:
            raise ValueError(f"{symbol}: no contiguous BingX funding segment")
        funding_meta = provenance["funding_reports"][symbol]
        if len(funding) != int(funding_meta["events_latest_segment"]):
            raise ValueError(f"{symbol}: funding segment length differs from manifest")
        if int(funding["funding_time"].iloc[0]) != int(funding_meta["latest_segment_start_ms"]):
            raise ValueError(f"{symbol}: funding segment start differs from manifest")
        if int(funding["funding_time"].iloc[-1]) != int(funding_meta["latest_segment_end_ms"]):
            raise ValueError(f"{symbol}: funding segment end differs from manifest")
        features = add_volume_features(m15, h1)
        prepared.append((symbol, features, funding))
        coverage[symbol] = {
            "15m_rows": len(m15), "15m_first": int(m15["open_time"].iloc[0]),
            "15m_last_decision": int(m15["decision_time"].iloc[-1]),
            "1h_rows": len(h1), "1h_first": int(h1["open_time"].iloc[0]),
            "1h_last_decision": int(h1["decision_time"].iloc[-1]),
            "funding_events_latest_contiguous": len(funding),
            "funding_events_excluded_before_latest_segment": excluded,
            "funding_first": int(funding["funding_time"].iloc[0]),
            "funding_last": int(funding["funding_time"].iloc[-1]),
        }
    common_start = max(item["15m_first"] for item in coverage.values())
    common_end = min(item["15m_last_decision"] for item in coverage.values())
    if common_end - common_start < 365 * 86_400_000:
        raise ValueError("common BingX 15m coverage is shorter than 365 days")
    is_start, val_start, val_end, fresh_start = map(
        _ms, (FROZEN_WINDOW_START_UTC, VALIDATION_START_UTC, VALIDATION_END_UTC, FRESH_OOS_START_UTC)
    )
    if common_start != is_start or not (is_start < val_start < val_end < fresh_start):
        raise ValueError("frozen chronological split boundaries do not match source window")
    if common_end < val_end:
        raise ValueError("BingX source does not cover the frozen validation period")
    fresh_oos_days = max(0.0, (common_end - fresh_start) / 86_400_000)
    periods = {"IS": (is_start, val_start), "VALIDATION": (val_start, val_end)}
    modules = {
        "VOLUME_IMPACT_CONTINUATION": choose_volume_impact_continuation,
        "VOLUME_ABSORPTION_RELEASE": choose_volume_absorption_release,
        "VOLUME_REGIME_ROUTER": choose_volume_regime_router,
    }
    cost_cases = {
        "base": dict(BASE_COSTS),
        "stress_1_5x": {key: value * STRESS_MULTIPLIER for key, value in BASE_COSTS.items()},
    }
    results = {}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    any_passed = False
    for module, picker in modules.items():
        results[module] = {"screen_passed": False, "failures": [], "cost_cases": {}}
        populations = {}
        for cost_name, costs in cost_cases.items():
            results[module]["cost_cases"][cost_name] = {}
            for period, (start, end) in periods.items():
                frame, diagnostics = _simulate(prepared, picker, start, end, costs)
                summary = _summary(frame, start, end)
                results[module]["cost_cases"][cost_name][period] = {
                    "summary": summary, "diagnostics": diagnostics,
                }
                frame.to_csv(args.out_dir / f"{module.lower()}_{period.lower()}_{cost_name}.csv", index=False)
                populations[(cost_name, period)] = frame
        for period in periods:
            if _population(populations[("base", period)]) != _population(populations[("stress_1_5x", period)]):
                raise RuntimeError(f"{module} {period}: base/stress trade populations differ")
        failures = []
        for cost_name in cost_cases:
            for period in periods:
                summary = results[module]["cost_cases"][cost_name][period]["summary"]
                failures.extend(f"{cost_name}.{period}:{reason}" for reason in _screen(summary))
        results[module]["failures"] = failures
        results[module]["screen_passed"] = not failures
        any_passed = any_passed or not failures
    native_validation = {
        symbol: candle["symbols"][symbol].get("native_overlap_validation", {})
        for symbol in SYMBOLS
    }
    report = {
        "status": "IS_VALIDATION_PASSED_OOS_PENDING" if any_passed else "IS_VALIDATION_SCREEN_FAILED",
        "venue": "BingX USDT-M perpetual swaps only",
        "symbols": list(SYMBOLS),
        "strategy_family": "15m volume/price-impact proxies; not actual order-flow or wallet identity",
        "signal_timeframe": "closed 15m candles only",
        "context_timeframe": "latest fully closed 1h candle; backward as-of join, exact close-time matches allowed",
        "source_resolution_policy": "1m OHLCV is used only to aggregate 15m/1h bars; no 1m/5m signal or entry",
        "holding_rules": {
            "no_progress_30m": "exit if MFE <0.35R after 2 bars",
            "no_progress_90m": "exit if MFE <0.75R after 6 bars",
            "hard_max_minutes": 540,
        },
        "costs": {"base": BASE_COSTS, "stress_1_5x": cost_cases["stress_1_5x"], "stress_multiplier": STRESS_MULTIPLIER},
        "split_boundaries_utc": {
            "IS_start": FROZEN_WINDOW_START_UTC,
            "IS_end_VALIDATION_start": VALIDATION_START_UTC,
            "VALIDATION_end": VALIDATION_END_UTC,
            "retired_historical_OOS_start": VALIDATION_END_UTC,
            "retired_historical_OOS_end": FRESH_OOS_START_UTC,
            "fresh_OOS_start": FRESH_OOS_START_UTC,
            "fresh_OOS_days_available": round(fresh_oos_days, 2),
            "fresh_OOS_minimum_days": 90,
        },
        "historical_oos_policy": "2026-09-01 through 2026-11-13 is retired; not evaluated or used for selection",
        "data_provenance": {
            "candle_manifest_sha256": provenance["candle_manifest_sha256"],
            "funding_manifest_sha256": provenance["funding_manifest_sha256"],
            "candle_endpoint": candle["source_endpoint"],
            "funding_endpoint": funding_manifest["source_endpoint"],
            "source_interval": candle["source_interval"],
            "complete_utc_days": candle["complete_utc_days"],
            "data_fields": candle["data_fields"],
            "unavailable_fields": candle["unavailable_fields"],
            "coverage": coverage,
            "input_hashes": provenance["input_hashes"],
            "native_overlap_validation": native_validation,
            "bar_source_policy": "use only pure 1m-derived 15m/1h bars; exclude merged native 1h series because manifest records one 0.1 close mismatch",
        },
        "modules": results,
        "promotion_gate": {
            "IS_validation_screen": "at least 30 trades, net PF >=1.0 and positive net expectancy in each split and both cost cases",
            "fresh_OOS_required": "at least 90 untouched days; at least 100 combined trades; net PF >=1.25; positive expectancy; >=70% positive months; acceptable drawdown; positive 1.5x stress expectancy",
            "side_claims": "at least 25 OOS trades per side",
            "current_recommendation": "no VST recommendation until independent fresh OOS gate passes",
        },
    }
    (args.out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--funding-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("reports/bingx_volume_impact"))
    args = parser.parse_args()
    report = run(args)
    print(json.dumps({
        "status": report["status"],
        "modules": {name: {"screen_passed": item["screen_passed"], "failures": item["failures"]}
                    for name, item in report["modules"].items()},
        "fresh_OOS_days_available": report["split_boundaries_utc"]["fresh_OOS_days_available"],
    }, indent=2))


if __name__ == "__main__":
    main()
