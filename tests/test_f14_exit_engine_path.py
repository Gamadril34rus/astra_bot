"""F14: load_positions(None) must not AttributeError; default path works."""
from __future__ import annotations

import json

from astra_bot.decision.live_positions import DEFAULT_PATH, load_positions


def test_load_positions_none_uses_default_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_positions(None) == {}
    assert load_positions() == {}


def test_load_positions_none_reads_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "models" / "zeus_live_positions.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "positions": {
                    "BTC-USDT|long": {
                        "symbol": "BTC-USDT",
                        "side": "long",
                        "entry": "100",
                        "stop": "90",
                        "qty": "1",
                    }
                },
                "ts": 1.0,
            }
        ),
        encoding="utf-8",
    )
    got = load_positions(None)
    assert "BTC-USDT|long" in got
    assert got["BTC-USDT|long"]["entry"] == "100"


def test_load_positions_explicit_none_equals_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir(parents=True)
    (tmp_path / "models" / "zeus_live_positions.json").write_text(
        json.dumps({"positions": {"X|long": {"symbol": "X", "side": "long"}}, "ts": 0}),
        encoding="utf-8",
    )
    a = load_positions(None)
    b = load_positions(DEFAULT_PATH)
    assert a == b
    assert "X|long" in a
