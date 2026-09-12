"""D3: контракт confidence и инварианты маппинга (плечо/EV).

Владелец (13.09.2026): ``conf`` — НЕ калиброванная вероятность. Пересчёта
нет, поля state не переименовываются; закрепляем контракт и математику
лестницы плеча тестами.
"""

from __future__ import annotations

from astra_bot.decision.config import DecisionConfig
from astra_bot.decision.trading_engine import LEVERAGE_LADDER, leverage_for

MM = 0.005


def _lev(conf: float, ev: float, *, entry: float = 100.0, stop: float = 98.0,
         max_lev: int = 100, min_ev: float = 0.8) -> int:
    return leverage_for(conf, ev, entry, stop, max_lev, min_ev, MM)


def test_confidence_is_documented_as_not_calibrated():
    """Контракт зафиксирован в docstring конфига (и в README)."""
    doc = DecisionConfig.__doc__ or ""
    assert "НЕ калиброванная вероятность" in doc


def test_no_edge_gives_no_leverage_even_at_full_confidence():
    """Плечо — функция EV-гейта: без edge conf=1.0 не даёт ничего."""
    assert _lev(1.0, 0.0) == 1
    assert _lev(1.0, 0.79) == 1
    assert _lev(1.0, 0.8) >= 2


def test_leverage_is_nondecreasing_in_confidence():
    for ev in (1.1, 1.6, 2.5, 3.5):
        prev = 1
        for conf in (0.0, 0.4, 0.55, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0):
            lev = _lev(conf, ev)
            assert lev >= prev, (conf, ev, lev, prev)
            prev = lev


def test_leverage_is_nondecreasing_in_ev():
    for conf in (0.0, 0.55, 0.85, 1.0):
        prev = 1
        for ev in (0.0, 0.5, 0.8, 1.1, 1.3, 1.6, 2.0, 2.5, 3.0):
            lev = _lev(conf, ev)
            assert lev >= prev, (conf, ev, lev, prev)
            prev = lev


def test_ladder_rungs_and_ci_cap():
    """Ступени соответствуют LEVERAGE_LADDER; потолок max_leverage — жёсткий."""
    for min_conf, min_ev, rung in LEVERAGE_LADDER:
        # Стоп 0.49% → feasible = int(1/(0.0049+0.005))-1 = 100:
        # клипса «стоп раньше ликвидации» здесь не режет ступень.
        assert _lev(min_conf, min_ev, stop=99.51) == min(rung, 100)
    assert _lev(0.99, 5.0, max_lev=3) == 3          # CI-потолок «эпохи-2»
    for conf in (0.5, 0.9, 1.0):
        for ev in (0.8, 2.0, 5.0):
            assert _lev(conf, ev, max_lev=3) <= 3


def test_stop_must_die_before_liquidation_clamp():
    """Инвариант README: lev <= 1/(d + mm) − 1 (стоп раньше ликвидации)."""
    for stop in (99.0, 98.0, 95.0, 90.0):
        d = (100.0 - stop) / 100.0
        feasible = int(1.0 / (d + MM)) - 1
        lev = _lev(1.0, 5.0, stop=stop)
        assert lev <= max(1, feasible)
        if lev > 1:
            liq = 100.0 * (1 - 1 / lev + MM)      # broker.liquidation_price, long
            assert liq < stop                     # уровень стопа выше ликвидации
