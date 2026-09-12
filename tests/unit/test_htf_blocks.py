"""Тесты набора индикаторов владельца (п.7): об/брейкер/маикросс + дневные гейты.

Ключевые проверки по поправкам владельца (12.09):
* ОДНОРАЗОВОСТЬ: повторное касание зоны молчит; кросс двухдневной
  давности молчит; первое касание и свежий кросс — сигнал;
* дневной ряд подаётся стратегиям явно (preferred_timeframe="1d");
* гейты: лента дневных режет против направления, нейтраль режет обе
  стороны, фейл-оупен на короткой истории, «по BTC» — только против
  полного стэка и только для альтов.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from astra_bot.core import models
from astra_bot.decision.config import DecisionConfig
from astra_bot.decision.htf_gates import apply_htf_gates, ribbon_bias
from astra_bot.decision.strategies.pattern_strategies import _volume_ok
from astra_bot.strategies.htf_blocks import (
    BreakerBlockStrategy,
    MaiCrossStrategy,
    ObSwingStrategy,
    build_zones,
    find_swing_pivots,
    walk_zone,
)

LOOKBACK = 10


def _candle(o, h, l, c, v=100.0, i=0):
    return models.Candle(
        exchange="test",
        symbol="TEST-USDT",
        timeframe="1d",
        open_time=i * 86_400_000,
        open=Decimal(str(round(o, 6))),
        high=Decimal(str(round(h, 6))),
        low=Decimal(str(round(l, 6))),
        close=Decimal(str(round(c, 6))),
        volume=Decimal(str(v)),
        quote_volume=Decimal("0"),
    )


def _candles_from(rows):
    """rows: список (open, high, low, close[, volume])."""
    out = []
    for i, r in enumerate(rows):
        o, h, l, c = r[:4]
        v = r[4] if len(r) > 4 else 100.0
        out.append(_candle(o, h, l, c, v=v, i=i))
    return out


# ---------------------------------------------------------------------------
# Фикстуры дневных баров
# ---------------------------------------------------------------------------


def _bull_ob_rows():
    """Бычий OB на баре 10 (пивот-лой), импульс вверх, ретест последним баром.

    Зона = свеча пивота целиком: низ 100.0, верх 102.5.
    """
    rows = []
    # Бары 0..9: монотонный спуск к пивот-лою (низы выше 100.0!).
    for i in range(LOOKBACK):
        base = 121.0 - 2.0 * (i + 1)
        rows.append((base + 0.8, base + 1.2, base - 0.3, base + 0.2))
    # Бар 10 — пивот-лой: свеча зоны (полный диапазон 100.0..102.5).
    rows.append((102.0, 102.5, 100.0, 100.6))
    # Бары 11..30: импульс вверх (выход из зоны и рост до ~116).
    for i in range(1, 21):
        base = 100.5 + 0.75 * i
        rows.append((base - 0.4, base + 0.5, base - 0.7, base + 0.3))
    # Бары 31..44: монотонная консолидация НАД зоной (без касаний).
    for i in range(14):
        base = 116.0 + 0.05 * i
        rows.append((base, base + 0.4, base - 0.2, base + 0.2))
    return rows


def _bear_ob_rows():
    """Медвежий OB на баре 10 (пивот-хай, ТОНКАЯ зона 119.2..120.0), спуск, ретест снизу."""
    rows = []
    for i in range(LOOKBACK):
        base = 100.0 + 1.9 * (i + 1)
        rows.append((base - 0.8, base + 0.3, base - 1.2, base - 0.2))
    # Бар 10 — пивот-хай: зона = свеча целиком (119.2..120.0).
    rows.append((119.9, 120.0, 119.2, 119.3))
    # Импульс вниз до ~104.
    for i in range(1, 21):
        base = 119.5 - 0.75 * i
        rows.append((base + 0.4, base + 0.7, base - 0.5, base - 0.3))
    # Консолидация ПОД зоной.
    for i in range(14):
        base = 104.0 - 0.05 * i
        rows.append((base, base + 0.2, base - 0.4, base - 0.2))
    return rows


def _maicross_closes(extra_flat_bars: int = 0):
    """Ряд закрытий (92 бара): пик 120, спуск к 100, полка 64 бара,
    подъём 8×0.6 — кросс ЕМА20/50 ИМЕННО на последнем закрытом баре
    (подобрано численно). Пик остаётся в 90-баровом окне структурных
    целей. ``extra_flat_bars`` добавляет «прошедшие дни» после кросса —
    для теста несвежего кросса.
    """
    closes = [120.0] * 5
    for i in range(15):
        closes.append(120.0 - (20.0 / 15.0) * (i + 1))
    closes += [100.0] * 64
    closes += [100.0 + 0.6 * (i + 1) for i in range(8)]
    for _ in range(extra_flat_bars):
        closes.append(closes[-1])
    return closes


def _candles_from_closes(closes, vol_last=300.0):
    rows = []
    for i, c in enumerate(closes):
        v = vol_last if i == len(closes) - 1 else 100.0
        rows.append((c - 0.1, c + 0.2, c - 0.2, c, v))
    return _candles_from(rows)


# ---------------------------------------------------------------------------
# 7.1 ob_swing
# ---------------------------------------------------------------------------


class TestObSwing:
    def test_first_touch_gives_long(self):
        rows = _bull_ob_rows()
        # Последний бар: первое касание зоны сверху (низ входит в зону,
        # закрытие над её дальней границей) + объёмное подтверждение.
        rows.append((106.0, 106.5, 102.0, 103.5, 300.0))
        candles = _candles_from(rows)
        sig = asyncio.run(ObSwingStrategy().evaluate("TEST-USDT", candles, current_price=103.5))
        assert sig is not None, "первое касание живой зоны должно дать сигнал"
        assert sig.direction == models.TradeDirection.LONG
        assert float(sig.stop_loss) < 100.0  # за дальней границей зоны с буфером
        assert float(sig.take_profit) == pytest.approx(117.05, abs=0.1)  # структурный хай после зоны
        assert sig.risk_reward_ratio >= 1.5

    def test_second_touch_is_silent(self):
        rows = _bull_ob_rows()
        rows.append((106.0, 106.5, 102.0, 103.5, 300.0))  # первое касание
        rows.append((104.0, 104.5, 101.8, 103.2, 300.0))  # повторное касание
        candles = _candles_from(rows)
        sig = asyncio.run(ObSwingStrategy().evaluate("TEST-USDT", candles, current_price=103.2))
        assert sig is None, "зона после первого касания отработана — тишина"

    def test_bearish_first_touch_gives_short(self):
        rows = _bear_ob_rows()
        # Ретест медвежьей зоны снизу: хай входит в зону, закрытие у нижней
        # границы (реалистичная цена входа у края зоны).
        rows.append((106.0, 119.5, 105.6, 118.8, 300.0))
        candles = _candles_from(rows)
        sig = asyncio.run(ObSwingStrategy().evaluate("TEST-USDT", candles, current_price=118.8))
        assert sig is not None
        assert sig.direction == models.TradeDirection.SHORT
        assert float(sig.stop_loss) > 120.0  # за верхней границей зоны
        assert float(sig.take_profit) < 105.0  # структурный лой импульса
        assert sig.risk_reward_ratio >= 1.5

    def test_no_touch_is_silent(self):
        rows = _bull_ob_rows()
        rows.append((116.4, 116.8, 116.2, 116.6, 300.0))  # цена вдали от зоны
        candles = _candles_from(rows)
        assert asyncio.run(ObSwingStrategy().evaluate("TEST-USDT", candles, current_price=116.6)) is None

    def test_volume_gate_blocks_signal(self):
        rows = _bull_ob_rows()
        rows.append((106.0, 106.5, 102.0, 103.5, 5.0))  # тихий объём
        candles = _candles_from(rows)
        assert _volume_ok([float(c.volume) for c in candles], 1.2) is False
        assert asyncio.run(ObSwingStrategy().evaluate("TEST-USDT", candles, current_price=103.5)) is None


# ---------------------------------------------------------------------------
# 7.2 breaker_block
# ---------------------------------------------------------------------------


def _breaker_rows(extra_bars=()):
    """Бычий OB сломан закрытием, затем откат к зоне снизу (ретест брейкера)."""
    rows = _bull_ob_rows()
    # Бар 45: закрытие НИЖЕ дальней границы зоны (100.0) — митигация.
    rows.append((103.0, 103.2, 98.5, 98.9))
    # Бары после слома: держатся под зоной, структурный лой ~94.
    rows.append((98.9, 99.4, 96.0, 96.5))
    rows.append((96.5, 96.8, 94.0, 94.6))
    for r in extra_bars:
        rows.append(r)
    return rows


class TestBreakerBlock:
    def test_retest_of_broken_block_gives_short(self):
        rows = _breaker_rows()
        # Последний бар: первое касание сломанной зоны СНИЗУ
        # (хай входит в зону, закрытие под её верхней границей).
        rows.append((95.0, 100.8, 94.8, 99.5, 300.0))
        candles = _candles_from(rows)
        sig = asyncio.run(BreakerBlockStrategy().evaluate("TEST-USDT", candles, current_price=99.5))
        assert sig is not None, "первое касание брейкера снизу — шорт"
        assert sig.direction == models.TradeDirection.SHORT
        assert float(sig.stop_loss) > 102.5  # стоп над зоной
        assert float(sig.take_profit) == pytest.approx(94.0, abs=0.1)  # структурный лой после слома
        assert sig.risk_reward_ratio >= 1.5

    def test_second_breaker_touch_is_silent(self):
        rows = _breaker_rows()
        rows.append((95.0, 100.8, 94.8, 99.5, 300.0))  # первое касание
        rows.append((99.0, 101.2, 98.6, 100.5, 300.0))  # повторное
        candles = _candles_from(rows)
        assert asyncio.run(BreakerBlockStrategy().evaluate("TEST-USDT", candles, current_price=100.5)) is None

    def test_reclaim_kills_breaker(self):
        rows = _breaker_rows()
        rows.append((95.0, 100.8, 94.8, 99.5, 300.0))
        # Возврат закрытия ВЫШЕ зоны — полярность не сработала, зона гаснет.
        rows.append((99.5, 104.0, 99.2, 103.6, 300.0))
        rows.append((103.6, 103.8, 100.4, 101.0, 300.0))  # касание после возврата
        candles = _candles_from(rows)
        assert asyncio.run(BreakerBlockStrategy().evaluate("TEST-USDT", candles, current_price=101.0)) is None

    def test_unmitigated_zone_gives_no_breaker(self):
        rows = _bull_ob_rows()
        rows.append((106.0, 106.5, 102.0, 103.5, 300.0))
        candles = _candles_from(rows)
        assert asyncio.run(BreakerBlockStrategy().evaluate("TEST-USDT", candles, current_price=103.5)) is None


# ---------------------------------------------------------------------------
# 7.3 maicross
# ---------------------------------------------------------------------------


class TestMaiCross:
    def test_fresh_cross_on_last_bar_gives_long(self):
        closes = _maicross_closes()
        candles = _candles_from_closes(closes)
        sig = asyncio.run(MaiCrossStrategy().evaluate("TEST-USDT", candles, current_price=closes[-1]))
        assert sig is not None, "кросс на последнем закрытом баре — сигнал"
        assert sig.direction == models.TradeDirection.LONG
        assert sig.risk_reward_ratio >= 1.5

    def test_stale_cross_is_silent(self):
        closes = _maicross_closes(extra_flat_bars=2)
        candles = _candles_from_closes(closes)
        assert asyncio.run(MaiCrossStrategy().evaluate("TEST-USDT", candles, current_price=closes[-1])) is None, (
            "кросс двухдневной давности — тишина"
        )

    def test_no_cross_is_silent(self):
        closes = [100.0] * 100  # полка: разница ЕМА всегда 0 — кросса нет
        candles = _candles_from_closes(closes)
        assert asyncio.run(MaiCrossStrategy().evaluate("TEST-USDT", candles, current_price=100.0)) is None

    def test_short_history_is_silent(self):
        candles = _candles_from_closes([100.0] * 30)
        assert asyncio.run(MaiCrossStrategy().evaluate("TEST-USDT", candles, current_price=100.0)) is None


# ---------------------------------------------------------------------------
# Структурные инварианты (поправка 2 владельца)
# ---------------------------------------------------------------------------


class TestInvariants:
    def test_preferred_timeframe_is_daily(self):
        for cls in (ObSwingStrategy, BreakerBlockStrategy, MaiCrossStrategy):
            assert cls.preferred_timeframe == "1d"

    def test_pivot_no_repaint(self):
        highs = [10.0] * 30
        lows = [9.0] * 30
        highs[20] = 20.0  # потенциальный пивот-хай; правая сторона не полна
        ph, _ = find_swing_pivots(highs, lows, LOOKBACK)
        assert 20 not in ph, "пивот без полной правой стороны не подтверждается"
        highs2 = list(highs) + [10.0] * LOOKBACK  # дорастает справа
        ph2, _ = find_swing_pivots(highs2, lows + [9.0] * LOOKBACK, LOOKBACK)
        assert 20 in ph2

    def test_zone_max_per_side_and_merge(self):
        highs = [10.0] * 80
        lows = [9.0] * 80
        # Четыре пивот-лоя: два перекрываются, два нет.
        for i, px in ((25, 5.0), (27, 5.5), (60, 4.0), (62, 3.5)):
            lows[i] = px
        zones = build_zones(highs, lows, LOOKBACK, max_per_side=3)
        bulls = [z for z in zones if z.side == "bull"]
        assert len(bulls) <= 3
        assert any(z.formed_idx == 25 for z in bulls), "перекрытие сливается в раннюю зону"

    def test_walk_starts_after_confirmation(self):
        # Импульс из зоны в барах между пивотом и подтверждением —
        # НЕ касание: зона «живёт» с бара подтверждения.
        rows = _bull_ob_rows()
        rows.append((106.0, 106.5, 102.0, 103.5, 300.0))
        candles = _candles_from(rows)
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        closes = [float(c.close) for c in candles]
        zones = build_zones(highs, lows, LOOKBACK)
        bull = next(z for z in zones if z.side == "bull")
        first_touch, broken, _bt, dead = walk_zone(bull, highs, lows, closes, LOOKBACK)
        assert not dead and broken is None
        assert first_touch == len(candles) - 1, "касанием считается только бар после подтверждения зоны"


# ---------------------------------------------------------------------------
# 7.4/7.5 Дневные гейты
# ---------------------------------------------------------------------------


def _gate_ctx(symbol: str, closes: list[float]):
    candles = [SimpleNamespace(close=Decimal(str(c))) for c in closes]

    def candles_on(tf):
        # Гейты всегда считают по ЗАКРЫТЫМ барам: последняя свеча
        # (формирующаяся) отбрасывается внутри модуля — отдаём сырой ряд
        # с «живой» последней свечой, как это делает фид.
        return [*candles, SimpleNamespace(close=Decimal(str(closes[-1])))] if candles else []

    return SimpleNamespace(symbol=symbol, candles_on=candles_on)


def _candidate(strategy: str, direction: str, symbol: str = "ETH-USDT"):
    from astra_bot.decision.context import SignalCandidate

    return SignalCandidate(
        symbol=symbol,
        direction=direction,
        entry_price=Decimal("100"),
        stop_loss=Decimal("98"),
        take_profit=Decimal("104"),
        timeframe="5m",
        strategy=strategy,
    )


BULL_CLOSES = [100.0 + 0.5 * i for i in range(300)]
BEAR_CLOSES = [250.0 - 0.5 * i for i in range(300)]
NEUTRAL_CLOSES = [150.0 + (8.0 if (i // 7) % 2 == 0 else -8.0) for i in range(300)]


class TestRibbonBias:
    def test_bull_bear_neutral_failopen(self):
        assert ribbon_bias(BULL_CLOSES)[0] == "bull"
        assert ribbon_bias(BEAR_CLOSES)[0] == "bear"
        assert ribbon_bias(NEUTRAL_CLOSES)[0] == "neutral"
        bias, diag = ribbon_bias(BULL_CLOSES[:50])
        assert bias is None and diag["reason"] == "fail_open_bars"


class TestHtfGates:
    def _run(self, candidates, ctx, config=None):
        return asyncio.run(apply_htf_gates(candidates, ctx, config or DecisionConfig()))

    def test_bull_ribbon_cuts_short_keeps_long(self):
        ctx = _gate_ctx("BTC-USDT", BULL_CLOSES)
        kept = self._run([_candidate("ob_swing", "long", "BTC-USDT"), _candidate("ob_swing", "short", "BTC-USDT")], ctx)
        assert [c.direction for c in kept] == ["long"]

    def test_bear_ribbon_cuts_long_keeps_short(self):
        ctx = _gate_ctx("BTC-USDT", BEAR_CLOSES)
        kept = self._run([_candidate("rounded_top", "long", "BTC-USDT"), _candidate("rounded_top", "short", "BTC-USDT")], ctx)
        assert [c.direction for c in kept] == ["short"]

    def test_neutral_ribbon_cuts_both(self):
        ctx = _gate_ctx("BTC-USDT", NEUTRAL_CLOSES)
        kept = self._run([_candidate("maicross", "long", "BTC-USDT"), _candidate("maicross", "short", "BTC-USDT")], ctx)
        assert kept == []

    def test_failopen_short_history_passes_all(self):
        ctx = _gate_ctx("BTC-USDT", BULL_CLOSES[:50])
        cands = [_candidate("ob_swing", "long", "BTC-USDT"), _candidate("ob_swing", "short", "BTC-USDT")]
        assert self._run(cands, ctx) == cands

    def test_ungated_strategies_pass(self):
        ctx = _gate_ctx("BTC-USDT", BEAR_CLOSES)
        kept = self._run([_candidate("order_block", "long", "BTC-USDT")], ctx)
        assert len(kept) == 1, "старые бакеты гейт не трогает"

    def test_gate_disabled_passes_all(self):
        cfg = DecisionConfig(htf_gate_enabled=False)
        ctx = _gate_ctx("BTC-USDT", BEAR_CLOSES)
        assert len(self._run([_candidate("ob_swing", "long", "BTC-USDT")], ctx, cfg)) == 1

    def test_btc_full_bear_stack_cuts_alt_long(self, monkeypatch):
        import astra_bot.decision.htf_gates as g

        async def fake_btc(config):
            return BEAR_CLOSES[:-1]

        monkeypatch.setattr(g, "_btc_daily_closes", fake_btc)
        # Своя лента альта бычья — без BTC-фильтра лонг бы прошёл.
        ctx = _gate_ctx("ETH-USDT", BULL_CLOSES)
        kept = self._run([_candidate("ob_swing", "long")], ctx)
        assert kept == [], "альт-лонг против полного медвежьего стэка BTC срезается"

    def test_btc_neutral_passes_alt_long(self, monkeypatch):
        import astra_bot.decision.htf_gates as g

        async def fake_btc(config):
            return NEUTRAL_CLOSES[:-1]

        monkeypatch.setattr(g, "_btc_daily_closes", fake_btc)
        ctx = _gate_ctx("ETH-USDT", BULL_CLOSES)
        kept = self._run([_candidate("ob_swing", "long")], ctx)
        assert len(kept) == 1

    def test_btc_full_bull_stack_cuts_alt_short(self, monkeypatch):
        import astra_bot.decision.htf_gates as g

        async def fake_btc(config):
            return BULL_CLOSES[:-1]

        monkeypatch.setattr(g, "_btc_daily_closes", fake_btc)
        ctx = _gate_ctx("ETH-USDT", BEAR_CLOSES)
        kept = self._run([_candidate("breaker_block", "short")], ctx)
        assert kept == []

    def test_btc_gate_skipped_for_btc_itself(self, monkeypatch):
        import astra_bot.decision.htf_gates as g

        async def boom(config):
            raise AssertionError("для BTC-символа BTC-фильтр вызываться не должен")

        monkeypatch.setattr(g, "_btc_daily_closes", boom)
        ctx = _gate_ctx("BTC-USDT", BULL_CLOSES)
        kept = self._run([_candidate("ob_swing", "long", "BTC-USDT")], ctx)
        assert len(kept) == 1

    def test_btc_candles_unavailable_failopen(self, monkeypatch):
        import astra_bot.decision.htf_gates as g

        async def fake_btc(config):
            return None

        monkeypatch.setattr(g, "_btc_daily_closes", fake_btc)
        ctx = _gate_ctx("ETH-USDT", BULL_CLOSES)
        kept = self._run([_candidate("ob_swing", "long")], ctx)
        assert len(kept) == 1, "нет свечей BTC — фейл-оупен"
