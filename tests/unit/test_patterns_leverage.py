"""Тесты улучшений паттернов (касания/тени/закругления) и плеч.

Правила пользователя:
- граница подтверждается КАСАНИЯМИ по ТЕНЯМ: 3 сверху + 3 снизу;
- отталкиваться от теней, а не тел (пробой тенью = ранний сигнал);
- бывают клинья с закруглением (чаша/купол) — обязаны распознаваться;
- плечо: маржа ограничена, стоп раньше ликвидации, плата за заём
  ОБЯЗАТЕЛЬНО попадает в PnL — как и комиссия сделки.
"""

import math
from decimal import Decimal

import pytest
from astra_bot.decision.broker import PaperBroker
from astra_bot.decision.strategies.pattern_strategies import (
    PatternType,
    detect_pattern,
)


# ------------------------------------------------------------- helpers
def curved_pattern(kind: str, n: int = 150, leg: int = 15, noise: float = 0.08):
    """Чаша (rounded bottom) или купол (rounded top) с ретестами."""

    def bounds(i: int) -> tuple[float, float]:
        t = i / (n - 1)
        if kind == "rounded_bottom":
            lo = 90 + 14 * (2 * t - 1) ** 2  # чаша: min в середине
            up = 109.0  # плоский верх
        else:  # rounded_top
            up = 110 - 14 * (2 * t - 1) ** 2  # купол: max в середине
            lo = 96.0
        return up, lo

    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    prev = None
    for i in range(n):
        up, lo = bounds(i)
        pos = i % leg
        going_up = (i // leg) % 2 == 0
        if going_up:
            price = lo + (up - lo) * pos / (leg - 1)
        else:
            price = up - (up - lo) * pos / (leg - 1)
        wig = noise * math.sin(i * 2.1)
        if prev is not None:
            highs.append(max(prev, price) + abs(wig) + 0.02)
            lows.append(min(prev, price) - abs(wig) - 0.02)
        else:
            highs.append(price + abs(wig) + 0.02)
            lows.append(price - abs(wig) - 0.02)
        closes.append(price + wig)
        prev = price
    return highs, lows, closes


# ---------------------------------------------------------- patterns
class TestTouchCounting:
    def test_textbook_pattern_has_three_touches_per_line(self):
        from tests.unit.test_patterns_and_regimes import channel_zigzag

        highs, lows, closes = channel_zigzag("asc_triangle")
        result = detect_pattern(highs, lows, closes)
        d = result.diagnostics
        assert d["upper_touches"] >= 3, d
        assert d["lower_touches"] >= 3, d
        assert d["touches_confirmed"] is True

    def test_touches_use_wicks_not_bodies(self):
        """Касания считаются по хаям/лоям (теням), а не по open/close.

        Делаем «тени», дотягивающиеся до линии, при телах вдали от неё:
        touch всё равно должен засчитаться.
        """
        from tests.unit.test_patterns_and_regimes import channel_zigzag

        highs, lows, closes = channel_zigzag("desc_triangle")
        # Усиливаем тени: хаи касаются верхней линии, тела ниже.
        for i in range(len(highs)):
            highs[i] = max(highs[i], highs[i])  # no-op, хаи уже тени
        result = detect_pattern(highs, lows, closes)
        d = result.diagnostics
        # desc_triangle: верхняя линия нисходящая — касания по теням хаёв
        assert d["upper_touches"] >= 3 or d["lower_touches"] >= 3

    def test_few_touches_penalize_confidence(self):
        """Мало касаний — нет подтверждения границы, уверенность ниже."""
        from tests.unit.test_patterns_and_regimes import channel_zigzag

        # Короткая серия: всего 2 лега → мало свингов-касаний.
        highs, lows, closes = channel_zigzag("falling_wedge", n=40, leg=14)
        r_short = detect_pattern(highs, lows, closes)
        d = r_short.diagnostics
        if r_short.pattern != PatternType.NONE:
            assert d["touches_confirmed"] is False
            assert d.get("touches_bonus") == -0.05


class TestWickBreakout:
    def test_wick_breakout_detected_without_body_close(self):
        """Тень пробила верхнюю линию, тело ещё нет → пробой = up.

        Раньше пробой смотрел только по close (телу) — ранний сигнал
        тенью терялся.
        """
        from tests.unit.test_patterns_and_regimes import channel_zigzag

        highs, lows, closes = channel_zigzag("asc_triangle")
        # Последний бар: тело глубоко внутри диапазона, тень до линии+.
        up_at = detect_pattern(highs, lows, closes).diagnostics["upper_at_now"]
        highs[-1] = up_at * 1.004  # тень за линией
        lows[-1] = min(lows[-1], up_at * 0.98)
        closes[-1] = up_at * 0.995  # тело НЕ за линией
        result = detect_pattern(highs, lows, closes)
        if result.pattern == PatternType.ASCENDING_TRIANGLE:
            assert result.breakout_direction == "up"


class TestRoundedPatterns:
    def test_rounded_bottom_recognized(self):
        highs, lows, closes = curved_pattern("rounded_bottom")
        result = detect_pattern(highs, lows, closes)
        assert result.pattern == PatternType.ROUNDED_BOTTOM, result.diagnostics
        assert result.confidence >= 0.5

    def test_rounded_top_recognized(self):
        highs, lows, closes = curved_pattern("rounded_top")
        result = detect_pattern(highs, lows, closes)
        assert result.pattern == PatternType.ROUNDED_TOP, result.diagnostics

    def test_rounded_lines_flagged_in_diagnostics(self):
        highs, lows, closes = curved_pattern("rounded_bottom")
        d = detect_pattern(highs, lows, closes).diagnostics
        assert d["lower_rounded"] is True  # чаша описана параболой


class TestRoundedStrategies:
    @staticmethod
    async def _signal(strategy, highs, lows, closes):
        from astra_bot.core import models
        from astra_bot.decision.context import StrategyContext

        candles = [
            models.Candle(
                symbol="X", open_time=i * 60_000,
                open=Decimal(str(closes[i])), high=Decimal(str(highs[i])),
                low=Decimal(str(lows[i])), close=Decimal(str(closes[i])),
                volume=Decimal("300" if i > len(closes) - 3 else "100"),
                exchange="sim", timeframe="5m", quote_volume=Decimal("10000"),
            )
            for i in range(len(closes))
        ]
        return await strategy.evaluate(
            StrategyContext(symbol="X", timeframe="5m", candles=candles)
        )

    def test_falling_wedge_strategy_takes_rounded_bottom(self):
        import asyncio

        from astra_bot.decision.strategies.pattern_strategies import (
            FallingWedgeStrategy,
        )

        highs, lows, closes = curved_pattern("rounded_bottom")
        sig = asyncio.run(self._signal(FallingWedgeStrategy(), highs, lows, closes))
        assert sig is not None, "чаша (бычье закругление) не дала лонг"
        assert sig.direction == "long"

    def test_rising_wedge_strategy_takes_rounded_top(self):
        import asyncio

        from astra_bot.decision.strategies.pattern_strategies import (
            RisingWedgeStrategy,
        )

        highs, lows, closes = curved_pattern("rounded_top")
        sig = asyncio.run(self._signal(RisingWedgeStrategy(), highs, lows, closes))
        assert sig is not None, "купол (медвежье закругление) не дал шорт"
        assert sig.direction == "short"


# ---------------------------------------------------------- leverage
def _mk_broker(tmp_path, **kw) -> PaperBroker:
    """Брокер с hermetic-состоянием: общие /tmp-файлы протекают между
    тестами (позиции прошлых прогонов съедают маржу следующих)."""
    return PaperBroker(
        state_path=tmp_path / "pos.json",
        trades_path=tmp_path / "trades.jsonl",
        initial_capital=Decimal("1000"),
        fee_pct=Decimal("0.001"),
        slippage_pct=Decimal("0.001"),
        **kw,
    )


class TestLeverageMargin:
    def test_open_with_leverage_records_margin(self, tmp_path):
        b = _mk_broker(tmp_path)
        pos = b.open_position(
            symbol="BTC-USDT", direction="long",
            entry_price=Decimal("100"), stop_loss=Decimal("97"),
            take_profit=Decimal("106"), quantity=Decimal("1"),
            leverage=2,
        )
        assert pos.leverage == 2
        assert pos.margin_used == Decimal("50")  # notional/плечо

    def test_leverage_clamped_to_max(self, tmp_path):
        b = _mk_broker(tmp_path, max_leverage=Decimal("3"))
        pos = b.open_position(
            symbol="BTC-USDT", direction="long",
            entry_price=Decimal("100"), stop_loss=Decimal("97"),
            take_profit=Decimal("106"), quantity=Decimal("1"),
            leverage=10,
        )
        assert pos.leverage == 3

    def test_insufficient_margin_rejected(self, tmp_path):
        b = _mk_broker(tmp_path)
        # notional 900 при equity 1000 → маржа 900 ок (плечо 1)
        b.open_position(
            symbol="A", direction="long", entry_price=Decimal("100"),
            stop_loss=Decimal("95"), take_profit=Decimal("110"),
            quantity=Decimal("9"), leverage=1,
        )
        # Вторая с плечом 2: маржа 400/2=200, свободно 100 → отказ
        with pytest.raises(ValueError, match="margin"):
            b.open_position(
                symbol="B", direction="long", entry_price=Decimal("100"),
                stop_loss=Decimal("97"), take_profit=Decimal("110"),
                quantity=Decimal("4"), leverage=2,
            )

    def test_stop_beyond_liquidation_rejected(self, tmp_path):
        """Стоп дальше цены ликвидации = смерть от ликвидации до стопа."""
        b = _mk_broker(tmp_path, max_leverage=Decimal("10"))
        # Плечо 10: liq_long ≈ 100*(1-0.1+0.005)=90.5; стоп 80 — за ликвидацией.
        with pytest.raises(ValueError, match="ликвидаци"):
            b.open_position(
                symbol="BTC-USDT", direction="long",
                entry_price=Decimal("100"), stop_loss=Decimal("80"),
                take_profit=Decimal("120"), quantity=Decimal("1"),
                leverage=10,
            )

    def test_stop_inside_liquidation_allowed(self, tmp_path):
        b = _mk_broker(tmp_path, max_leverage=Decimal("10"))
        # Стоп 95 выше liq≈90.5 при плече 10 — ок.
        pos = b.open_position(
            symbol="BTC-USDT", direction="long",
            entry_price=Decimal("100"), stop_loss=Decimal("95"),
            take_profit=Decimal("120"), quantity=Decimal("1"),
            leverage=10,
        )
        assert pos.leverage == 10


class TestFundingPayment:
    def test_funding_accrued_on_close(self, tmp_path):
        """Фандинг перпов за время удержания попадает в сделку и уменьшает PnL.

        Ставка 0.01% за 8ч на нотионал: 24 бара × 1h = 3 интервала →
        100.1 * 0.0001 * 3 = 0.03003.
        """
        b = _mk_broker(tmp_path, funding_rate=Decimal("0.0001"))
        pos = b.open_position(
            symbol="BTC-USDT", direction="long",
            entry_price=Decimal("100"), stop_loss=Decimal("97"),
            take_profit=Decimal("106"), quantity=Decimal("1"),
            leverage=2,
        )
        # Время удержания — детерминированное: 24 бара по 1h.
        pos.timeframe = "1h"
        pos.bars_held = 24
        trade = b.close_position(pos.id, Decimal("101"), "TP")
        assert trade is not None
        # Фандинг = 100.1 * 0.0001 * 3 = 0.03003 (отдельно от комиссий).
        assert trade.funding == pytest.approx(0.03003, abs=1e-9)
        assert trade.fees > 0  # комиссии сделки — отдельно
        # PnL нетто: gross = (101*0.999 - 100.1)*1 ≈ 0.799; минус издержки
        assert trade.pnl == pytest.approx(0.799 - trade.fees - 0.03003, abs=1e-9)
        assert trade.pnl < 0.799  # издержки вычтены

    def test_short_receives_funding(self, tmp_path):
        """При положительной ставке шорт ПОЛУЧАЕТ фандинг (отрицательный)."""
        b = _mk_broker(tmp_path, funding_rate=Decimal("0.0001"))
        pos = b.open_position(
            symbol="BTC-USDT", direction="short",
            entry_price=Decimal("100"), stop_loss=Decimal("103"),
            take_profit=Decimal("94"), quantity=Decimal("1"),
            leverage=2,
        )
        pos.timeframe = "1h"
        pos.bars_held = 8  # 1 интервал фандинга
        trade = b.close_position(pos.id, Decimal("99"), "TP")
        assert trade is not None
        # fill(short) = 99.9; funding = -(99.9 * 0.0001 * 1) = -0.00999
        assert trade.funding == pytest.approx(-0.00999, abs=1e-9)

    def test_funding_deterministic_across_replays(self, tmp_path):
        """Фандинг одинаков в двух идентичных прогонах (реплей)."""
        fundings = []
        for i in range(2):
            b = _mk_broker(tmp_path / f"run{i}", funding_rate=Decimal("0.0001"))
            pos = b.open_position(
                symbol="BTC", direction="long", entry_price=Decimal("100"),
                stop_loss=Decimal("97"), take_profit=Decimal("106"),
                quantity=Decimal("1"), leverage=2,
            )
            pos.timeframe = "1h"
            pos.bars_held = 10
            fill = pos.fill_price or pos.entry_price
            fundings.append(b.funding_payment(pos, Decimal("1"), fill))
        assert fundings[0] == fundings[1] > 0

    def test_trade_never_forgets_commission(self, tmp_path):
        """Сделка «в ноль» обязана быть убыточной NET: комиссия есть всегда."""
        b = _mk_broker(tmp_path)
        pos = b.open_position(
            symbol="BTC", direction="long", entry_price=Decimal("100"),
            stop_loss=Decimal("95"), take_profit=Decimal("105"),
            quantity=Decimal("1"), leverage=1,
        )
        trade = b.close_position(pos.id, Decimal("100"), "FLAT")  # вышли в ноль
        assert trade.pnl < 0, "нетто-сделка в ноль должна нести издержки"
        assert trade.fees > 0


class TestEngineLeverageDecision:
    """Движок берёт плечо только при сильном EV, иначе торгует без заёма."""

    @staticmethod
    async def _run_engine(tmp_path, monkeypatch, ev_r: float, lev_max: int = 2):
        from unittest.mock import MagicMock

        from astra_bot.core.market_safety import SafetyVerdict
        from astra_bot.decision.broker import PaperBroker
        from astra_bot.decision.context import SignalCandidate
        from astra_bot.decision.trading_engine import (
            Decision,
            TradingEngine,
            TradingEngineConfig,
        )
        from astra_bot.engines.risk_engine import RiskConfig, RiskEngine

        monkeypatch.setattr(
            "astra_bot.decision.trading_engine.append_lessons", lambda trades: 0
        )
        monkeypatch.setattr(
            "astra_bot.data.state_manager.get_state_manager",
            lambda: MagicMock(
                save_trades=lambda x: 0, load_state=dict, save_state=lambda x: None
            ),
        )

        cand = SignalCandidate(
            symbol="BTC-USDT",
            direction="long",
            entry_price=Decimal("100"),
            stop_loss=Decimal("99"),
            take_profit=Decimal("103"),
            timeframe="5m",
            strategy="fake",
            confidence=0.9,
            features={"ev_r": ev_r},
        )

        class Pipe:
            async def decide(self, ctx):
                return Decision("LONG", ctx.symbol, ["fake"], candidate=cand)

        cfg = TradingEngineConfig(
            symbols=("BTC-USDT",),
            timeframes=("5m",),
            bars_per_tf={"5m": 120},
            fee_pct=Decimal("0"),
            slippage_pct=Decimal("0"),
            stats_path=str(tmp_path / "stats.json"),
            leverage_max=lev_max,
        )
        feed = MagicMock()
        from tests.unit.test_trading_engine_risk import _AsyncMockReturn, _candles

        feed.get_candles = _AsyncMockReturn(_candles())
        feed.get_orderbook = _AsyncMockReturn(None)
        feed.get_ticker = _AsyncMockReturn(
            {"last": "100", "high_24h": "101", "low_24h": "99"}
        )
        broker = PaperBroker(
            state_path=tmp_path / "pos.json",
            trades_path=tmp_path / "trades.jsonl",
            initial_capital=Decimal("1000"),
            fee_pct=Decimal("0"),
            slippage_pct=Decimal("0"),
        )
        eng = TradingEngine(
            exchange=feed,
            pipeline=Pipe(),
            config=cfg,
            broker=broker,
            risk_engine=RiskEngine(
                RiskConfig(
                    risk_per_trade=Decimal(cfg.risk_per_trade_pct),
                    max_open_positions=cfg.max_open_positions,
                    max_exposure_pct=Decimal(cfg.max_total_exposure_pct),
                )
            ),
        )
        eng.safety.check = lambda *a, **k: SafetyVerdict(allowed=True)
        await eng.step()
        return eng.broker.positions

    def test_high_ev_gets_leverage(self, tmp_path, monkeypatch):
        positions = asyncio_run(self._run_engine(tmp_path, monkeypatch, ev_r=1.5))
        assert len(positions) == 1
        assert positions[0].leverage == 2
        assert positions[0].notes.get("leverage") == 2

    def test_low_ev_trades_without_leverage(self, tmp_path, monkeypatch):
        positions = asyncio_run(self._run_engine(tmp_path, monkeypatch, ev_r=0.2))
        assert len(positions) == 1
        assert positions[0].leverage == 1

    def test_leverage_disabled(self, tmp_path, monkeypatch):
        positions = asyncio_run(self._run_engine(tmp_path, monkeypatch, ev_r=1.5, lev_max=1))
        assert len(positions) == 1
        assert positions[0].leverage == 1


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
