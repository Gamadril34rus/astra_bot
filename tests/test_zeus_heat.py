"""Zeus heat governor: 3 consecutive losses → risk capped at 1% until win or UTC day end."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from astra_bot.decision.conf_risk_ladder import conf_risk_pct
from astra_bot.decision.zeus_heat import ZeusHeatGovernor

CONF_RISK_LADDER = ((0.88, 3.0), (0.80, 2.0), (0.70, 1.5))


def _budget(conf: float, equity: Decimal, heat: ZeusHeatGovernor) -> Decimal:
    """Mirror runner wrap: heat → 1%, else ladder."""
    if heat.is_heat_active():
        return equity * Decimal("0.01")
    return equity * conf_risk_pct(conf, CONF_RISK_LADDER)


def test_heat_cap_after_3_losses(tmp_path: Path):
    heat = ZeusHeatGovernor(state_path=tmp_path / "heat.json")
    equity = Decimal("2000")
    conf = 0.95  # would be 3% ladder

    heat.record(-1.0)
    heat.record(-1.0)
    assert heat.consecutive == 2
    assert heat.is_heat_active() is False
    assert _budget(conf, equity, heat) == Decimal("60.0")  # still 3%

    heat.record(-1.0)  # 3rd loss → heat
    assert heat.consecutive == 3
    assert heat.is_heat_active() is True
    assert _budget(conf, equity, heat) == Decimal("20.0")  # capped to 1%


def test_heat_release_on_win(tmp_path: Path):
    heat = ZeusHeatGovernor(state_path=tmp_path / "heat.json")
    equity = Decimal("2000")
    conf = 0.95

    heat.record(-1.0)
    heat.record(-1.0)
    heat.record(-1.0)
    assert heat.is_heat_active() is True
    assert _budget(conf, equity, heat) == Decimal("20.0")

    heat.record(5.0)  # win clears
    assert heat.consecutive == 0
    assert heat.is_heat_active() is False
    assert _budget(conf, equity, heat) == Decimal("60.0")


def test_heat_survives_restart(tmp_path: Path):
    path = tmp_path / "heat.json"
    heat = ZeusHeatGovernor(state_path=path)
    heat.record(-1.0)
    heat.record(-1.0)
    assert heat.consecutive == 2
    assert path.exists()

    heat2 = ZeusHeatGovernor(state_path=path)
    assert heat2.consecutive == 2
    assert heat2.is_heat_active() is False

    # After 3rd loss, heat_active also survives restart
    heat2.record(-1.0)
    assert heat2.is_heat_active() is True
    heat3 = ZeusHeatGovernor(state_path=path)
    assert heat3.consecutive == 3
    assert heat3.is_heat_active() is True


def test_heat_expires_next_utc_day(tmp_path: Path):
    path = tmp_path / "heat.json"
    day0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    heat = ZeusHeatGovernor(state_path=path)
    heat.record(-1.0, now=day0)
    heat.record(-1.0, now=day0)
    heat.record(-1.0, now=day0)
    assert heat.is_heat_active(now=day0) is True

    day1 = day0 + timedelta(days=1)
    assert heat.is_heat_active(now=day1) is False
