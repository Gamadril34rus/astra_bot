#!/usr/bin/env python3
"""Research-only BingX 15m session-VWAP reclaim with closed 1h context."""
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

DEFAULT_SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
ALL_SYMBOLS = DEFAULT_SYMBOLS
SIGNAL_TIMEFRAME = "15m"
CONTEXT_TIMEFRAME = "1h"
BASE_COSTS = {"fee_bps_per_side": 5.0, "spread_bps_per_side": 1.25, "slippage_bps_per_side": 2.0}
STRESS_MULTIPLIER = 1.5
MIN_SCREEN_TRADES_PER_PERIOD = 30
MIN_OOS_TRADES = 100
MIN_OOS_TRADES_PER_SIDE = 25
MIN_OOS_PF = 1.25
MIN_POSITIVE_MONTH_SHARE_PCT = 70.0
MAX_REALIZED_EQUAL_NOTIONAL_DRAWDOWN_PCT = 10.0
MAX_TRADE_SEQUENCE_DRAWDOWN_PCT = 20.0

EXTREME_DEVIATION_ATR = 1.25
RECLAIM_MIN_DEVIATION_ATR = 0.15
RECLAIM_MAX_DEVIATION_ATR = 0.75
MAX_HOURLY_ADX = 20.0
MIN_RELATIVE_VOLUME = 1.0
SWING_LOOKBACK_BARS = 3
STOP_ATR_BUFFER = 0.15


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_session_features(m15: pd.DataFrame, h1: pd.DataFrame) -> pd.DataFrame:
    """Build causal UTC-session VWAP and a prior-20-bar volume baseline."""
    x = core.add_features(m15, h1)
    session_id = pd.to_datetime(x["open_time"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    typical_price = (x["high"] + x["low"] + x["close"]) / 3.0
    volume = x["volume"].astype(float)
    cumulative_volume = volume.groupby(session_id).cumsum()
    cumulative_value = (typical_price * volume).groupby(session_id).cumsum()
    x["session_id"] = session_id
    x["session_vwap"] = cumulative_value / cumulative_volume.replace(0, np.nan)
    x["vwap_deviation_atr"] = (
        (x["close"] - x["session_vwap"]) / x["atr14"].replace(0, np.nan)
    )
    prior_volume_mean = volume.shift(1).rolling(20, min_periods=20).mean()
    x["volume_ratio_prior"] = volume / prior_volume_mean.replace(0, np.nan)
    return x


def choose_session_vwap_signal(x: pd.DataFrame, i: int) -> dict[str, Any] | None:
    """Fade a stretched, weak-trend session auction only after a 15m reclaim."""
    if i < SWING_LOOKBACK_BARS - 1 or i + 1 >= len(x):
        return None
    row = x.iloc[i]
    previous = x.iloc[i - 1]
    swing = x.iloc[i - SWING_LOOKBACK_BARS + 1 : i + 1]
    required = (
        row.get("atr14"),
        row.get("h1_adx14"),
        row.get("session_vwap"),
        row.get("vwap_deviation_atr"),
        previous.get("vwap_deviation_atr"),
        row.get("volume_ratio_prior"),
    )
    if any(pd.isna(value) for value in required):
        return None
    if float(row["atr14"]) <= 0:
        return None
    session = row["session_id"]
    # Never use a prior-day VWAP/swing to trigger a new-day trade or carry the
    # target across the UTC session boundary.
    if (
        previous["session_id"] != session
        or x.iloc[i + 1]["session_id"] != session
        or not swing["session_id"].eq(session).all()
    ):
        return None
    if float(row["h1_adx14"]) > MAX_HOURLY_ADX:
        return None
    if float(row["volume_ratio_prior"]) < MIN_RELATIVE_VOLUME:
        return None

    deviation = float(row["vwap_deviation_atr"])
    prior_deviation = float(previous["vwap_deviation_atr"])
    atr = float(row["atr14"])
    target = float(row["session_vwap"])

    if (
        prior_deviation <= -EXTREME_DEVIATION_ATR
        and -RECLAIM_MAX_DEVIATION_ATR <= deviation <= -RECLAIM_MIN_DEVIATION_ATR
        and float(row["close"]) > float(row["open"])
    ):
        stop = float(swing["low"].min()) - STOP_ATR_BUFFER * atr
        return {
            "family": "SESSION_VWAP_RECLAIM",
            "side": 1,
            "stop": stop,
            "target_price": target,
        }

    if (
        prior_deviation >= EXTREME_DEVIATION_ATR
        and RECLAIM_MIN_DEVIATION_ATR <= deviation <= RECLAIM_MAX_DEVIATION_ATR
        and float(row["close"]) < float(row["open"])
    ):
        stop = float(swing["high"].max()) + STOP_ATR_BUFFER * atr
        return {
            "family": "SESSION_VWAP_RECLAIM",
            "side": -1,
            "stop": stop,
            "target_price": target,
        }
    return None


def _period_signal(
    signal_fn: Callable[[pd.DataFrame, int], dict[str, Any] | None],
    start_ms: int,
    end_ms: int,
) -> Callable[[pd.DataFrame, int], dict[str, Any] | None]:
    """Allow entries only inside one chronological split; no positions cross splits."""
    def pick(x: pd.DataFrame, i: int) -> dict[str, Any] | None:
        entry_time = int(x.iloc[i + 1]["open_time"])
        if not start_ms <= entry_time < end_ms:
            return None
        return signal_fn(x, i)
    return pick


def _period_months(start_ms: int, end_ms: int) -> tuple[pd.Period, pd.Period]:
    first = pd.to_datetime(start_ms, unit="ms", utc=True).to_period("M")
    last = pd.to_datetime(end_ms - 1, unit="ms", utc=True).to_period("M")
    return first, last


def _pf_value(value: Any) -> float:
    if value == "inf":
        return float("inf")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _screen_period(summary: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    if int(summary.get("trades", 0)) < MIN_SCREEN_TRADES_PER_PERIOD:
        reasons.append(f"trades<{MIN_SCREEN_TRADES_PER_PERIOD}")
    if _pf_value(summary.get("pf")) < 1.0:
        reasons.append("net_pf<1.0")
    if float(summary.get("net_expectancy_pct", 0.0)) <= 0:
        reasons.append("net_expectancy<=0")
    return not reasons, reasons


def _screen_passes(screen: dict[str, dict[str, dict[str, Any]]]) -> tuple[bool, list[str]]:
    reasons = []
    for cost_case in ("base", "stress_1_5x"):
        for period in ("IS", "VALIDATION"):
            passed, failures = _screen_period(screen[cost_case][period])
            if not passed:
                reasons.extend(f"{cost_case}.{period}:{failure}" for failure in failures)
    return not reasons, reasons


def _simulate_period(
    prepared: list[tuple[str, pd.DataFrame, pd.DataFrame]],
    start_ms: int,
    end_ms: int,
    costs: dict[str, float],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}
    picker = _period_signal(choose_session_vwap_signal, start_ms, end_ms)
    for symbol, features, funding in prepared:
        symbol_diagnostics: dict[str, Any] = {}
        trades = core.simulate_symbol(
            symbol,
            features,
            funding,
            costs["fee_bps_per_side"],
            costs["spread_bps_per_side"],
            costs["slippage_bps_per_side"],
            diagnostics=symbol_diagnostics,
            bar_minutes=15,
            signal_fn=picker,
        )
        rows.extend(
            trade for trade in trades
            if int(trade["entry_time"]) >= start_ms and int(trade["exit_time"]) < end_ms
        )
        diagnostics[symbol] = symbol_diagnostics
    return pd.DataFrame(rows), diagnostics


def _add_notional_pnl(summary: dict[str, Any]) -> dict[str, Any]:
    # Research-normalized PnL in USDT per 1,000 USDT of notional per trade.
    for key, source in (
        ("gross_usdt_per_1000", "gross_sum_pct"),
        ("taker_fee_usdt_per_1000", "taker_fee_sum_pct"),
        ("spread_usdt_per_1000", "spread_sum_pct"),
        ("slippage_usdt_per_1000", "slippage_sum_pct"),
        ("funding_cost_usdt_per_1000", "funding_sum_pct"),
        ("net_usdt_per_1000", "sum_trade_net_pct"),
    ):
        summary[key] = round(float(summary.get(source, 0.0)) * 10.0, 4)
    summary["funding_convention"] = "positive funding_sum_pct is paid; negative is received"
    return summary


def _population_signature(frame: pd.DataFrame) -> list[tuple[Any, ...]]:
    if frame.empty:
        return []
    columns = ["symbol", "side", "entry_time", "exit_time"]
    return sorted(tuple(row) for row in frame[columns].itertuples(index=False, name=None))


def _assert_same_population(base: pd.DataFrame, stress: pd.DataFrame, label: str) -> None:
    if _population_signature(base) != _population_signature(stress):
        raise RuntimeError(f"{label}: base and 1.5x cost cases do not share an identical trade population")


def _summary(frame: pd.DataFrame, start_ms: int, end_ms: int) -> dict[str, Any]:
    first_month, last_month = _period_months(start_ms, end_ms)
    result = _add_notional_pnl(core.summarize(frame, first_month, last_month))
    if not frame.empty:
        result["by_symbol"] = {
            str(symbol): _add_notional_pnl(
                core.summarize(group, first_month, last_month)
            )
            for symbol, group in frame.groupby("symbol", sort=True)
        }
        result["by_side"] = {
            str(side): _add_notional_pnl(
                core.summarize(group, first_month, last_month)
            )
            for side, group in frame.groupby("side", sort=True)
        }
        result["by_symbol_side"] = {
            f"{symbol}:{side}": _add_notional_pnl(
                core.summarize(group, first_month, last_month)
            )
            for (symbol, side), group in frame.groupby(["symbol", "side"], sort=True)
        }
    else:
        result["by_symbol"] = {}
        result["by_side"] = {}
        result["by_symbol_side"] = {}
    return result


def _oos_gate(
    base: dict[str, Any],
    stress: dict[str, Any],
    long_trades: int,
    short_trades: int,
) -> dict[str, Any]:
    failures = []
    if int(base["trades"]) < MIN_OOS_TRADES:
        failures.append(f"base_oos_trades<{MIN_OOS_TRADES}")
    if _pf_value(base["pf"]) < MIN_OOS_PF:
        failures.append(f"base_oos_pf<{MIN_OOS_PF}")
    if float(base["net_expectancy_pct"]) <= 0:
        failures.append("base_oos_net_expectancy<=0")
    if float(base["positive_month_share_pct"]) < MIN_POSITIVE_MONTH_SHARE_PCT:
        failures.append(f"base_oos_positive_month_share<{MIN_POSITIVE_MONTH_SHARE_PCT}")
    if float(base["realized_equal_notional_drawdown_pct"]) < -MAX_REALIZED_EQUAL_NOTIONAL_DRAWDOWN_PCT:
        failures.append(f"base_oos_realized_drawdown>{MAX_REALIZED_EQUAL_NOTIONAL_DRAWDOWN_PCT}")
    if float(base["trade_sequence_drawdown_pct"]) < -MAX_TRADE_SEQUENCE_DRAWDOWN_PCT:
        failures.append(f"base_oos_trade_sequence_drawdown>{MAX_TRADE_SEQUENCE_DRAWDOWN_PCT}")
    if float(stress["net_expectancy_pct"]) <= 0:
        failures.append("stress_oos_net_expectancy<=0")
    if _pf_value(stress["pf"]) < 1.0:
        failures.append("stress_oos_net_pf<1.0")
    if float(stress["positive_month_share_pct"]) < MIN_POSITIVE_MONTH_SHARE_PCT:
        failures.append(f"stress_oos_positive_month_share<{MIN_POSITIVE_MONTH_SHARE_PCT}")
    if float(stress["realized_equal_notional_drawdown_pct"]) < -MAX_REALIZED_EQUAL_NOTIONAL_DRAWDOWN_PCT:
        failures.append(f"stress_oos_realized_drawdown>{MAX_REALIZED_EQUAL_NOTIONAL_DRAWDOWN_PCT}")
    if float(stress["trade_sequence_drawdown_pct"]) < -MAX_TRADE_SEQUENCE_DRAWDOWN_PCT:
        failures.append(f"stress_oos_trade_sequence_drawdown>{MAX_TRADE_SEQUENCE_DRAWDOWN_PCT}")
    return {
        "passed": not failures,
        "failures": failures,
        "long_trades": long_trades,
        "short_trades": short_trades,
        "side_specific_claims_allowed": (
            long_trades >= MIN_OOS_TRADES_PER_SIDE
            and short_trades >= MIN_OOS_TRADES_PER_SIDE
        ),
        "side_sample_warning": (
            None if long_trades >= MIN_OOS_TRADES_PER_SIDE
            and short_trades >= MIN_OOS_TRADES_PER_SIDE
            else f"at least {MIN_OOS_TRADES_PER_SIDE} OOS trades per side are required for side-specific claims"
        ),
    }


def _load_provenance(path: Path, expected_market: str) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("exchange") != "BingX" or manifest.get("market") != expected_market:
        raise ValueError(f"{path}: not a verified BingX USDT-M perpetual manifest")
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "exchange": manifest.get("exchange"),
        "market": manifest.get("market"),
        "manifest": manifest,
    }


def run_research(args: argparse.Namespace) -> dict[str, Any]:
    candle_provenance = _load_provenance(args.candle_manifest, "USDT-M perpetual swaps")
    funding_provenance = _load_provenance(args.funding_manifest, "USDT-M perpetual swaps")
    candle_manifest = candle_provenance["manifest"]
    funding_manifest = funding_provenance["manifest"]
    if int(candle_manifest.get("complete_utc_days", 0)) < 365:
        raise ValueError("BingX 1m source archive does not contain 365 complete UTC days")
    funding_window = funding_manifest.get("core_common_window", {})
    if set(funding_manifest.get("promotion_core_symbols", [])) != set(args.symbols):
        raise ValueError("funding manifest promotion cohort does not match the requested symbol cohort")
    if not set(args.symbols).issubset(set(candle_manifest.get("symbols", []))):
        raise ValueError("candle manifest does not contain every requested BingX symbol")
    if (
        not funding_window.get("covers_required_days")
        or not funding_window.get("all_symbols_cadence_and_edges_ok")
    ):
        raise ValueError("merged BingX funding manifest does not prove the required common window")

    prepared: list[tuple[str, pd.DataFrame, pd.DataFrame]] = []
    coverage: dict[str, dict[str, Any]] = {}
    source_hashes: dict[str, dict[str, str]] = {}
    for symbol in args.symbols:
        stem = symbol.replace("-", "")
        m15_path = args.data_dir / f"{stem}_15m.csv"
        h1_path = args.data_dir / f"{stem}_1h.csv"
        funding_path = args.data_dir / f"funding_{stem}.csv"
        for path in (m15_path, h1_path, funding_path):
            if not path.is_file():
                raise FileNotFoundError(f"required BingX research input is missing: {path}")
        m15 = core.load_bars(m15_path, "15m")
        h1 = core.load_bars(h1_path, "1h")
        raw_funding = core.load_funding(funding_path, symbol)
        funding, excluded = core.latest_contiguous_funding_segment(raw_funding)
        if funding.empty:
            raise ValueError(f"{symbol}: no contiguous BingX funding segment")
        recent_gaps = np.diff(funding["funding_time"].to_numpy(dtype="int64")) / 3_600_000
        if len(recent_gaps):
            median_gap = float(np.median(recent_gaps[-min(200, len(recent_gaps)):]))
            gap_limit = max(1.5 * median_gap, median_gap + 1.0)
            if float(recent_gaps.max()) > gap_limit:
                raise ValueError(f"{symbol}: unresolved funding gap in the selected segment")
        m15 = m15[m15["open_time"] >= int(funding["funding_time"].iloc[0])].reset_index(drop=True)
        if m15.empty:
            raise ValueError(f"{symbol}: no 15m bars inside contiguous funding coverage")
        features = add_session_features(m15, h1)
        prepared.append((symbol, features, funding))
        coverage[symbol] = {
            "15m_rows": len(m15),
            "15m_first": int(m15["open_time"].iloc[0]),
            "15m_last": int(m15["decision_time"].iloc[-1]),
            "1h_rows": len(h1),
            "1h_first": int(h1["open_time"].iloc[0]),
            "1h_last": int(h1["decision_time"].iloc[-1]),
            "funding_events": len(funding),
            "funding_events_excluded_before_latest_segment": excluded,
            "funding_first": int(funding["funding_time"].iloc[0]),
            "funding_last": int(funding["funding_time"].iloc[-1]),
            "funding_median_gap_hours": (
                round(float(np.median(recent_gaps)), 4) if len(recent_gaps) else None
            ),
            "funding_max_gap_hours": (
                round(float(recent_gaps.max()), 4) if len(recent_gaps) else None
            ),
        }
        source_hashes[symbol] = {
            "15m_sha256": _sha256(m15_path),
            "1h_sha256": _sha256(h1_path),
            "funding_sha256": _sha256(funding_path),
        }

    common = core.common_15m_coverage_window(coverage, "15m")
    if not common["covers_12_months"]:
        raise ValueError(
            f"common BingX 15m window is only {common['common_days']} days; 365 required"
        )
    common_start_ms = int(pd.Timestamp(common["common_start_utc"]).timestamp() * 1000)
    common_end_ms = int(pd.Timestamp(common["common_end_utc"]).timestamp() * 1000)
    span = common_end_ms - common_start_ms
    validation_start_ms = common_start_ms + int(span * 0.60)
    oos_start_ms = common_start_ms + int(span * 0.80)
    boundaries = {
        "is_start": pd.to_datetime(common_start_ms, unit="ms", utc=True).isoformat(),
        "validation_start": pd.to_datetime(validation_start_ms, unit="ms", utc=True).isoformat(),
        "oos_start": pd.to_datetime(oos_start_ms, unit="ms", utc=True).isoformat(),
        "oos_end": pd.to_datetime(common_end_ms, unit="ms", utc=True).isoformat(),
    }

    base_costs = dict(BASE_COSTS)
    stress_costs = {key: value * STRESS_MULTIPLIER for key, value in base_costs.items()}
    screen: dict[str, dict[str, dict[str, Any]]] = {
        "base": {},
        "stress_1_5x": {},
    }
    screen_trades: dict[str, dict[str, pd.DataFrame]] = {
        "base": {},
        "stress_1_5x": {},
    }
    screen_diagnostics: dict[str, dict[str, Any]] = {
        "base": {},
        "stress_1_5x": {},
    }
    periods = {
        "IS": (common_start_ms, validation_start_ms),
        "VALIDATION": (validation_start_ms, oos_start_ms),
    }
    for cost_case, costs in (("base", base_costs), ("stress_1_5x", stress_costs)):
        for period, (period_start, period_end) in periods.items():
            frame, diagnostics = _simulate_period(prepared, period_start, period_end, costs)
            screen_trades[cost_case][period] = frame
            screen_diagnostics[cost_case][period] = diagnostics
            screen[cost_case][period] = _summary(frame, period_start, period_end)

    for period in periods:
        _assert_same_population(
            screen_trades["base"][period],
            screen_trades["stress_1_5x"][period],
            f"{period} screen",
        )
    screen_passed, screen_failures = _screen_passes(screen)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for cost_case in screen_trades:
        for period, frame in screen_trades[cost_case].items():
            frame.to_csv(args.out_dir / f"trades_{period.lower()}_{cost_case}.csv", index=False)

    report: dict[str, Any] = {
        "status": "IS_VALIDATION_SCREEN_FAILED" if not screen_passed else "IS_VALIDATION_PASSED_OOS_PENDING",
        "strategy": "SESSION_VWAP_RECLAIM",
        "venue": "BingX USDT-M perpetual swaps only",
        "symbols": list(args.symbols),
        "signal_timeframe": SIGNAL_TIMEFRAME,
        "context_timeframe": "latest fully closed 1h candle",
        "holding_rules": {
            "no_progress_exit_30m": {"bars": 2, "mfe_r_below": 0.35},
            "no_progress_exit_90m": {"bars": 6, "mfe_r_below": 0.75},
            "hard_maximum_minutes": 540,
            "typical_holding_target_minutes": 30,
        },
        "hypothesis": {
            "regime": f"closed 1h ADX14 <= {MAX_HOURLY_ADX}",
            "anchor": "UTC-session VWAP from completed 15m typical-price x base-volume bars",
            "long": (
                f"previous 15m close <= VWAP - {EXTREME_DEVIATION_ATR} ATR; "
                f"current 15m closes back to {RECLAIM_MIN_DEVIATION_ATR}-{RECLAIM_MAX_DEVIATION_ATR} ATR below VWAP"
            ),
            "short": (
                f"previous 15m close >= VWAP + {EXTREME_DEVIATION_ATR} ATR; "
                f"current 15m closes back to {RECLAIM_MIN_DEVIATION_ATR}-{RECLAIM_MAX_DEVIATION_ATR} ATR above VWAP"
            ),
            "confirmation": "directional 15m candle and current volume >= prior 20-bar mean",
            "entry": "next 15m open",
            "stop": f"3-bar swing plus/minus {STOP_ATR_BUFFER} ATR",
            "target": "fixed session VWAP measured at signal close; minimum 1.0R",
            "one_position_per_symbol": True,
            "no_forced_trade": True,
        },
        "costs": {
            "base": base_costs,
            "stress_1_5x": stress_costs,
            "stress_factor": STRESS_MULTIPLIER,
            "execution_costs_are_proxies": True,
        },
        "drawdown_guardrails": {
            "realized_equal_notional_drawdown_max_pct": MAX_REALIZED_EQUAL_NOTIONAL_DRAWDOWN_PCT,
            "trade_sequence_drawdown_max_pct": MAX_TRADE_SEQUENCE_DRAWDOWN_PCT,
            "note": "research OHLC/realized-PnL proxies; not intratrade margin or liquidation risk",
        },
        "coverage": coverage,
        "common_15m_window": common,
        "funding_common_window": funding_window,
        "split_boundaries_utc": boundaries,
        "data_provenance": {
            "candle_manifest": {
                "path": candle_provenance["path"],
                "sha256": candle_provenance["sha256"],
                "source_endpoint": candle_manifest.get("source_endpoint"),
                "source_interval": candle_manifest.get("source_interval"),
                "aggregation": candle_manifest.get("aggregation"),
                "unavailable_fields": candle_manifest.get("unavailable_fields"),
            },
            "funding_manifest": {
                "path": funding_provenance["path"],
                "sha256": funding_provenance["sha256"],
                "source_endpoint": funding_manifest.get("source_endpoint"),
                "source_artifacts": funding_manifest.get("source_artifacts"),
                "core_common_window": funding_window,
            },
            "input_hashes": source_hashes,
            "volume_source": "BingX base-asset volume only; quote volume, trade count and taker-buy fields are not assumed",
        },
        "no_lookahead": {
            "signals": "closed 15m bars only",
            "entry": "next 15m open",
            "hourly_context": "backward as-of join on 1h close time; no forming 1h bar",
            "session_vwap": "cumulative from current UTC session using only completed 15m bars",
            "volume_baseline": "prior 20 closed 15m bars; current signal bar excluded from denominator",
            "funding": "actual BingX settlement timestamps; no forward-fill or imputation",
            "split_boundary": "each period simulated separately; trades crossing a split boundary are excluded",
        },
        "screen": screen,
        "screen_diagnostics": screen_diagnostics,
        "is_validation_gate": {
            "passed": screen_passed,
            "failures": screen_failures,
            "minimum_trades_per_period": MIN_SCREEN_TRADES_PER_PERIOD,
            "rule": "IS and validation must each have PF >=1.0 and positive net expectancy at base and 1.5x costs",
        },
        "oos_opened": False,
        "warning": (
            "Research-only OHLC proxy. Spread/slippage are conservative assumptions, not historical exchange fills. "
            "Funding is charged at actual BingX event timestamps. No production, VST or REAL state is changed."
        ),
    }

    if screen_passed:
        oos_base, diag_base = _simulate_period(
            prepared, oos_start_ms, common_end_ms, base_costs
        )
        oos_stress, diag_stress = _simulate_period(
            prepared, oos_start_ms, common_end_ms, stress_costs
        )
        _assert_same_population(oos_base, oos_stress, "OOS replay")
        oos_base_summary = _summary(oos_base, oos_start_ms, common_end_ms)
        oos_stress_summary = _summary(oos_stress, oos_start_ms, common_end_ms)
        long_count = int((oos_base["side"] == "LONG").sum()) if not oos_base.empty else 0
        short_count = int((oos_base["side"] == "SHORT").sum()) if not oos_base.empty else 0
        gate = _oos_gate(oos_base_summary, oos_stress_summary, long_count, short_count)
        report["oos_opened"] = True
        report["oos"] = {
            "base": oos_base_summary,
            "stress_1_5x": oos_stress_summary,
            "diagnostics": {"base": diag_base, "stress_1_5x": diag_stress},
            "gate": gate,
        }
        report["status"] = (
            "OWNER_REVIEW_READY_VST_PLANNING_ONLY" if gate["passed"] else "OOS_GATE_FAILED"
        )
        oos_base.to_csv(args.out_dir / "trades_oos_base.csv", index=False)
        oos_stress.to_csv(args.out_dir / "trades_oos_stress_1_5x.csv", index=False)
    else:
        report["oos"] = {
            "status": "UNOPENED",
            "reason": "IS/validation screen failed; OOS data was not simulated or summarized",
        }

    (args.out_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": report["status"],
        "common_15m_days": common["common_days"],
        "is_validation_gate": report["is_validation_gate"],
        "oos_opened": report["oos_opened"],
        "oos": report.get("oos"),
    }, ensure_ascii=False, allow_nan=False))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/bingx_usdm"))
    parser.add_argument("--candle-manifest", type=Path, required=True)
    parser.add_argument("--funding-manifest", type=Path, required=True)
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--out-dir", type=Path, default=Path("reports/session_vwap_reclaim"))
    args = parser.parse_args()
    args.symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    if not args.symbols or set(args.symbols) - set(DEFAULT_SYMBOLS):
        raise SystemExit("promotion cohort must be a non-empty subset of BTC, ETH, SOL, HYPE and INJ")
    run_research(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
