"""Тесты единого исполнителя плана выхода (бэклог B8 этап 2 + D1/D2/D3).

Решения владельца 11.09.2026:
  1. ОДИН тейк в зоне 2-2.3R (clamp_take_rr) — вместо лестницы 1R/1.8R/2.5R.
  2. Жёсткая иерархия: жёсткий стоп старше плана; план только подтягивает
     стоп (никогда не расширяет и не снимает).
  3. Один исполнитель для paper и бэктестера.
  D1: структурный стоп по закрытым барам, только в пользу позиции.
  D2: жёсткий потолок риска на сделку <= 3% капитала.
  D3: «стоп раньше ликвидации» сохраняется для перемещённого стопа.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from astra_bot.backtester.engine import BacktestConfig, BacktestEngine
from astra_bot.core import models
from astra_bot.decision.broker import PaperBroker
from astra_bot.decision.exit_plan import (
    ExitPlanEngine,
    apply_tighter,
    breakeven_target,
    clamp_take_rr,
    structural_tighten,
    trailing_target,
)
from astra_bot.strategies.base import BaseStrategy, Signal, StrategyConfig

TF = "5m"
STEP = 300_000


def _bar(o, h, low, c, ts: int = 0, symbol: str = "BTC-USDT"):
    return models.Candle(
        exchange="test",
        symbol=symbol,
        timeframe=TF,
        open_time=ts,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=Decimal("10"),
        quote_volume=Decimal("100"),
    )


def _series(start: float, n: int, ts0: int = 0):
    """Ровная лесенка вверх: close растёт на 0.1 за бар (низкая ATR)."""
    out = []
    px = start
    for i in range(n):
        out.append(_bar(px, px + 0.2, px - 0.2, px + 0.1, ts0 + i * STEP))
        px += 0.1
    return out


class _Ctrl:
    """Стаб ExitController: смарт-флаг + выбор гипотезы."""

    def __init__(self, smart: bool = True, plan=None):
        self.smart_default = smart
        self._plan = plan or (lambda strategy, regime: ("STATIC_TP", {}))

    def plan_for(self, strategy: str, regime: str):
        return self._plan(strategy, regime)


class _Mgr:
    def __init__(self):
        from astra_bot.decision.exit_manager import ExitManagerConfig

        self.config = ExitManagerConfig()


def _broker(tmp_path):
    return PaperBroker(
        state_path=tmp_path / "pos.json",
        trades_path=tmp_path / "trades.jsonl",
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
    )


def _open_long(broker, entry="100", stop="95", qty="1"):
    return broker.open_position(
        symbol="BTC-USDT",
        direction="long",
        entry_price=Decimal(entry),
        stop_loss=Decimal(stop),
        take_profit=Decimal("0"),
        quantity=Decimal(qty),
        strategy="test",
    )


# --------------------------------------------------------------- clamp тейк
class TestSingleTake:
    def test_low_rr_widened_to_2r(self):
        take = clamp_take_rr(Decimal(100), Decimal(95), Decimal(103), "long")
        assert take == Decimal("110")

    def test_high_rr_cut_to_23(self):
        take = clamp_take_rr(Decimal(100), Decimal(95), Decimal(120), "long")
        assert take == Decimal("111.5")

    def test_in_zone_kept(self):
        take = clamp_take_rr(Decimal(100), Decimal(95), Decimal(111), "long")
        assert take == Decimal("111")

    def test_short_mirrored(self):
        take = clamp_take_rr(Decimal(100), Decimal(105), Decimal(97), "short")
        assert take == Decimal("90")

    def test_no_take_passthrough(self):
        assert clamp_take_rr(Decimal(100), Decimal(95), Decimal(0), "long") == 0


# ------------------------------------------------- инвариант подтягивания
class TestTightenInvariant:
    def test_structural_never_widens_long(self):
        bars = [_bar(100, 100.2, 99.8, 100.1, i * STEP) for i in range(10)]
        # Свинг-кандидат ~99.5 НИЖЕ текущего стопа -> расширять нельзя, None.
        cur = Decimal("99.9")
        assert structural_tighten("long", Decimal(100), cur, bars) is None

    def test_structural_tightens_up_long(self):
        bars = [_bar(100 - 0.01 * i, 101, 96 + 0.1 * i, 100, i * STEP) for i in range(10)]
        # Окно: min low = 96.0 у последнего (i=0)... свинг поднимается со временем.
        cur = Decimal("95.0")
        cand = structural_tighten("long", Decimal(100), cur, bars)
        assert cand is not None and cand > cur

    def test_breakeven_only_forward(self):
        # long: стоп уже выше входа — БУ не откатывает его вниз.
        assert (
            breakeven_target(
                "long", Decimal("101"), Decimal(100), 2.0, 0.8, Decimal(100)
            )
            is None
        )

    def test_trailing_only_forward(self):
        assert (
            trailing_target(
                "long", Decimal("101"), Decimal("100"), None, 1.0, 2.0
            )
            is None
        )

    def test_apply_tighter_side_field(self):
        class T:  # бэктестерный Trade использует side
            side = "long"
            stop_loss = Decimal("95")

        t = T()
        assert apply_tighter(t, Decimal("97")) is True
        assert t.stop_loss == Decimal("97")
        assert apply_tighter(t, Decimal("94")) is False
        assert t.stop_loss == Decimal("97")


# --------------------------------------------- executor: стоп-правила paper
class TestExecutorStops:
    def _engine(self, smart=True):
        eng = ExitPlanEngine(_Ctrl(smart=smart), _Mgr())
        return eng

    def test_smart_breakeven_moves_stop_after_08r(self, tmp_path):
        broker = _broker(tmp_path)
        pos = _open_long(broker)
        pos.highest_price = Decimal("104")  # MFE = 0.8R
        eng = self._engine()
        bars = _series(100, 30)
        eng.adjust_stops(broker, "BTC-USDT", [pos], {TF: bars}, bars, bars[-1])
        assert pos.stop_loss >= Decimal("100")  # в нетто-БУ (fee=0 → вход)

    def test_no_smart_no_move(self, tmp_path):
        broker = _broker(tmp_path)
        pos = _open_long(broker)
        pos.highest_price = Decimal("104")
        eng = self._engine(smart=False)
        bars = _series(100, 30)
        eng.adjust_stops(broker, "BTC-USDT", [pos], {TF: bars}, bars, bars[-1])
        assert pos.stop_loss == Decimal("95")

    def test_d1_structural_on_new_closed_bar_only(self, tmp_path):
        broker = _broker(tmp_path)
        pos = _open_long(broker)
        eng = self._engine()
        bars = _series(96, 30)  # свинг вырос: min low последних 15 >> 95
        # Первый вызов: новый закрытый бар -> структурный стоп подтянут.
        eng.adjust_stops(broker, "BTC-USDT", [pos], {TF: bars}, bars, bars[-1])
        moved = pos.stop_loss
        assert moved > Decimal("95")
        # Повторный вызов на ТОМ ЖЕ закрытом баре: структурного сдвига нет
        # (план_last_bar_ts зафиксирован); трейлинг может двинуть, но не
        # структурное правило.
        pos2_stop = moved
        eng.adjust_stops(broker, "BTC-USDT", [pos], {TF: bars}, bars, bars[-1])
        assert pos.stop_loss >= pos2_stop  # только подтягивание

    def test_hypothesis_breakeven_raw_entry(self, tmp_path):
        broker = _broker(tmp_path)
        pos = _open_long(broker)
        pos.highest_price = Decimal("104")
        eng = ExitPlanEngine(
            _Ctrl(
                smart=False,
                plan=lambda s, r: ("BREAKEVEN", {"trigger_r": 0.8}),
            ),
            _Mgr(),
        )
        # Цена УБЫВАЕТ на 0.1 за бар: свинг-кандидат D1 (min low окна
        # минус буфер) остаётся НИЖЕ текущего стопа -> расширять нельзя,
        # движется только правило БУ гипотезы — в сырой вход.
        bars = [
            _bar(100 - 0.1 * i, 100.2 - 0.1 * i, 99.8 - 0.1 * i, 99.9 - 0.1 * i, i * STEP)
            for i in range(30)
        ]
        eng.adjust_stops(broker, "BTC-USDT", [pos], {TF: bars}, bars, bars[-1])
        assert pos.stop_loss == Decimal("100")  # сырой вход (fee=0)


# -------------------------------------------- executor: forced-правила paper
class TestExecutorForced:
    def test_mae_cut_closes_at_live_price(self, tmp_path):
        broker = _broker(tmp_path)
        pos = _open_long(broker, entry="100", stop="95")
        eng = ExitPlanEngine(_Ctrl(smart=True), _Mgr())
        bars = _series(100, 30)
        closed = eng.forced_closes(
            broker, "BTC-USDT", [pos], {TF: bars},
            Decimal("95.4"), Decimal("95.4"), "ANY",
        )
        assert len(closed) == 1 and closed[0].exit_reason == "mae_cut"
        assert broker.positions == []

    def test_no_mae_cut_when_smart_off(self, tmp_path):
        broker = _broker(tmp_path)
        pos = _open_long(broker, entry="100", stop="95")
        eng = ExitPlanEngine(_Ctrl(smart=False), _Mgr())
        bars = _series(100, 30)
        closed = eng.forced_closes(
            broker, "BTC-USDT", [pos], {TF: bars},
            Decimal("95.4"), Decimal("95.4"), "ANY",
        )
        assert closed == [] and len(broker.positions) == 1

    def test_hierarchy_stop_beats_plan(self, tmp_path):
        """Стоп и MAE на одном баре -> стоп старше: reason=stop_loss."""
        broker = _broker(tmp_path)
        pos = _open_long(broker, entry="100", stop="95")
        eng = ExitPlanEngine(_Ctrl(smart=True), _Mgr())
        bars = _series(100, 30)
        # 1) стоп-правила плана двигают стоп; 2) бар пробивает стоп —
        # check_exits закрывает по стопу; 3) MAE до живой позиции не доходит.
        eng.adjust_stops(broker, "BTC-USDT", [pos], {TF: bars}, bars, bars[-1])
        crash = _bar(96, 96.2, 94.5, 94.6, ts=999 * STEP)
        closed = broker.check_exits(crash)
        assert len(closed) == 1 and closed[0].exit_reason == "stop_loss"
        survivors = [p for p in broker.positions if p.symbol == "BTC-USDT"]
        forced = eng.forced_closes(
            broker, "BTC-USDT", survivors, {TF: bars},
            Decimal("94.6"), Decimal("94.6"), "ANY",
        )
        assert forced == []

    def test_max_hold_safety(self, tmp_path):
        broker = _broker(tmp_path)
        pos = _open_long(broker)
        import time as _t

        pos.opened_at = int(_t.time() * 1000) - 49 * 3600 * 1000
        eng = ExitPlanEngine(_Ctrl(smart=False), _Mgr())
        bars = _series(100, 30)
        closed = eng.forced_closes(
            broker, "BTC-USDT", [pos], {TF: bars},
            Decimal("100"), Decimal("100"), "ANY",
        )
        assert len(closed) == 1 and closed[0].exit_reason == "MAX_HOLD"

    def test_vol_expansion_safety(self, tmp_path):
        broker = _broker(tmp_path)
        pos = _open_long(broker)
        pos.timeframe = TF  # safety смотрит ТФ позиции (фолбэк 1h)
        eng = ExitPlanEngine(_Ctrl(smart=False), _Mgr())
        # 60 ровных баров + один спайк TR >= 2x медианы.
        bars = [_bar(100, 100.1, 99.9, 100, i * STEP) for i in range(60)]
        bars.append(_bar(100, 101.0, 99.0, 100.5, 60 * STEP))
        closed = eng.forced_closes(
            broker, "BTC-USDT", [pos], {TF: bars},
            Decimal("100.5"), Decimal("100.5"), "ANY",
        )
        assert len(closed) == 1 and closed[0].exit_reason == "VOL_EXPANSION"


# --------------------------------------------- единый тейк через брокера
class TestBrokerTakeLevels:
    def test_single_take_closes_full(self, tmp_path):
        broker = _broker(tmp_path)
        pos = broker.open_position(
            symbol="BTC-USDT",
            direction="long",
            entry_price=Decimal("100"),
            stop_loss=Decimal("95"),
            take_profit=Decimal("103"),
            quantity=Decimal("2"),
            strategy="test",
            take_levels=[Decimal("110")],
        )
        assert pos.tp_fractions == [1.0]
        assert pos.plan_take is None  # план крепится движком, не брокером
        hit = _bar(105, 110.5, 105, 110, ts=STEP)
        closed = broker.check_exits(hit)
        reasons = [c.exit_reason for c in closed]
        assert reasons == ["tp1"]
        assert broker.positions == []  # объём закрыт полностью
        assert closed[0].quantity == 2.0


# --------------------------------- D3: стоп раньше ликвидации после движения
class TestD3StopBeforeLiquidation:
    def test_moved_stop_stays_before_liquidation(self, tmp_path):
        broker = _broker(tmp_path)
        entry = Decimal("100")
        stop = Decimal("95")
        pos = broker.open_position(
            symbol="BTC-USDT",
            direction="long",
            entry_price=entry,
            stop_loss=stop,
            take_profit=Decimal("0"),
            quantity=Decimal("1"),
            strategy="test",
            leverage=5,  # лестница плеча допустила: стоп строго раньше ликв.
        )
        liq = broker.liquidation_price(entry, "long", Decimal(5))
        assert liq is not None and stop > liq
        # Двигаем стоп всеми правилами плана — каждый кандидат обязан
        # оставаться ВЫШЕ цены ликвидации (свойство «стоп раньше ликв.»).
        eng = ExitPlanEngine(_Ctrl(smart=True), _Mgr())
        pos.highest_price = Decimal("104")
        bars = _series(100, 30)
        eng.adjust_stops(broker, "BTC-USDT", [pos], {TF: bars}, bars, bars[-1])
        assert pos.stop_loss > liq
        # Инвариант функции подтягивания: новый стоп никогда ниже старого.
        assert pos.stop_loss >= stop

    def test_short_moved_stop_stays_below_liquidation(self, tmp_path):
        broker = _broker(tmp_path)
        entry = Decimal("100")
        stop = Decimal("105")
        pos = broker.open_position(
            symbol="BTC-USDT",
            direction="short",
            entry_price=entry,
            stop_loss=stop,
            take_profit=Decimal("0"),
            quantity=Decimal("1"),
            strategy="test",
            leverage=5,
        )
        liq = broker.liquidation_price(entry, "short", Decimal(5))
        assert liq is not None and stop < liq
        pos.lowest_price = Decimal("96")
        eng = ExitPlanEngine(_Ctrl(smart=True), _Mgr())
        bars = _series(100, 30)
        bars = [
            _bar(100 - 0.1 * i, 100.2 - 0.1 * i, 99.8 - 0.1 * i, 99.9 - 0.1 * i, i * STEP)
            for i in range(30)
        ]
        eng.adjust_stops(broker, "BTC-USDT", [pos], {TF: bars}, bars, bars[-1])
        assert pos.stop_loss < liq
        assert pos.stop_loss <= stop


# ---------------------------------------- бэктестер: тот же исполнитель
class OneShotLong(BaseStrategy):
    def __init__(self):
        super().__init__(StrategyConfig(name="one_shot_long"))
        self._fired = False

    def calculate_stop_loss(self, entry_price, candles, atr=None):
        return entry_price * Decimal("0.95")

    def calculate_take_profit(self, entry_price, stop_loss, candles):
        return entry_price * Decimal("1.10")

    def evaluate(self, symbol, candles, orderbook=None, current_price=None, market_regime=None):
        if self._fired:
            return None
        self._fired = True
        price = Decimal(str(current_price or float(candles[-1].close)))
        return Signal(
            symbol=symbol,
            strategy_name=self.name,
            direction=models.TradeDirection.LONG,
            entry_price=price,
            stop_loss=price * Decimal("0.95"),
            take_profit=price * Decimal("1.10"),
        )


def _run_bt(candles, plan_enabled=True):
    config = BacktestConfig(
        symbol="BTC/USDT",
        timeframe="1h",
        initial_capital=Decimal("1000"),
        max_open_positions=1,
        risk_config={"risk_per_trade": "0.004"},
        exit_plan_enabled=plan_enabled,
    )
    engine = BacktestEngine(config)
    engine.add_strategy("one_shot_long", OneShotLong())
    engine.load_candles(candles)
    engine.run()
    return engine


def _bt_candles(rally_then_crash: bool):
    """База + вход на баре 99; после входа — рост или управляемый обвал.

    База: стоблик 100 с ШИРОКИМИ тенями (TR ~7.3): ATR велик, поэтому
    трейлинг (hi − 2×ATR) и структурный кандидат (min low − 0.15×ATR)
    НЕ перепрыгивают исходный стоп 95 — правила плана можно наблюдать
    изолированно. Обвал: один гэп-бар с low=95.1 (стоп не задет) и
    close=95.3 (≤ −0.9R) -> срабатывает MAE_CUT, а не стоп.
    """
    out = []
    for i in range(99):
        out.append({
            "open_time": 1_700_000_000_000 + i * 3_600_000,
            "open": 100.0, "high": 102.5, "low": 95.2,
            "close": 100.0, "volume": 10.0,
        })
    # Бар входа (закрыт в семантике бэктеста): сигнал по close.
    out.append({
        "open_time": 1_700_000_000_000 + 99 * 3_600_000,
        "open": 100.0, "high": 102.5, "low": 95.2,
        "close": 100.0, "volume": 10.0,
    })
    if rally_then_crash:
        out.append({
            "open_time": 1_700_000_000_000 + 100 * 3_600_000,
            "open": 100.0, "high": 100.5, "low": 95.1,
            "close": 95.3, "volume": 10.0,
        })
        px = 95.3
        for i in range(101, 130):
            px = px * 0.995
            out.append({
                "open_time": 1_700_000_000_000 + i * 3_600_000,
                "open": px, "high": px * 1.01, "low": px * 0.99,
                "close": px, "volume": 10.0,
            })
    else:
        px = 100.0
        for i in range(100, 130):
            px = px * 1.01  # рост ~1%/бар до тейка 2R (+10%)
            out.append({
                "open_time": 1_700_000_000_000 + i * 3_600_000,
                "open": px / 1.01, "high": px * 1.01, "low": px * 0.99,
                "close": px, "volume": 10.0,
            })
    return out


class TestBacktesterPlan:
    def test_mae_cut_fires_in_backtest(self):
        eng = _run_bt(_bt_candles(rally_then_crash=True), plan_enabled=True)
        reasons = [t.exit_reason for t in eng.get_trades() if t.exit_reason]
        assert "mae_cut" in reasons

    def test_plan_disabled_keeps_legacy_stop(self):
        eng = _run_bt(_bt_candles(rally_then_crash=True), plan_enabled=False)
        reasons = [t.exit_reason for t in eng.get_trades() if t.exit_reason]
        assert "mae_cut" not in reasons
        assert "stop_loss" in reasons

    def test_single_take_in_backtest_at_2r(self):
        eng = _run_bt(_bt_candles(rally_then_crash=False), plan_enabled=True)
        opens = [t for t in eng.get_trades() if t.exit_reason]
        assert opens, "ожидается закрытая сделка"
        t = opens[0]
        # Вход ~100, стоп -5% -> R=5; тейк = 2R = entry*1.10 (RR сигнала 2.0 — в зоне).
        assert t.exit_reason == "take_profit"
        assert float(t.exit_price) == pytest.approx(float(t.entry_price) * 1.10, rel=0.01)
