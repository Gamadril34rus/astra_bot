#!/usr/bin/env python3
"""Opt-in, one-shot BingX public-candle capture. Never run during PR preparation."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = "https://open-api.bingx.com"
KLINES = "/openApi/swap/v3/quote/klines"
SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
MAX_CALLS_PER_RUN = 10


def output_dir(raw: str) -> Path:
    if not raw:
        raise ValueError("--data-dir required")
    path = Path(raw).expanduser().resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError("output must be outside repository")
    return path


async def get_public(session, path, params):
    async with session.get(BASE + path, params=params) as response:
        body = await response.json()
        received = int(time.time() * 1000)
        if (
            response.status != 200
            or not isinstance(body, dict)
            or body.get("code", 0) not in (0, "0", None)
        ):
            code = body.get("code") if isinstance(body, dict) else "invalid"
            raise RuntimeError(f"public GET failed: http={response.status}, code={code}")
        return body.get("data"), received, response.headers.get("Date")


def parse_candles(raw, symbol, timeframe):
    from astra_bot.core.models import Candle

    if isinstance(raw, dict):
        raw = raw.get("klines", raw.get("data", []))
    output = []
    for row in raw or []:
        try:
            if isinstance(row, dict):
                values = (
                    row.get("time") or row.get("openTime"),
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row["volume"],
                    row.get("quoteVolume", 0),
                )
            else:
                values = (
                    row[0], row[1], row[2], row[3], row[4], row[5],
                    row[7] if len(row) > 7 else 0,
                )
            output.append(
                Candle(
                    exchange="bingx",
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time=int(values[0]),
                    open=Decimal(str(values[1])),
                    high=Decimal(str(values[2])),
                    low=Decimal(str(values[3])),
                    close=Decimal(str(values[4])),
                    volume=Decimal(str(values[5])),
                    quote_volume=Decimal(str(values[6])),
                )
            )
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return sorted(output, key=lambda candle: candle.open_time)


def channel_config():
    # Frozen copy of live_arm_feed.paper_channel_config() from source blob b57d27cf.
    # It is the active arm-feed channel config, not research_wrap.channel_capital_config().
    from astra_bot.strategies.zeus_channel_boundary import ZeusChannelBoundaryConfig

    return ZeusChannelBoundaryConfig(
        enabled=True,
        lookback=48,
        min_width_pct=0.01,
        max_width_pct=0.12,
        near_boundary_width_frac=0.20,
        use_parallel_clone=True,
        parallel_tol=0.25,
        min_rr=2.0,
        enable_breakout_hold=True,
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


def candidate_from_channel(signal, symbol, bar_ts):
    if signal is None:
        return None
    side = str(
        getattr(
            getattr(signal, "direction", None),
            "value",
            getattr(signal, "direction", ""),
        )
    ).lower()
    return {
        "signal_id": f"zeus_channel_boundary|{symbol}|{side}|{bar_ts}",
        "symbol": symbol,
        "side": side,
        "strategy": "zeus_channel_boundary",
        "entry_reference": float(signal.entry_price),
        "stop_reference": float(signal.stop_loss),
        "target_reference": (
            float(signal.take_profit) if signal.take_profit is not None else None
        ),
        "confidence": float(signal.confidence),
        "timeframe": "4h",
        "formed_at_ms": int(bar_ts),
    }


def candidate_from_breakdown(signal, symbol, timeframe, bar_ts):
    if signal is None:
        return None
    return {
        "signal_id": f"zeus_multi_top_breakdown|{symbol}|{signal.side}|{bar_ts}",
        "symbol": symbol,
        "side": signal.side,
        "strategy": "zeus_multi_top_breakdown",
        "entry_reference": signal.entry,
        "stop_reference": signal.stop,
        "target_reference": signal.take_profit,
        "confidence": signal.confidence,
        "timeframe": timeframe,
        "formed_at_ms": int(bar_ts),
    }


def select_baseline_signal(channel, multi_4h, multi_1h):
    """Mirror scan_symbol precedence: channel, else 4h multi, else 1h multi."""
    return channel or multi_4h or multi_1h


async def capture_symbol(session, symbol):
    from astra_bot.decision.zone_shadow import build_volume_zones
    from astra_bot.research.isolated_shadow import Signal as ShadowSignal
    from astra_bot.research.isolated_shadow import (
        classify_zone,
        variant_decision,
    )
    from astra_bot.strategies.zeus_channel_boundary import ZeusChannelBoundaryStrategy
    from astra_bot.strategies.zeus_multi_top_breakdown import (
        detect_breakdown_from_ohlc,
        series_from_candles,
    )

    raw4, recv4, date4 = await get_public(
        session, KLINES, {"symbol": symbol, "interval": "4h", "limit": 120}
    )
    bars4 = parse_candles(raw4, symbol, "4h")
    if len(bars4) < 48:
        raise RuntimeError("insufficient_4h_history")
    bars1 = []
    recv1 = None
    date1 = None
    request_count = 1
    # live_arm_feed.scan_symbol evaluates the current (possibly forming) 4h bar.
    price = float(bars4[-1].close)
    zones = build_volume_zones(bars4, price=price)
    zone_bar = bars4[-2].open_time

    channel_strategy = ZeusChannelBoundaryStrategy(channel_config())
    channel_signal = await channel_strategy.evaluate(symbol, bars4)
    channel = candidate_from_channel(channel_signal, symbol, bars4[-1].open_time)

    # The multi detector is a fallback, never a parallel alternative to channel.
    multi_4h = None
    multi_1h = None
    if channel is None:
        highs, lows, closes = series_from_candles(bars4)
        multi_signal = detect_breakdown_from_ohlc(highs, lows, closes)
        multi_4h = candidate_from_breakdown(
            multi_signal, symbol, "4h", bars4[-1].open_time
        )
        if multi_4h is None:
            raw1, recv1, date1 = await get_public(
                session, KLINES, {"symbol": symbol, "interval": "1h", "limit": 120}
            )
            request_count += 1
            bars1 = parse_candles(raw1, symbol, "1h")
            if len(bars1) >= 30:
                h1, l1, cl1 = series_from_candles(bars1)
                multi_signal = detect_breakdown_from_ohlc(h1, l1, cl1)
                multi_1h = candidate_from_breakdown(
                    multi_signal, symbol, "1h", bars1[-1].open_time
                )

    # Decision time is recorded only after every source needed for this candidate
    # has arrived. A conditional 1h fallback must not appear future-dated.
    decision_at = int(time.time() * 1000)

    # This one source event is fanned out to all variants. Variant B may reject it;
    # it is forbidden to re-run a detector or substitute another candidate.
    baseline = select_baseline_signal(channel, multi_4h, multi_1h)
    annotated = None
    decisions = {}
    if baseline is not None:
        market_received = max(recv4, recv1) if recv1 is not None else recv4
        shadow_signal = ShadowSignal(
            signal_id=baseline["signal_id"],
            symbol=symbol,
            side=baseline["side"],
            strategy=baseline["strategy"],
            formed_at_ms=baseline["formed_at_ms"],
            market_received_at_ms=market_received,
            zone_received_at_ms=recv4,
            source_closed_bar_ms=zone_bar,
            decision_at_ms=decision_at,
            predecision_price=price,
            price_source="BingX public 4h forming-candle close",
            entry_reference=baseline["entry_reference"],
            stop_reference=baseline["stop_reference"],
            target_reference=baseline["target_reference"],
            confidence=baseline["confidence"],
            timeframe=baseline["timeframe"],
        )
        zone_decision = classify_zone(
            side=shadow_signal.side,
            price=price,
            zones=zones,
            market_received_at_ms=market_received,
            zone_received_at_ms=recv4,
            decision_at_ms=decision_at,
        )
        for variant in ("A", "B", "C"):
            result = variant_decision(variant, shadow_signal, zone_decision)
            decisions[variant] = {
                "accepted": result.accepted_by_variant,
                "reason": result.reason,
                "zone_state": zone_decision.state.value,
                "boundary": zone_decision.boundary,
                "distance_pct": zone_decision.distance_pct,
            }
        annotated = {**baseline, "variants": decisions}

    return {
        "symbol": symbol,
        "market_received_at_ms": max(recv4, recv1) if recv1 is not None else recv4,
        "zone_received_at_ms": recv4,
        "decision_at_ms": decision_at,
        "predecision_price": price,
        "price_source": "BingX public 4h forming-candle close",
        "source_closed_bar_ms": zone_bar,
        "zone_source": "closed 4h candles via build_volume_zones",
        "zones": zones,
        "channel_candidate": channel,
        "multi_top_4h_fallback_candidate": multi_4h,
        "multi_top_1h_fallback_candidate": multi_1h,
        "baseline_signal": annotated,
        "variant_signals": {
            variant: annotated for variant in ("A", "B", "C")
        } if annotated is not None else {"A": None, "B": None, "C": None},
        "request_count": request_count,
        "server_date_headers": [date for date in (date4, date1) if date is not None],
    }


async def run(args):
    if os.environ.get("BINGX_API_KEY") or os.environ.get("BINGX_API_SECRET"):
        raise RuntimeError("refusing: BingX trading credentials present in environment")
    destination = output_dir(args.data_dir)
    destination.mkdir(parents=True, exist_ok=True)
    timeout = aiohttp.ClientTimeout(total=15)
    connector = aiohttp.TCPConnector(limit=2)
    async with aiohttp.ClientSession(
        timeout=timeout,
        connector=connector,
        headers={"User-Agent": "ASTRA-isolated-shadow/0.2"},
    ) as session:
        rows = []
        for symbol in SYMBOLS:
            rows.append(await capture_symbol(session, symbol))
    count = sum(row["request_count"] for row in rows)
    if count > MAX_CALLS_PER_RUN:
        raise RuntimeError("public request budget exceeded")
    event = {
        "schema_version": 2,
        "capture_id": f"capture-{int(time.time() * 1000)}",
        "captured_at_ms": int(time.time() * 1000),
        "request_count": count,
        "snapshots": rows,
    }
    path = destination / "market_capture.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "capture_id": event["capture_id"],
                "symbols": len(rows),
                "requests": count,
                "output": str(path),
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
