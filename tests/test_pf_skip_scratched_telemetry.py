"""PF skip scratched + exit telemetry fields (exit_price, mfe_r, initial_stop)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra_bot.decision.live_risk import profit_factor


def _write_trades(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_profit_factor_skips_scratched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEUS_PF_SKIP_SCRATCHED", "1")
    path = tmp_path / "trades.jsonl"
    rows = [
        {"event": "exit", "pnl": 10.0, "ts": 1.0, "scratched": False},
        {"event": "exit", "pnl": -2.0, "ts": 2.0, "scratched": True},  # noise
        {"event": "exit", "pnl": -5.0, "ts": 3.0, "scratched": False},
        {"event": "exit", "pnl": 8.0, "ts": 4.0, "scratched": False},
        {"event": "exit", "pnl": -1.0, "ts": 5.0, "scratched": True},
        {"event": "exit", "pnl": 3.0, "ts": 6.0, "scratched": False},
        {"event": "exit", "pnl": 4.0, "ts": 7.0, "scratched": False},
    ]
    _write_trades(path, rows)
    pf, n = profit_factor(path)
    # non-scratched: +10,-5,+8,+3,+4 → wins=25 losses=5 → pf=5.0, n=5
    assert n == 5
    assert pf is not None
    assert abs(float(pf) - 5.0) < 1e-9


def test_profit_factor_includes_scratched_when_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZEUS_PF_SKIP_SCRATCHED", "0")
    path = tmp_path / "trades.jsonl"
    rows = [
        {"event": "exit", "pnl": 10.0, "ts": 1.0, "scratched": False},
        {"event": "exit", "pnl": -2.0, "ts": 2.0, "scratched": True},
        {"event": "exit", "pnl": -5.0, "ts": 3.0, "scratched": False},
        {"event": "exit", "pnl": 8.0, "ts": 4.0, "scratched": False},
        {"event": "exit", "pnl": -1.0, "ts": 5.0, "scratched": True},
        {"event": "exit", "pnl": 3.0, "ts": 6.0, "scratched": False},
        {"event": "exit", "pnl": 4.0, "ts": 7.0, "scratched": False},
    ]
    _write_trades(path, rows)
    pf, n = profit_factor(path)
    # all 7: wins=25 losses=8 → pf=3.125
    assert n == 7
    assert pf is not None
    assert abs(float(pf) - (25.0 / 8.0)) < 1e-9


def test_trade_row_telemetry_fields_present() -> None:
    """Static shape check: broker exit path includes telemetry keys."""
    src = Path("astra_bot/decision/live_broker.py").read_text(encoding="utf-8")
    assert '"exit_price": exit_fill' in src
    assert '"initial_stop": init_stop_f' in src
    assert '"mfe_r": mfe_r_v' in src
    assert "ZEUS_PF_SKIP_SCRATCHED" in Path("astra_bot/decision/live_risk.py").read_text(
        encoding="utf-8"
    )
