"""Лестница плеча по уверенности и паттерн «голова и плечи».

Пользователь: если бот УВЕРЕН, что цена пойдёт по сценарию («прям
точно»), плечо не должно ограничиваться 2x — доходить до 100x, но
ТОЛЬКО после полной уверенности. Плюс бот должен узнавать классические
голову-плечи (и обратные).

Ключевые свойства лестницы:
  - база 2x по сильному EV (прежнее поведение не ломается);
  - выше — только ступени confidence+EV (до 100x при conf>=0.95/EV>=3R);
  - стоп обязан умирать раньше ликвидации: плечо срезается до
    допустимого по ширине стопа (100x со стопом 2% = ликвидация);
  - потолок ASTRA_LEVERAGE_MAX (env) уважается всегда.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from astra_bot.decision.strategies.pattern_strategies import (
    PatternType,
    detect_head_shoulders,
)
from astra_bot.decision.trading_engine import leverage_for


def _seg(n: int, a: float, b: float) -> list[float]:
    return [a + (b - a) * (i + 1) / n for i in range(n)]


def _bars(prices: list[float], spread: float = 0.08):
    highs, lows, closes = [], [], []
    for i, v in enumerate(prices):
        nxt = prices[i + 1] if i + 1 < len(prices) else v
        prev = prices[i - 1] if i > 0 else v
        highs.append(max(v, nxt, prev) + spread)
        lows.append(min(v, nxt, prev) - spread)
        closes.append(v)
    return highs, lows, closes


class TestLeverageLadder:
    def test_base_two_x_on_strong_ev(self):
        # Слабая уверенность, но сильный EV -> базовые 2x (как раньше).
        assert leverage_for(0.10, 1.1, 100, 97, 100, 0.8) == 2

    def test_no_leverage_without_edge(self):
        assert leverage_for(0.99, 0.5, 100, 97, 100, 0.8) == 1

    def test_ladder_rungs(self):
        f = leverage_for
        assert f(0.60, 1.15, 100, 97, 100, 0.8) == 3
        assert f(0.75, 1.4, 100, 97, 100, 0.8) == 5
        assert f(0.82, 1.7, 100, 97, 100, 0.8) == 10
        assert f(0.88, 2.2, 100, 97, 100, 0.8) == 20
        assert f(0.93, 2.7, 100, 99.4, 100, 0.8) == 50

    def test_full_confidence_reaches_100(self):
        # «Прям точно»: conf >= 0.95 и EV >= 3R, стоп узкий (0.4%) —
        # ликвидация дальше стопа -> разрешены все 100x.
        lev = leverage_for(0.96, 3.5, 100, 99.6, 100, 0.8)
        assert lev == 100

    def test_wide_stop_clamps_100_to_feasible(self):
        # Та же «полная уверенность», но стоп 2%: при 100x ликвидация
        # (~0.5%) настанет РАНЬШЕ стопа -> плечо срезается до ~39x,
        # чтобы стоп умер раньше ликвидации.
        lev = leverage_for(0.96, 3.5, 100, 98, 100, 0.8)
        assert 20 <= lev < 100
        # Инвариант: 1/lev > d + mm (стоп внутри ликвидации).
        d = 0.02
        assert 1.0 / lev > d + 0.005

    def test_extremely_wide_stop_falls_back_to_1(self):
        # Стоп 40% — даже 2x небезопасно по ликвидации -> 1x.
        lev = leverage_for(0.99, 5.0, 100, 60, 100, 0.8)
        assert lev == 1

    def test_env_cap_respected(self):
        # Потолок ASTRA_LEVERAGE_MAX=5 не даёт лестнице выше 5x.
        assert leverage_for(0.99, 5.0, 100, 99.6, 5, 0.8) == 5
        assert leverage_for(0.99, 5.0, 100, 99.6, 2, 0.8) == 2
        assert leverage_for(0.99, 5.0, 100, 99.6, 1, 0.8) == 1


class TestHeadShouldersDetection:
    def test_bearish_hs_detected(self):
        prices = (
            _seg(10, 100, 102) + _seg(6, 102, 100.0)     # LS, T1
            + _seg(10, 100.0, 106) + _seg(6, 106, 100.2)  # голова, T2
            + _seg(10, 100.2, 102.3) + _seg(6, 102.3, 98.8)  # RS, пробой
        )
        highs, lows, closes = _bars(prices)
        r = detect_head_shoulders(highs, lows, closes)
        assert r.pattern == PatternType.HEAD_SHOULDERS
        assert r.confidence >= 0.6
        assert r.breakout_direction == "down"
        assert r.diagnostics["breakout"] == "down"  # close ниже шеи
        assert r.diagnostics["neckline_touches"] >= 2

    def test_bullish_inverted_hs_detected(self):
        prices = (
            _seg(10, 100, 98) + _seg(6, 98, 100.0)
            + _seg(10, 100.0, 94) + _seg(6, 94, 99.8)
            + _seg(10, 99.8, 97.7) + _seg(6, 97.7, 101.2)
        )
        highs, lows, closes = _bars(prices)
        r = detect_head_shoulders(highs, lows, closes)
        assert r.pattern == PatternType.INVERTED_HEAD_SHOULDERS
        assert r.confidence >= 0.6
        assert r.diagnostics["breakout"] == "up"

    def test_trend_is_not_hs(self):
        prices = [100 + i * 0.3 for i in range(60)]
        highs, lows, closes = _bars(prices)
        assert detect_head_shoulders(highs, lows, closes).pattern == PatternType.NONE

    def test_early_wick_break_marked(self):
        # ГП сформирована, текущий бар тенью проколол шею, но закрылся
        # выше — ранний сигнал.
        prices = (
            _seg(10, 100, 102) + _seg(6, 102, 100.0)
            + _seg(10, 100.0, 106) + _seg(6, 106, 100.2)
            + _seg(10, 100.2, 102.3) + _seg(6, 102.3, 100.6)  # над шеей
        )
        highs, lows, closes = _bars(prices)
        # Прокол тенью: последний бар тенью ПОД шеей (~100.3), close над ней.
        lows[-1] = 100.0
        r = detect_head_shoulders(highs, lows, closes)
        if r.pattern == PatternType.HEAD_SHOULDERS:
            assert r.diagnostics["breakout"] == "early_down"
        else:
            raise AssertionError("ГП должна распознаваться до подтверждения")


class TestHeadShouldersStrategy:
    def _ctx(self, prices):
        highs, lows, closes = _bars(prices)
        candles = [
            SimpleNamespace(high=h, low=lo, close=c, volume=1000.0 * (1.05 ** i))
            for i, (h, lo, c) in enumerate(zip(highs, lows, closes, strict=True))
        ]
        return SimpleNamespace(symbol="BTC-USDT", timeframe="1h", candles=candles)

    def test_short_signal_on_hs_break(self):
        import asyncio

        from astra_bot.decision.strategies.pattern_strategies import (
            HeadShouldersStrategy,
        )

        prices = (
            _seg(10, 100, 102) + _seg(6, 102, 100.0)
            + _seg(10, 100.0, 106) + _seg(6, 106, 100.2)
            + _seg(10, 100.2, 102.3) + _seg(6, 102.3, 98.8)
        )
        sig = asyncio.run(HeadShouldersStrategy().evaluate(self._ctx(prices)))
        assert sig is not None and sig.direction == "short"
        # Стоп за правое плечо, тейк — проекция головы.
        assert float(sig.stop_loss) > float(sig.entry_price)
        assert float(sig.take_profit) < float(sig.entry_price)
        assert sig.features["pattern"] == "HEAD_SHOULDERS"

    def test_long_signal_on_inverted_break(self):
        import asyncio

        from astra_bot.decision.strategies.pattern_strategies import (
            HeadShouldersStrategy,
        )

        prices = (
            _seg(10, 100, 98) + _seg(6, 98, 100.0)
            + _seg(10, 100.0, 94) + _seg(6, 94, 99.8)
            + _seg(10, 99.8, 97.7) + _seg(6, 97.7, 101.2)
        )
        sig = asyncio.run(HeadShouldersStrategy().evaluate(self._ctx(prices)))
        assert sig is not None and sig.direction == "long"
        assert float(sig.stop_loss) < float(sig.entry_price)
        assert float(sig.take_profit) > float(sig.entry_price)

    def test_no_signal_without_break(self):
        import asyncio

        from astra_bot.decision.strategies.pattern_strategies import (
            HeadShouldersStrategy,
        )

        # Голова-плечи без пробоя шеи: цена осталась над шеей.
        prices = (
            _seg(10, 100, 102) + _seg(6, 102, 100.0)
            + _seg(10, 100.0, 106) + _seg(6, 106, 100.2)
            + _seg(10, 100.2, 102.3) + _seg(6, 102.3, 101.5)
        )
        assert asyncio.run(HeadShouldersStrategy().evaluate(self._ctx(prices))) is None


class TestBrokerHighLeverage:
    def test_broker_accepts_leverage_100(self, tmp_path):
        from astra_bot.decision.broker import PaperBroker

        b = PaperBroker(
            state_path=tmp_path / "pos.json",
            trades_path=tmp_path / "trades.jsonl",
            initial_capital=Decimal("10000"),
            max_leverage=Decimal("100"),
        )
        pos = b.open_position(
            symbol="BTC-USDT", direction="long",
            entry_price=Decimal("100"), stop_loss=Decimal("99.6"),
            take_profit=Decimal("101.2"), quantity=Decimal("1"),
            leverage=100, timeframe="5m",
        )
        assert pos.leverage == 100
        # Маржа крошечная, но ликвидация впритык (0.5% + mm).
        liq = b.liquidation_price(pos.entry_price, pos.direction, pos.leverage)
        assert liq is not None and float(liq) < 99.6

    def test_broker_rejects_stop_beyond_liquidation(self, tmp_path):
        import pytest
        from astra_bot.decision.broker import PaperBroker

        b = PaperBroker(
            state_path=tmp_path / "pos.json",
            trades_path=tmp_path / "trades.jsonl",
            initial_capital=Decimal("10000"),
            max_leverage=Decimal("100"),
        )
        # Стоп 2% ниже при 100x: ликвидация ~0.5% — стоп не спасёт.
        with pytest.raises(ValueError):
            b.open_position(
                symbol="BTC-USDT", direction="long",
                entry_price=Decimal("100"), stop_loss=Decimal("98"),
                take_profit=Decimal("103"), quantity=Decimal("1"),
                leverage=100, timeframe="5m",
            )
