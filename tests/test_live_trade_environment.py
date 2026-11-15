from __future__ import annotations

import json

from astra_bot.decision.live_broker import append_live_trade


def read_single_row(path):
    rows = path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    return json.loads(rows[0])


def test_append_live_trade_tags_verified_vst(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_VST_ENDPOINT_CONFIRMED", "1")
    monkeypatch.setenv("ZEUS_MAINNET", "0")
    path = tmp_path / "trades.jsonl"
    source = {"event": "entry", "symbol": "BTC-USDT"}

    append_live_trade(source, path=path)

    assert read_single_row(path)["environment"] == "VST"
    assert "environment" not in source


def test_append_live_trade_tags_real_only_after_endpoint_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_VST_ENDPOINT_CONFIRMED", "0")
    monkeypatch.setenv("ZEUS_MAINNET", "1")
    path = tmp_path / "trades.jsonl"

    append_live_trade({"event": "exit", "symbol": "ETH-USDT"}, path=path)

    assert read_single_row(path)["environment"] == "REAL"


def test_append_live_trade_does_not_guess_without_endpoint_confirmation(tmp_path, monkeypatch):
    monkeypatch.delenv("ZEUS_VST_ENDPOINT_CONFIRMED", raising=False)
    monkeypatch.setenv("ZEUS_MAINNET", "0")
    path = tmp_path / "trades.jsonl"

    append_live_trade({"event": "exit", "symbol": "SOL-USDT"}, path=path)

    row = read_single_row(path)
    assert "environment" not in row
    assert "env" not in row


def test_append_live_trade_preserves_explicit_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_VST_ENDPOINT_CONFIRMED", "1")
    monkeypatch.setenv("ZEUS_MAINNET", "0")
    path = tmp_path / "trades.jsonl"

    append_live_trade(
        {"event": "exit", "symbol": "HYPE-USDT", "environment": "REAL"},
        path=path,
    )

    assert read_single_row(path)["environment"] == "REAL"
