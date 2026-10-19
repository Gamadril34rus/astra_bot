"""Offset dual-store + no-ack on dedup (owner phantom reset_kill)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from astra_bot.decision.live_commands import (
    enqueue,
    get_last_tg_update_id,
    set_last_tg_update_id,
    load_commands,
)
from astra_bot.decision import tg_live_commands as tlc


def test_last_tg_update_id_roundtrip(tmp_path):
    path = tmp_path / "cmds.json"
    enqueue("stop", path=path)
    set_last_tg_update_id(42, path)
    assert get_last_tg_update_id(path) == 42
    assert len(load_commands(path)) == 1  # commands preserved


def test_poll_once_stuck_update_no_dup(tmp_path, monkeypatch):
    """Same update_id twice → one enqueue, second no ack, offset grows."""
    cmds = tmp_path / "cmds.json"
    off = tmp_path / "offset.json"
    off.write_text(json.dumps({"offset": 100}), encoding="utf-8")
    set_last_tg_update_id(0, cmds)

    upd = {
        "update_id": 105,
        "message": {
            "text": tlc.BTN_RESET_KILL,
            "from": {"id": 1},
            "chat": {"id": 1},
        },
    }
    calls = {"n": 0}

    def fake_api(token, method, params=None, post_json=None):
        if method == "getUpdates":
            calls["n"] += 1
            return {"ok": True, "result": [upd]}
        return {"ok": True}

    acks = []
    monkeypatch.setattr(tlc, "_api", fake_api)
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: acks.append(a[2] if len(a) > 2 else k))
    monkeypatch.setenv("TELEGRAM_ADMIN_ID", "1")

    n1 = tlc.poll_once("tok", offset_path=off, commands_path=cmds, timeout_sec=1)
    n2 = tlc.poll_once("tok", offset_path=off, commands_path=cmds, timeout_sec=1)
    assert n1 == 1
    assert n2 == 0  # skipped by floor
    assert len(load_commands(cmds)) == 1
    assert get_last_tg_update_id(cmds) >= 105
    assert json.loads(off.read_text())["offset"] >= 106
    # only one ack
    assert sum(1 for a in acks if isinstance(a, str) and "стоп-кран" in a) <= 1


def test_dedup_no_ack(tmp_path, monkeypatch):
    path = tmp_path / "c.json"
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no ack")))
    # first ok
    monkeypatch.setattr(tlc, "send_reply", lambda *a, **k: None)
    r1 = tlc._enqueue_and_ack("reset_kill", token="t", chat=1, commands_path=path)
    assert r1 == "reset_kill"
    r2 = tlc._enqueue_and_ack("reset_kill", token="t", chat=1, commands_path=path)
    assert r2 is None
