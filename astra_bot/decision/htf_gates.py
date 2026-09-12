"""Живые направлЯющие гейты кандидатов на дневной ленте EMA (пункт 7).

Решение владельца (12.09): это ЖИВЫЕ фильтры — режут кандидатов, не
тень (тень была только у D5, её модуль не тронут).

7.4 Лента дневных EMA 20/50/100/200 своего символа: лонг разрешён при
    ``close > EMA20`` и полном выстраивании ``EMA20 > EMA50 > EMA100 >
    EMA200``; шорт зеркально; нейтральная лента режет обе стороны.
    Правило — как ``feature_engine._trend_alignment``, но по четырём
    линиям и на дневках.
7.5 Фильтр «по BTC» для альтов: лонг альта только если BTC НЕ в полном
    медвежьем стэке (``EMA20<EMA50<EMA100<EMA200`` и ``close<EMA20``);
    шорт зеркально. Нейтральный/недоступный BTC пропускает. Не
    дублирует ``candidate_filters`` (BTC.D / корреляция / корзина —
    другие данные и механики).

Список гейтуемых бакетов — в конфиге (``htf_gate_strategies``), не
константой: расширение на остальные фигуры/стратегии — отдельное
решение владельца по данным ``HTF_GATE`` через 2 недели.

Применяется ТОЛЬКО к новым стратегиям набора (``ob_swing``,
``breaker_block``, ``maicross``) и Д-фигурам (``rounded_top``,
``rounded_bottom``). Fail-open: ``< htf_gate_min_bars`` закрытых
дневных баров (молодые листинги не блокируем), ошибка ЕМА, нет
свечей — кандидаты проходят, событие в лог. Каждая строка решения
несёт ключевое слово ``HTF_GATE`` (им владелец ищет в логах).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..adapters.bingx.client import BingXClient
from ..core.utils import exponential_moving_average

logger = logging.getLogger(__name__)

# Лента дневных гейтов (пункт 7.4). Периоды не ослабляются.
GATE_EMA_FAST = 20
GATE_EMA_MID = 50
GATE_EMA_MID2 = 100
GATE_EMA_SLOW = 200

_lazy_bingx_client: BingXClient | None = None
_btc_daily_cache: dict[str, Any] = {"timestamp": 0.0, "closes": None}


async def _get_bingx() -> BingXClient:
    """Ленивый клиент — тот же паттерн, что в candidate_filters."""
    global _lazy_bingx_client
    if _lazy_bingx_client is None or _lazy_bingx_client._session is None:
        _lazy_bingx_client = BingXClient({})
        await _lazy_bingx_client.initialize()
    return _lazy_bingx_client


def ribbon_bias(
    closes: list[float],
    *,
    min_bars: int = 200,
) -> tuple[str | None, dict[str, Any]]:
    """Выстраивание дневной ленты 20/50/100/200 по ЗАКРЫТЫМ барам.

    Возвращает ``(bias, diag)``: ``"bull"`` — полный бычий стэк и цена
    над EMA20; ``"bear"`` — зеркально; ``"neutral"`` — лента не
    выстроена; ``None`` — fail-open (мало баров/ошибка ЕМА).
    """
    diag: dict[str, Any] = {"bars": len(closes), "min_bars": min_bars}
    if len(closes) < min_bars:
        diag["reason"] = "fail_open_bars"
        return None, diag
    e_fast = exponential_moving_average(closes, GATE_EMA_FAST)
    e_mid = exponential_moving_average(closes, GATE_EMA_MID)
    e_mid2 = exponential_moving_average(closes, GATE_EMA_MID2)
    e_slow = exponential_moving_average(closes, GATE_EMA_SLOW)
    if None in (e_fast, e_mid, e_mid2, e_slow):
        diag["reason"] = "fail_open_ema"
        return None, diag
    last = closes[-1]
    if e_fast > e_mid > e_mid2 > e_slow and last > e_fast:
        return "bull", diag
    if e_fast < e_mid < e_mid2 < e_slow and last < e_fast:
        return "bear", diag
    return "neutral", diag


async def _btc_daily_closes(config) -> list[float] | None:
    """Дневные закрытия BTC (кэш на ``htf_gate_btc_cache_ttl`` секунд)."""
    now = time.time()
    ttl = float(getattr(config, "htf_gate_btc_cache_ttl", 900))
    cached = _btc_daily_cache.get("closes")
    if cached is not None and now - float(_btc_daily_cache.get("timestamp", 0.0)) < ttl:
        return cached
    try:
        client = await _get_bingx()
        candles = await client.get_candles(
            getattr(config, "htf_gate_btc_symbol", "BTC-USDT"),
            timeframe=getattr(config, "htf_gate_tf", "1d"),
            limit=getattr(config, "htf_gate_daily_bars", 500),
        )
        closed = list(candles[:-1]) if candles else []
        closes = [float(c.close) for c in closed]
        if closes:
            _btc_daily_cache["timestamp"] = now
            _btc_daily_cache["closes"] = closes
            return closes
    except Exception as exc:  # fail-open: нет свечей — фильтр молчит
        logger.info("HTF_GATE пропуск BTC-фильтра: дневные свечи недоступны (%s)", exc)
    return None


async def apply_htf_gates(
    candidates: list,
    ctx: Any,
    config: Any,
) -> list:
    """Гейт кандидатов: лента дневных своего символа (7.4) + «по BTC» (7.5).

    Кандидаты стратегий вне ``config.htf_gate_strategies`` проходят без
    изменений; любая ошибка — кандидаты проходят (альфа fail-open).
    """
    if not candidates or not getattr(config, "htf_gate_enabled", False):
        return candidates
    gated_names = set(getattr(config, "htf_gate_strategies", frozenset()))
    if not gated_names or not any(c.strategy in gated_names for c in candidates):
        return candidates

    daily = ctx.candles_on(getattr(config, "htf_gate_tf", "1d")) or []
    closed = list(daily[:-1]) if daily else []
    closes = [float(c.close) for c in closed]
    min_bars = int(getattr(config, "htf_gate_min_bars", 200))
    bias, diag = ribbon_bias(closes, min_bars=min_bars)
    if bias is None:
        logger.info(
            "HTF_GATE symbol=%s пропуск всех кандидатов: закрытых дневных баров %s < %s (fail-open)",
            ctx.symbol,
            diag.get("bars"),
            min_bars,
        )
        return candidates

    btc_symbol = getattr(config, "htf_gate_btc_symbol", "BTC-USDT")
    need_btc = ctx.symbol != btc_symbol
    btc_bias: str | None = None
    btc_loaded = False

    kept: list = []
    for c in candidates:
        if c.strategy not in gated_names:
            kept.append(c)
            continue
        side = c.direction if c.direction in ("long", "short") else "long"
        cut_reason: str | None = None
        # 7.4 Лента дневных своего символа.
        if bias == "neutral":
            cut_reason = "дневная лента не выстроена"
        elif bias == "bull" and side != "long":
            cut_reason = "дневная лента бычья, шорт против"
        elif bias == "bear" and side != "short":
            cut_reason = "дневная лента медвежья, лонг против"
        # 7.5 «по BTC» — только для альтов и только против ПОЛНОГО стэка.
        if cut_reason is None and need_btc:
            if not btc_loaded:
                btc_loaded = True
                btc_closes = await _btc_daily_closes(config)
                btc_bias = ribbon_bias(btc_closes, min_bars=min_bars)[0] if btc_closes else None
            if btc_bias == "bear" and side == "long":
                cut_reason = "BTC в полном медвежьем стэке дневных"
            elif btc_bias == "bull" and side == "short":
                cut_reason = "BTC в полном бычьем стэке дневных"
        if cut_reason is not None:
            logger.info(
                "HTF_GATE symbol=%s strategy=%s side=%s срезан: %s (лента=%s, btc=%s)",
                ctx.symbol,
                c.strategy,
                side,
                cut_reason,
                bias,
                btc_bias if need_btc else "n/a",
            )
            continue
        kept.append(c)
    return kept
