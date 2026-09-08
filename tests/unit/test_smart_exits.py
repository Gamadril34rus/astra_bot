"""Умные выходы («отторговка») и структурные стопы.

Пользователь: с плечом можно заработать больше, но и потерять больше.
Значит бот должен: (1) грамотно ставить стопы — за свинг по теням;
(2) если цена пошла не туда — отторговываться: нетто-безубыток по MFE,
трейлинг, ранний срез (MAE_CUT) и выход при развороте режима против
позиции. С плечом пороги срезаются раньше.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from astra_bot.decision.broker import PaperBroker
from astra_bot.decision.exit_controller import (
    ExitController,
    structural_stop,
)


@dataclass
class Bar:
    open: float
    high: float
    low: float
    close: float
    volume: float = 1000.0
    symbol: str = "BTC-USDT"


def _mk_broker(tmp_path, **kw) -> PaperBroker:
    defaults = dict(
        initial_capital=Decimal("10000"),
        state_path=Path(tmp_path) / "broker_state.json",
    )
    defaults.update(kw)
    return PaperBroker(**defaults)


def _open_long(b, entry="100", stop="97", qty="1", lev=1):
    return b.open_position(
        symbol="BTC-USDT", direction="long",
        entry_price=Decimal(entry), stop_loss=Decimal(stop),
        take_profit=Decimal("1000"), quantity=Decimal(qty),
        leverage=lev, timeframe="1h",
    )


class _NoHyp:
    def for_strategy(self, _):
        return []


def _bars_down(n, start=100.0, step=0.9):
    """n медвежьих баров: close уходит вниз от start."""
    out = []
    c = start
    for _ in range(n):
        o = c
        c = c - step
        out.append(Bar(open=o, high=o + 0.2, low=min(o, c) - 0.2, close=c))
    return out


def _bars_up(n, start=100.0, step=0.9):
    out = []
    c = start
    for _ in range(n):
        o = c
        c = c + step
        out.append(Bar(open=o, high=max(o, c) + 0.2, low=o - 0.2, close=c))
    return out


class TestStructuralStop:
    def test_widens_inside_noise_long(self):
        # Стратегия поставила стоп 98 (2% ниже входа), а свинг-лоу по теням
        # 97.4 — стоп выносится за свинг с буфером.
        bars = [Bar(open=100, high=100.6, low=97.4, close=99.5)]
        bars += [Bar(open=99.5, high=100.2, low=98.6, close=99.8) for _ in range(14)]
        stop = structural_stop(Decimal("100"), Decimal("98"), "long", bars)
        assert float(stop) < 97.4  # за тенью свинга
        assert float(stop) > 96.0  # но в разумных пределах

    def test_keeps_structural_stop_unchanged(self):
        # Стоп уже за свингом — не трогаем.
        bars = [Bar(open=100, high=100.6, low=97.4, close=99.5)]
        bars += [Bar(open=99.5, high=100.2, low=98.6, close=99.8) for _ in range(14)]
        stop = structural_stop(Decimal("100"), Decimal("96.5"), "long", bars)
        assert float(stop) == 96.5

    def test_refuses_explosion_beyond_2r(self):
        # Свинг далеко: расширение больше 2.2R запрещено (R-бюджет).
        bars = [Bar(open=100, high=100.6, low=90.0, close=99.5)]
        bars += [Bar(open=99.5, high=100.2, low=98.6, close=99.8) for _ in range(14)]
        stop = structural_stop(Decimal("100"), Decimal("99"), "long", bars)
        assert float(stop) == 99.0  # слишком далеко — оставили как было

    def test_short_symmetric(self):
        bars = [Bar(open=100, high=102.6, low=99.4, close=100.5)]
        bars += [Bar(open=100.5, high=101.4, low=99.8, close=100.2) for _ in range(14)]
        stop = structural_stop(Decimal("100"), Decimal("102"), "short", bars)
        assert float(stop) > 102.6


class TestMaeCut:
    def test_cuts_early_before_full_stop(self, tmp_path):
        b = _mk_broker(tmp_path)
        pos = _open_long(b, entry="100", stop="97")  # R=3, стоп = -1R
        bars = _bars_down(12)
        ec = ExitController(_NoHyp(), smart_default=True)
        # Контроллер работает на КАЖДОМ баре — срез должен случиться на
        # первом баре глубже -0.9R (close <= 97.3), РАНЬШЕ стопа 97.
        trade = None
        for bar in bars:
            trade = ec._apply_smart(b, pos, bar, bars[: bars.index(bar) + 1], "RANGE")
            if trade is not None:
                break
        assert trade is not None
        assert trade.exit_reason == "mae_cut"
        # Средний убыток МЕНЬШЕ полного стопа: ~-0.9R, а не -1R.
        assert -1.1 < trade.r_multiple <= -0.8, trade.r_multiple
        assert pos not in b.positions

    def test_leverage_tightens_cut(self, tmp_path):
        # close = 97.9 → (97.9-100)/3 = -0.7R: без плеча держим (порог 0.9),
        # с плечом 2x порог 0.675 → срезаем.
        bars = _bars_down(3, start=100.0, step=0.7)  # close=97.9
        last = bars[-1]
        for lev, expect_cut in ((1, False), (2, True)):
            b = _mk_broker(tmp_path / f"lev{lev}")
            pos = _open_long(b, entry="100", stop="97", lev=lev)
            ec = ExitController(_NoHyp(), smart_default=True)
            trade = ec._apply_smart(b, pos, last, bars, "RANGE")
            assert (trade is not None) == expect_cut, f"lev={lev}"

    def test_no_cut_when_in_profit(self, tmp_path):
        b = _mk_broker(tmp_path)
        pos = _open_long(b)
        bars = _bars_up(8)
        ec = ExitController(_NoHyp(), smart_default=True)
        assert ec._apply_smart(b, pos, bars[-1], bars, "BULL") is None


class TestSmartBreakevenAndTrailing:
    def test_net_breakeven_above_entry(self, tmp_path):
        # MFE достиг 1R → стоп переносится в НЕТТО-безубыток: выше входа
        # (комиссии + плата за плечо не превращают БУ в минус).
        for lev in (1, 2):
            b = _mk_broker(tmp_path / f"be{lev}")
            pos = _open_long(b, entry="100", stop="97", lev=lev)
            pos.highest_price = 103.0  # +1R
            pos.bars_held = 5
            bars = _bars_up(10)
            ec = ExitController(_NoHyp(), smart_default=True)
            assert ec._apply_smart(b, pos, bars[-1], bars, "BULL") is None
            assert float(pos.stop_loss) > 100.0, f"lev={lev}"

    def test_trailing_locks_profit(self, tmp_path):
        b = _mk_broker(tmp_path)
        pos = _open_long(b, entry="100", stop="97")
        # Цена выросла до 106, экстремум запомнен, затем бар закрылся 105.
        bars = _bars_up(10)  # close последнего ~108?
        pos.highest_price = max(float(b.high) for b in bars)
        last = Bar(open=106, high=106.4, low=104.6, close=105.0)
        ec = ExitController(_NoHyp(), smart_default=True)
        ec._apply_smart(b, pos, last, bars, "BULL")
        assert float(pos.stop_loss) > 100.0  # подтянут вслед за ценой

    def test_smart_default_disabled(self, tmp_path):
        b = _mk_broker(tmp_path)
        pos = _open_long(b)
        bars = _bars_down(12)
        ec = ExitController(_NoHyp(), smart_default=False)
        stop0 = pos.stop_loss
        # Обычный apply(): без гипотез и без smart_default ничего не делает.
        assert ec.apply(b, "BTC-USDT", bars[-1], bars, "WEAK_BEAR_TREND") == []
        assert pos.stop_loss == stop0  # STATIC_TP как раньше


class TestRegimeExit:
    def test_long_closed_on_bear_regime(self, tmp_path):
        b = _mk_broker(tmp_path)
        pos = _open_long(b)
        pos.regime = "WEAK_BULL_TREND"
        bars = _bars_up(5)
        ec = ExitController(_NoHyp(), smart_default=True)
        trade = ec._apply_smart(b, pos, bars[-1], bars, "STRONG_BEAR_TREND")
        assert trade is not None and trade.exit_reason == "regime_exit"

    def test_same_regime_no_exit(self, tmp_path):
        b = _mk_broker(tmp_path)
        pos = _open_long(b)
        pos.regime = "WEAK_BEAR_TREND"  # открыт В этом режиме
        bars = _bars_down(3, step=0.5)  # просадка мала (=-0.5R) — не MAE
        ec = ExitController(_NoHyp(), smart_default=True)
        assert ec._apply_smart(b, pos, bars[-1], bars, "WEAK_BEAR_TREND") is None


class TestBrokerNetBreakeven:
    def test_tp1_stop_is_net_breakeven(self, tmp_path):
        """После TP1 стоп стоит ВЫШЕ входа: покрывает комиссии и фандинг."""
        b = _mk_broker(tmp_path)
        pos = b.open_position(
            symbol="BTC-USDT", direction="long",
            entry_price=Decimal("100"), stop_loss=Decimal("97"),
            take_profit=Decimal("103"),
            quantity=Decimal("1"), leverage=2, timeframe="1h",
        )
        pos.tp_fractions = [0.5, 0.25, 0.25]
        pos.tp_filled = [False, False, False]
        pos.bars_held = 4
        # Бар выбивает TP1 (хай 103) и возвращается.
        bar = Bar(open=101, high=103.2, low=99.5, close=99.8)
        closed = b.check_exits(bar)
        assert any(t.exit_reason == "tp1" for t in closed)
        assert pos.trailing_activated
        assert float(pos.stop_loss) > 100.0, "БУ должен покрывать издержки"
        # Стоп выбит на следующем баре → сделка НЕ в минус от издержек.
        bar2 = Bar(open=99.8, high=100.0, low=float(pos.stop_loss) - 0.01, close=float(pos.stop_loss) - 0.05)
        closed2 = b.check_exits(bar2)
        assert closed2 and closed2[0].exit_reason == "stop_loss"
        assert closed2[0].pnl > -0.05, "выход в нетто-БУ ~ ноль, а не минус"

    def test_funding_included_in_be(self, tmp_path):
        """Чем дольше держим, тем выше нетто-БУ (фандинг перпов копится)."""
        b = _mk_broker(tmp_path, funding_rate=Decimal("0.0001"))
        pos = _open_long(b, lev=2)
        pos.timeframe = "1h"
        be_short = b.net_breakeven_price(pos)
        pos.bars_held = 48
        be_long = b.net_breakeven_price(pos)
        assert be_long > be_short > Decimal("100")
