import json
from pathlib import Path

import scripts.morning_report as mr


def test_morning_report_exits_section(tmp_path: Path, monkeypatch, capsys):
    trades_file = tmp_path / "paper_trades.jsonl"
    trades_data = [
        {"strategy": "momentum", "mfe_r": 1.5, "mae_r": -0.5, "r_multiple": 1.0},
        {"strategy": "momentum", "mfe_r": 2.0, "mae_r": -0.2, "r_multiple": 1.5},
        {"strategy": "momentum", "mfe_r": 1.0, "mae_r": -0.8, "r_multiple": -0.5},
        {"strategy": "scalp", "mfe_r": 0.5, "mae_r": -0.2, "r_multiple": 0.3},
        {"strategy": "scalp", "mfe_r": 0.8, "mae_r": -0.1, "r_multiple": 0.5},  # scalp n=2 (<3 => excluded)
    ]
    with open(trades_file, "w", encoding="utf-8") as f:
        for t in trades_data:
            f.write(json.dumps(t) + "\n")

    monkeypatch.setattr(mr, "TRADES_JSONL", trades_file)
    monkeypatch.setattr("asyncio.run", lambda coro: None)

    mr.main()

    captured = capsys.readouterr().out
    assert "🚪 Выходы:" in captured
    assert "momentum (n=3): MFE=+1.50R | MAE=-0.50R | avgR=+0.67R" in captured
    assert "scalp (n=2)" not in captured
