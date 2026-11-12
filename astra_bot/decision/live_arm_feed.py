"""Arm feed: paper-parity channel detector -> models/zeus_arm_pending.jsonl.

Strategy = ZeusChannelBoundary with channel_capital_config() fields from
scripts/zeus_research_wrap.py (active paper runner). No new thresholds.
No orders - write pending only. ≤1 candidate per symbol per tick.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from astra_bot.decision.live_arm_wiring import PENDING_PATH
from astra_bot.strategies.zeus_channel_boundary import (
    ZeusChannelBoundaryConfig,
    ZeusChannelBoundaryStrategy,
)
from astra_bot.strategies.zeus_multi_top_breakdown import (
    detect_breakdown_from_ohlc,
    series_from_candles,
)

logger = logging.getLogger(__name__)
ZONE_FILTER_JOURNAL_PATH = Path("models/zone_trade_filter.jsonl")
_ZONE_CACHE: dict[str, tuple[int, list[dict[str, float]]]] = {}
_ZONE_SHADOW_CACHE: dict[str, tuple[int, list[dict[str, float]]]] = {}
_ZONE_PROFILE_META: dict[str, dict[str, Any]] = {}

TIMEFRAME = "4h"
CANDLE_LIMIT = 120
STRATEGY_NAME = "zeus_channel_boundary"

# Universe v3 (owner 31.10 shadow): majors only until alts prove net+.
# Evidence: shadow 7d channel core +33%, alts drag; replay 48; hours thin/alts toxic.
# HYPE+INJ per owner AUDIT 800; INJ only when open_n <= MAX_OPEN-2.
FEED_SYMBOLS = (
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "HYPE-USDT",  # owner AUDIT 800
    "INJ-USDT",  # owner AUDIT 800 + slot reserve (MAX_OPEN-2)
)

# asdict-diff vs ZeusChannelBoundaryConfig defaults (paper channel_capital_config):
#   enabled: False -> True
#   lookback: 30 -> 48 (proxy v1)
#   min_width_pct: 0.012 -> 0.01
#   min_rr: 1.5 -> 2.0
#   max_hold_bars: 8 -> 5
#   tp2_rr: 3.0 -> 2.0
# (min_stop_pct 0.008, require_htf_bias True, tp1_rr 1.5, fractions 0.4/0.6 - same)


def paper_channel_config() -> ZeusChannelBoundaryConfig:
    """Live/paper channel: Proxy v1 (owner 01.11) — numbers are bot assumptions."""
    return ZeusChannelBoundaryConfig(
        enabled=True,
        lookback=48,
        min_width_pct=0.01,
        max_width_pct=0.12,
        near_boundary_width_frac=0.20,
        use_parallel_clone=True,
        parallel_tol=0.25,
        min_rr=2.0,
        enable_breakout_hold=True,  # naked hold remains (risky variant)
        enable_breakout_retest=True,
        retest_atr_mult=0.15,
        min_hold_bars=2,
        max_hold_bars=5,
        min_stop_pct=0.01,
        require_htf_bias=True,
        tp1_rr=1.5,
        tp2_rr=2.0,
        tp1_fraction=0.4,
        tp2_fraction=0.6,
    )


def make_signal_id(symbol: str, side: str, bar_ts: int, strategy: str = STRATEGY_NAME) -> str:
    return f"{strategy}|{symbol}|{side.lower()}|{int(bar_ts)}"


def _direction_str(sig: Any) -> str:
    d = getattr(sig, "direction", None)
    if d is None:
        return ""
    v = getattr(d, "value", None)
    if v is not None:
        return str(v).lower()
    s = str(d).lower()
    if "long" in s:
        return "long"
    if "short" in s:
        return "short"
    return s


def signal_to_row(sig: Any, *, bar_ts: int, now: float | None = None) -> dict[str, Any]:
    now = now if now is not None else time.time()
    side = _direction_str(sig)
    if side not in ("long", "short"):
        raise ValueError(f"bad side {side!r}")
    entry = float(sig.entry_price)
    stop = float(sig.stop_loss)
    tp_raw = getattr(sig, "take_profit", None)
    tp = float(tp_raw) if tp_raw is not None else None
    conf = float(getattr(sig, "confidence", 0) or 0)
    symbol = str(sig.symbol)
    return {
        "signal_id": make_signal_id(symbol, side, bar_ts),
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "stop": stop,
        "tp": tp,
        "retest_ts": int(bar_ts),
        "ts": now,
        "strategy": STRATEGY_NAME,
        "confidence": conf,
    }


def write_pending(rows: list[dict[str, Any]], path: Path = PENDING_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


async def fetch_candles(
    client: Any,
    symbol: str,
    *,
    timeframe: str = TIMEFRAME,
    limit: int = CANDLE_LIMIT,
) -> list:
    return await client.get_candles(symbol, timeframe, limit=limit)


def _channel_strategy() -> ZeusChannelBoundaryStrategy:
    return ZeusChannelBoundaryStrategy(paper_channel_config())


def _candle_value(row: Any, key: str) -> float:
    aliases = {
        "open_time": ("open_time", "time", "timestamp", "ts"),
        "close": ("close", "close_price", "c"),
    }.get(key, (key,))
    for name in aliases:
        raw = row.get(name) if isinstance(row, dict) else getattr(row, name, None)
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
    return 0.0


_ZONE_APPROACH_PATH = Path("models/zone_shadow_approach.json")

def _load_zone_approach_state(path: Path | None = None) -> dict[str, Any]:
    try:
        path = path or _ZONE_APPROACH_PATH
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}

def _save_zone_approach_state(state: dict[str, Any], path: Path | None = None) -> None:
    try:
        path = path or _ZONE_APPROACH_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        logger.warning("zone shadow approach state unavailable: %s", type(exc).__name__)

async def scan_zone_shadow(client: Any, symbols: list[str] | tuple[str, ...] = FEED_SYMBOLS) -> dict[str, dict[str, Any]]:
    """Observe zones and persist per-closed-4h-bar minimum approach distances."""
    try:
        from astra_bot.decision.zone_shadow import (
            build_volume_zones_with_fallback, is_enabled, zone_context,
        )
        if not is_enabled():
            return {}
    except Exception as exc:
        logger.warning("zone shadow unavailable: %s", type(exc).__name__)
        return {}
    from astra_bot.decision.live_arm_wiring import append_journal
    approach_state = _load_zone_approach_state()
    out: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        try:
            zone_candles = await fetch_candles(client, symbol, timeframe="4h", limit=600)
            if len(zone_candles) < 2:
                raise ValueError("not_enough_4h_candles")
            closed = zone_candles[-2]
            closed_ts = int(_candle_value(closed, "open_time") or 0)
            if closed_ts <= 0:
                closed_ts = int(time.time() // 14400 * 14400)
            closed_price = _candle_value(closed, "close")
            if closed_price <= 0:
                raise ValueError("closed_candle_missing_close")
            cached = _ZONE_CACHE.get(symbol)
            if cached is None or cached[0] != closed_ts:
                shadow_zones, shadow_meta = build_volume_zones_with_fallback(
                    list(zone_candles), price=closed_price, fallback_lookback=120,
                )
                # Recent-window fallbacks are observational only until at least
                # 100 unique candidates have been measured. Keep the live filter
                # on its original full-window result (empty = fail-closed).
                if shadow_meta.get("source") == "recent_fallback":
                    filter_zones: list[dict[str, float]] = []
                    profile_meta = {
                        "source": "empty_full_window_shadow_only",
                        "lookback_bars": shadow_meta.get("full_lookback_bars"),
                        "shadow_source": "recent_fallback",
                        "shadow_lookback_bars": shadow_meta.get("lookback_bars"),
                        "shadow_only": True,
                    }
                else:
                    filter_zones = shadow_zones
                    profile_meta = {
                        "source": shadow_meta.get("source"),
                        "lookback_bars": shadow_meta.get("lookback_bars"),
                        "shadow_source": shadow_meta.get("source"),
                        "shadow_lookback_bars": shadow_meta.get("lookback_bars"),
                        "shadow_only": False,
                    }
                _ZONE_CACHE[symbol] = (closed_ts, filter_zones)
                _ZONE_SHADOW_CACHE[symbol] = (closed_ts, shadow_zones)
                _ZONE_PROFILE_META[symbol] = profile_meta
            else:
                filter_zones = cached[1]
                shadow_cached = _ZONE_SHADOW_CACHE.get(symbol)
                shadow_zones = (
                    shadow_cached[1]
                    if shadow_cached is not None and shadow_cached[0] == closed_ts
                    else filter_zones
                )
                profile_meta = _ZONE_PROFILE_META.get(
                    symbol, {"source": "cached", "lookback_bars": max(0, len(zone_candles) - 1)}
                )
            price_now = _candle_value(zone_candles[-1], "close") or closed_price
            context = zone_context(price_now, shadow_zones)
            context["profile_source"] = profile_meta.get("shadow_source") or profile_meta.get("source")
            context["profile_lookback_bars"] = profile_meta.get("shadow_lookback_bars") or profile_meta.get("lookback_bars")
            context["filter_profile_source"] = profile_meta.get("source")
            out[symbol] = context
            previous = approach_state.get(symbol)
            previous = previous if isinstance(previous, dict) else {}
            old_ts = previous.get("bar_ts")
            try:
                old_ts = int(old_ts) if old_ts is not None else None
            except (TypeError, ValueError, OverflowError):
                old_ts = None
            old_minima = previous.get("zones")
            old_minima = old_minima if isinstance(old_minima, dict) else {}
            if old_ts is not None and old_ts != closed_ts:
                summary_zones = []
                for center_key, minimum in old_minima.items():
                    try:
                        summary_zones.append({"center": float(center_key), "min_distance_pct": round(float(minimum), 4)})
                    except (TypeError, ValueError):
                        continue
                try:
                    append_journal({
                        "event": "zone_shadow_summary", "symbol": symbol, "bar_ts": old_ts,
                        "zones": summary_zones,
                        "profile_source": previous.get("profile_source"),
                        "profile_lookback_bars": previous.get("profile_lookback_bars"),
                        "ts": time.time(),
                    })
                except Exception as exc:
                    logger.warning("zone shadow summary journal failed %s: %s", symbol, type(exc).__name__)
                minima = {}
            elif old_ts is not None and old_ts == closed_ts:
                minima = dict(old_minima)
            else:
                minima = {}
            zone_rows = []
            if shadow_zones and price_now > 0:
                for zone in shadow_zones:
                    try:
                        center = float(zone["center"])
                        key = str(center)
                        distance = round(abs((center / price_now - 1.0) * 100.0), 4)
                        if key not in minima or distance < float(minima[key]):
                            minima[key] = distance
                        row = dict(zone)
                        row["min_distance_pct"] = round(float(minima[key]), 4)
                        zone_rows.append(row)
                    except (KeyError, TypeError, ValueError, ZeroDivisionError):
                        continue
            approach_state[symbol] = {
                "bar_ts": closed_ts, "zones": minima,
                "profile_source": profile_meta.get("shadow_source") or profile_meta.get("source"),
                "profile_lookback_bars": profile_meta.get("shadow_lookback_bars") or profile_meta.get("lookback_bars"),
                "filter_profile_source": profile_meta.get("source"),
            }
            _save_zone_approach_state(approach_state)
            try:
                append_journal({
                    "event": "zone_shadow", "symbol": symbol, "price": price_now,
                    "nearest_above": context.get("above"),
                    "nearest_below": context.get("below"),
                    "in_zone": context.get("in_zone"), "zones": zone_rows,
                    "profile_source": profile_meta.get("shadow_source") or profile_meta.get("source"),
                    "profile_lookback_bars": profile_meta.get("shadow_lookback_bars") or profile_meta.get("lookback_bars"),
                    "filter_profile_source": profile_meta.get("source"),
                    "bar_ts": closed_ts, "ts": time.time(),
                })
            except Exception as exc:
                logger.warning("zone shadow journal failed %s: %s", symbol, type(exc).__name__)
        except Exception as exc:
            logger.warning("zone shadow failed %s: %s", symbol, type(exc).__name__)
            try:
                append_journal({"event": "zone_shadow_error", "symbol": symbol, "error": type(exc).__name__, "ts": time.time()})
            except Exception:
                pass
    return out


def _append_zone_trade_filter_event(event: dict[str, Any]) -> None:
    """Persist the compact, independently readable zone-gate audit stream."""
    path = ZONE_FILTER_JOURNAL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def _zone_trade_filter_decision(
    row: dict[str, Any], zones: list[dict[str, float]] | None,
) -> dict[str, Any]:
    """Veto a candidate only when a volume zone lies before its planned target."""
    import math

    side = str(row.get("side") or "").lower()
    try:
        entry = float(row["entry"])
        stop = float(row["stop"])
        target = float(row["tp"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return {"accepted": False, "state": "UNKNOWN", "reason": "invalid_candidate_prices"}
    if (
        side not in {"long", "short"}
        or not all(math.isfinite(v) and v > 0 for v in (entry, stop, target))
        or (side == "long" and not stop < entry < target)
        or (side == "short" and not target < entry < stop)
    ):
        return {"accepted": False, "state": "UNKNOWN", "reason": "invalid_candidate_geometry"}
    if zones is None:
        return {"accepted": False, "state": "UNKNOWN", "reason": "zones_unavailable"}

    valid: list[tuple[float, float]] = []
    for zone in zones:
        try:
            lower, upper = float(zone["lower"]), float(zone["upper"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return {"accepted": False, "state": "UNKNOWN", "reason": "invalid_zone_payload"}
        if not (math.isfinite(lower) and math.isfinite(upper) and 0 < lower <= upper):
            return {"accepted": False, "state": "UNKNOWN", "reason": "invalid_zone_payload"}
        valid.append((lower, upper))
    if not valid:
        return {"accepted": False, "state": "UNKNOWN", "reason": "empty_or_unusable_zone_profile"}

    # If entry is already inside a zone, its far edge is the first obstacle.
    if side == "long":
        edges = [
            lower if lower > entry else upper
            for lower, upper in valid
            if lower > entry or lower <= entry <= upper
        ]
        boundary = min(edges) if edges else None
        if boundary is None:
            return {"accepted": True, "state": "NO_OPPOSING_ZONE", "reason": "no_zone_ahead"}
        room_r = (boundary - entry) / abs(entry - stop)
        target_r = (target - entry) / abs(entry - stop)
        blocked = boundary <= target
    else:
        edges = [
            upper if upper < entry else lower
            for lower, upper in valid
            if upper < entry or lower <= entry <= upper
        ]
        boundary = max(edges) if edges else None
        if boundary is None:
            return {"accepted": True, "state": "NO_OPPOSING_ZONE", "reason": "no_zone_ahead"}
        room_r = (entry - boundary) / abs(entry - stop)
        target_r = (entry - target) / abs(entry - stop)
        blocked = boundary >= target

    return {
        "accepted": not blocked,
        "state": "ZONE_BEFORE_TARGET" if blocked else "TARGET_BEFORE_ZONE",
        "reason": "opposing_zone_intersects_reward_path" if blocked else "planned_target_precedes_zone",
        "boundary": boundary,
        "room_r": round(room_r, 6),
        "target_r": round(target_r, 6),
    }


def _apply_zone_trade_filter(
    row: dict[str, Any], *, symbol: str, price: float, expected_bar_ts: int,
) -> dict[str, Any] | None:
    import os

    if str(os.environ.get("ZONE_TRADE_FILTER", "0")).strip().lower() not in {
        "1", "true", "yes", "on"
    }:
        return row
    cached = _ZONE_CACHE.get(symbol)
    if cached is None or int(cached[0]) != int(expected_bar_ts):
        decision = {
            "accepted": False, "state": "UNKNOWN",
            "reason": "zone_cache_missing_or_stale",
        }
    else:
        decision = _zone_trade_filter_decision(row, cached[1])
    event = {
        "event": "zone_trade_filter",
        "signal_id": row.get("signal_id"),
        "symbol": symbol,
        "side": row.get("side"),
        "strategy": row.get("strategy"),
        "accepted": decision["accepted"],
        "state": decision["state"],
        "reason": decision["reason"],
        "boundary": decision.get("boundary"),
        "room_r": decision.get("room_r"),
        "target_r": decision.get("target_r"),
        "entry": row.get("entry"),
        "stop": row.get("stop"),
        "target": row.get("tp"),
        "price": price,
        "zone_bar_ts": int(expected_bar_ts),
        "zone_profile_source": _ZONE_PROFILE_META.get(symbol, {}).get("source"),
        "zone_profile_lookback_bars": _ZONE_PROFILE_META.get(symbol, {}).get("lookback_bars"),
        "zone_shadow_source": _ZONE_PROFILE_META.get(symbol, {}).get("shadow_source"),
        "ts": time.time(),
    }
    shadow_cached = _ZONE_SHADOW_CACHE.get(symbol)
    profile_meta = _ZONE_PROFILE_META.get(symbol, {})
    if (
        profile_meta.get("shadow_only")
        and shadow_cached is not None
        and int(shadow_cached[0]) == int(expected_bar_ts)
    ):
        shadow_decision = _zone_trade_filter_decision(row, shadow_cached[1])
        try:
            from astra_bot.decision.live_arm_wiring import append_journal
            append_journal({
                "event": "zone_filter_shadow_counterfactual",
                "signal_id": row.get("signal_id"),
                "symbol": symbol,
                "side": row.get("side"),
                "actual_accepted": decision["accepted"],
                "actual_reason": decision["reason"],
                "shadow_accepted": shadow_decision["accepted"],
                "shadow_reason": shadow_decision["reason"],
                "shadow_source": profile_meta.get("shadow_source"),
                "shadow_lookback_bars": profile_meta.get("shadow_lookback_bars"),
                "zone_bar_ts": int(expected_bar_ts),
                "ts": time.time(),
            })
        except Exception as exc:
            logger.warning("zone shadow counterfactual journal failed %s: %s", symbol, type(exc).__name__)
    try:
        from astra_bot.decision.live_arm_wiring import append_journal
        append_journal(event)
        _append_zone_trade_filter_event(event)
    except Exception as exc:
        logger.warning("zone trade filter journal failed %s: %s", symbol, type(exc).__name__)
        decision = {
            "accepted": False, "state": "UNKNOWN",
            "reason": "zone_filter_journal_failed",
        }
    row["zone_trade_filter"] = decision
    if not decision["accepted"]:
        logger.info(
            "arm feed: zone filter blocked %s %s state=%s reason=%s",
            symbol, row.get("side"), decision["state"], decision["reason"],
        )
        return None
    return row

async def scan_symbol(
    client: Any,
    symbol: str,
    strategy: ZeusChannelBoundaryStrategy | None = None,
) -> dict[str, Any] | None:
    strat = strategy or _channel_strategy()
    try:
        candles = await fetch_candles(client, symbol)
    except Exception as exc:
        logger.warning("arm feed: %s candles failed: %s", symbol, type(exc).__name__)
        return None
    if not candles or len(candles) < 30:
        return None
    zone_ctx = None
    try:
        from astra_bot.decision.zone_shadow import is_enabled, zone_context
        cached = _ZONE_CACHE.get(symbol)
        price_now = _candle_value(candles[-1], "close")
        if is_enabled() and cached is not None and price_now > 0:
            zone_ctx = zone_context(price_now, cached[1])
            zone_ctx["profile_source"] = _ZONE_PROFILE_META.get(symbol, {}).get("source")
            zone_ctx["profile_lookback_bars"] = _ZONE_PROFILE_META.get(symbol, {}).get("lookback_bars")
    except Exception as exc:
        logger.warning("zone shadow context failed %s: %s", symbol, type(exc).__name__)
    # Entry decisions use confirmed candles only. The forming candle is
    # retained solely as the current execution-price reference.
    series = list(candles)
    live_price = _candle_value(series[-1], "close")
    closed_series = series[:-1] if len(series) > 1 else series
    if len(closed_series) < 30 or live_price <= 0:
        return None
    bar_ts = int(getattr(closed_series[-1], "open_time", 0) or 0)
    if bar_ts <= 0:
        logger.warning("arm feed: %s closed bar/live price missing - skip", symbol)
        return None
    try:
        sig = await strat.evaluate(symbol, closed_series, current_price=live_price)
    except Exception as exc:
        logger.warning("arm feed: %s evaluate failed: %s", symbol, type(exc).__name__)
        sig = None
    if sig is not None:
        try:
            row = signal_to_row(sig, bar_ts=bar_ts)
            if zone_ctx is not None:
                row["zone_shadow"] = zone_ctx
            expected_zone_bar = int(_candle_value(candles[-2], "open_time") or 0)
            return _apply_zone_trade_filter(
                row, symbol=symbol, price=_candle_value(candles[-1], "close"),
                expected_bar_ts=expected_zone_bar,
            )
        except Exception as exc:
            logger.warning("arm feed: %s row map failed: %s", symbol, type(exc).__name__)
            return None
    # Proxy v1: ZEUS_RETEST_V1=shadow → journal only (no pending row / no live entry).
    try:
        diag = strat.diagnose(closed_series, current_price=live_price)
        shadow = (diag or {}).get("shadow_retest")
        if shadow:
            logger.info(
                "arm feed: %s shadow_retest %s mode=%s",
                symbol,
                (diag or {}).get("pattern_retest"),
                (diag or {}).get("retest_mode"),
            )
            try:
                from astra_bot.decision.live_arm_wiring import append_journal
                append_journal(
                    {
                        "event": "channel_retest_shadow",
                        "symbol": symbol,
                        "pattern": (diag or {}).get("pattern_retest"),
                        "retest_mode": (diag or {}).get("retest_mode"),
                        "side": shadow.get("side"),
                        "entry": shadow.get("entry"),
                        "stop": shadow.get("stop"),
                        "target": shadow.get("target"),
                        "bar_ts": bar_ts,
                    }
                )
            except Exception:
                pass
    except Exception as exc:
        logger.warning("arm feed: %s diagnose shadow failed: %s", symbol, type(exc).__name__)
    # Owner 30.10: multi-top/bottom breakdown (missed BTC dump after 2-3 tops).
    # Prefer 4h structure; if silent, 1h fallback catches intra-4h continuations.
    br = None
    src_tf = "4h"
    try:
        if len(closed_series) >= 30:
            highs, lows, closes = series_from_candles(closed_series)
            br = detect_breakdown_from_ohlc(
                highs, lows, closes, entry_price=live_price,
            )
    except Exception as exc:
        logger.warning("arm feed: %s breakdown 4h failed: %s", symbol, type(exc).__name__)
    if br is None:
        try:
            c1h = await fetch_candles(client, symbol, timeframe="1h", limit=120)
            if c1h and len(c1h) >= 30:
                closed_1h = list(c1h[:-1]) if len(c1h) > 1 else list(c1h)
                if len(closed_1h) >= 30:
                    h1, l1, cl1 = series_from_candles(closed_1h)
                    br = detect_breakdown_from_ohlc(
                        h1, l1, cl1,
                        entry_price=_candle_value(c1h[-1], "close"),
                    )
                    if br is not None:
                        src_tf = "1h"
                        bar_ts = int(getattr(closed_1h[-1], "open_time", 0) or bar_ts)
        except Exception as exc:
            logger.warning("arm feed: %s breakdown 1h failed: %s", symbol, type(exc).__name__)
    if br is None:
        return None
    now = time.time()
    row = {
        "signal_id": make_signal_id(symbol, br.side, bar_ts, strategy="zeus_multi_top_breakdown"),
        "symbol": symbol,
        "side": br.side,
        "entry": br.entry,
        "stop": br.stop,
        "tp": br.take_profit,
        "retest_ts": int(bar_ts),
        "ts": now,
        "strategy": "zeus_multi_top_breakdown",
        "confidence": br.confidence,
        "pattern": (br.meta or {}).get("pattern"),
        "neckline": br.neckline,
        "n_tops": br.n_tops,
        "tf": src_tf,
        "zone_shadow": zone_ctx,
    }
    expected_zone_bar = int(_candle_value(candles[-2], "open_time") or 0)
    return _apply_zone_trade_filter(
        row, symbol=symbol, price=_candle_value(candles[-1], "close"),
        expected_bar_ts=expected_zone_bar,
    )


async def produce_pending(
    client: Any,
    symbols: list[str] | tuple[str, ...] = FEED_SYMBOLS,
    *,
    path: Path = PENDING_PATH,
    seen_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    strat = _channel_strategy()
    seen = set(seen_ids or ())
    out: list[dict[str, Any]] = []
    open_syms: set[str] = set()
    try:
        from astra_bot.decision.live_positions import DEFAULT_PATH, load_positions
        for _pl in (load_positions(DEFAULT_PATH) or {}).values():
            if isinstance(_pl, dict) and _pl.get("symbol"):
                open_syms.add(str(_pl["symbol"]))
    except Exception:
        pass
    scan_outcomes: dict[str, str] = {}
    for sym in symbols:
        if sym in open_syms:
            logger.info("arm feed: skip %s - already open", sym)
            scan_outcomes[sym] = "already_open"
            continue
        row = await scan_symbol(client, sym, strategy=strat)
        if row is None:
            scan_outcomes[sym] = "no_candidate_or_zone_veto"
            continue
        sid = row["signal_id"]
        if sid in seen:
            logger.info("arm feed: skip %s - signal_id already seen", sid)
            scan_outcomes[sym] = "duplicate_signal"
            continue
        if any(r["symbol"] == row["symbol"] for r in out):
            scan_outcomes[sym] = "duplicate_symbol"
            continue
        seen.add(sid)
        out.append(row)
        scan_outcomes[sym] = "candidate"
    write_pending(out, path)
    try:
        jpath = Path("models/zeus_live_journal.jsonl")
        jpath.parent.mkdir(parents=True, exist_ok=True)
        with jpath.open("a", encoding="utf-8") as jf:
            for row in out:
                jf.write(
                    json.dumps(
                        {
                            "event": "pending_signal",
                            "signal_id": row.get("signal_id"),
                            "symbol": row.get("symbol"),
                            "side": row.get("side"),
                            "ts": time.time(),
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n"
                )
    except Exception as exc:
        logger.warning("arm feed journal: %s", type(exc).__name__)
    logger.info(
        "arm feed scan: scanned=%d candidates=%d outcomes=%s",
        len(scan_outcomes), len(out), scan_outcomes,
    )
    try:
        from astra_bot.decision.live_arm_wiring import append_journal
        append_journal({
            "event": "arm_scan_summary",
            "scanned": len(scan_outcomes),
            "candidate_count": len(out),
            "outcomes": scan_outcomes,
            "ts": time.time(),
        })
    except Exception as exc:
        logger.warning("arm scan summary journal failed: %s", type(exc).__name__)
    logger.info("arm feed: wrote %d channel candidate(s) -> %s", len(out), path)
    return out
