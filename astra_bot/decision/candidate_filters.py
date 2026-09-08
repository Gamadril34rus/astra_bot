"""
ASTRA BOT — Candidate Filters (Block 4).

12a. btc_dominance_filter — BTC.D с CoinGecko /global (кэш 1ч, timeout 10s).
12b. btc_correlation_filter — rolling 100-bar 1h correlation с BTC.
12c. basket_momentum_filter — ROC14 по корзине BTC/ETH/BNB/SOL/XRP (кэш 15м).

Весь хук должен быть устойчив к ошибкам (try/except): при любой ошибке
кандидаты проходят без изменений.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp
import numpy as np

from ..adapters.bingx.client import BingXClient
from ..core.utils import exponential_moving_average
from .context import SignalCandidate

logger = logging.getLogger(__name__)

_lazy_bingx_client: BingXClient | None = None

_btcd_cache: dict[str, Any] = {"timestamp": 0.0, "history": []}
_candles_cache: dict[str, tuple[float, list[float]]] = {}
_basket_cache: dict[str, Any] = {"timestamp": 0.0, "pos_count": 0, "neg_count": 0}


async def _get_bingx() -> BingXClient:
    global _lazy_bingx_client
    if _lazy_bingx_client is None or _lazy_bingx_client._session is None:
        _lazy_bingx_client = BingXClient({})
        await _lazy_bingx_client.initialize()
    return _lazy_bingx_client


async def _get_btc_dominance() -> tuple[float | None, float | None]:
    now = time.time()
    history = _btcd_cache.get("history", [])
    history = [x for x in history if now - x[0] <= 86400]
    _btcd_cache["history"] = history

    last_ts = _btcd_cache.get("timestamp", 0.0)
    curr_val = history[-1][1] if history else None

    if now - last_ts >= 3600 or curr_val is None:
        try:
            async with aiohttp.ClientSession() as session, session.get(
                "https://api.coingecko.com/api/v3/global",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    val = data.get("data", {}).get("market_cap_percentage", {}).get("btc")
                    if val is not None:
                        curr_val = float(val)
                        history.append((now, curr_val))
                        _btcd_cache["timestamp"] = now
                        _btcd_cache["history"] = history
        except Exception as exc:
            logger.debug("CoinGecko fetch failed: %s", exc)

    if curr_val is None:
        return None, None

    val_4h_ago = None
    for ts, val in reversed(history):
        if 3.5 * 3600 <= (now - ts) <= 5.0 * 3600:
            val_4h_ago = val
            break

    change_4h = (curr_val - val_4h_ago) if val_4h_ago is not None else 0.0
    return curr_val, change_4h


async def _get_1h_closes(symbol: str, limit: int = 100, ttl: int = 900) -> list[float]:
    now = time.time()
    cached = _candles_cache.get(symbol)
    if cached and (now - cached[0] < ttl):
        return cached[1]

    try:
        bingx = await _get_bingx()
        candles = await bingx.get_candles(symbol, timeframe="1h", limit=limit)
        if candles:
            closes = [float(c.close) for c in candles]
            _candles_cache[symbol] = (now, closes)
            return closes
    except Exception as exc:
        logger.debug("Failed to fetch 1h closes for %s: %s", symbol, exc)

    return cached[1] if cached else []


async def btc_dominance_filter(candidates: list[SignalCandidate]) -> list[SignalCandidate]:
    try:
        _curr_d, change_4h = await _get_btc_dominance()
        if change_4h is None:
            return candidates

        for cand in candidates:
            cand_dir = str(cand.direction).lower()
            if "BTC" not in cand.symbol.upper():
                if cand_dir == "long":
                    if change_4h > 0.5:
                        cand.confidence = max(0.1, cand.confidence - 0.10)
                    elif change_4h < -0.1:
                        cand.confidence = min(0.95, cand.confidence + 0.05)
    except Exception as exc:
        logger.debug("btc_dominance_filter error: %s", exc)
    return candidates


async def btc_correlation_filter(candidates: list[SignalCandidate]) -> list[SignalCandidate]:
    try:
        btc_closes = await _get_1h_closes("BTC-USDT", limit=100)
        if len(btc_closes) < 55:
            return candidates

        e21 = exponential_moving_average(btc_closes[-21:], 21)
        e55 = exponential_moving_average(btc_closes[-55:], 55)
        btc_downtrend = bool(e21 and e55 and e21 < e55)

        filtered: list[SignalCandidate] = []
        for cand in candidates:
            cand_dir = str(cand.direction).lower()
            if "BTC" in cand.symbol.upper() or cand_dir != "long":
                filtered.append(cand)
                continue

            symbol_closes = await _get_1h_closes(cand.symbol, limit=100)
            if len(symbol_closes) < 50 or len(symbol_closes) != len(btc_closes):
                filtered.append(cand)
                continue

            corr = float(np.corrcoef(symbol_closes, btc_closes)[0, 1])
            if corr > 0.85 and btc_downtrend:
                continue
            filtered.append(cand)

        return filtered
    except Exception as exc:
        logger.debug("btc_correlation_filter error: %s", exc)
        return candidates


async def basket_momentum_filter(candidates: list[SignalCandidate]) -> list[SignalCandidate]:
    try:
        now = time.time()
        last_ts = _basket_cache.get("timestamp", 0.0)
        pos_count = _basket_cache.get("pos_count", 0)
        neg_count = _basket_cache.get("neg_count", 0)

        if now - last_ts >= 900:
            basket = ["BTC-USDT", "ETH-USDT", "BNB-USDT", "SOL-USDT", "XRP-USDT"]
            pos = 0
            neg = 0
            for sym in basket:
                closes = await _get_1h_closes(sym, limit=20)
                if len(closes) >= 15:
                    roc14 = (closes[-1] - closes[-15]) / closes[-15]
                    if roc14 > 0:
                        pos += 1
                    elif roc14 < 0:
                        neg += 1
            pos_count = pos
            neg_count = neg
            _basket_cache.update({"timestamp": now, "pos_count": pos, "neg_count": neg})

        for cand in candidates:
            cand_dir = str(cand.direction).lower()
            if pos_count >= 4:
                if cand_dir == "long":
                    cand.confidence = min(0.95, cand.confidence + 0.05)
            elif neg_count >= 4:
                if cand_dir == "short":
                    cand.confidence = min(0.95, cand.confidence + 0.05)
                elif cand_dir == "long":
                    cand.confidence = max(0.1, cand.confidence - 0.05)
    except Exception as exc:
        logger.debug("basket_momentum_filter error: %s", exc)
    return candidates


async def apply_candidate_filters(candidates: list[SignalCandidate]) -> list[SignalCandidate]:
    if not candidates:
        return candidates

    try:
        candidates = await btc_dominance_filter(candidates)
        candidates = await btc_correlation_filter(candidates)
        candidates = await basket_momentum_filter(candidates)
    except Exception as exc:
        logger.warning("apply_candidate_filters error (graceful fallback): %s", exc)

    return candidates
