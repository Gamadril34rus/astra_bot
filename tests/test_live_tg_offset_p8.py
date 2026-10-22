"""P8: live TG offset isolation + migration (owner 21.10)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from astra_bot.decision import tg_live_commands as tlc


def test_offset_path_is_live_native():
    assert tlc.OFFSET_PATH.name == "zeus_live_tg_offset.json"
    assert "live_tg_offset" in str(tlc.OFFSET_PATH)


def test_migration_floor_zero_processes_low_uids(tmp_path: Path):
    """No own file → floor=0; uid ~290M processed (not skipped vs 774M)."""
    own = tmp_path / "zeus_live_tg_offset.json"
    # do NOT create own file
    updates = {
        "ok": True,
        "result": [
            {"update_id": 290668292, "message": {"text": "/reset_kill", "from": {"id": 1}, "chat": {"id": 1}}},
            {"update_id": 290668293, "message": {"text": "/resume_entries", "from": {"id": 1}, "chat": {"id": 1}}},
        ],
    }
    with patch.object(tlc, "_api", return_value=updates), patch.object(
        tlc, "process_update", return_value=True
    ) as proc, patch.dict("os.environ", {"TELEGRAM_ADMIN_ID": "1"}, clear=False):
        n = tlc.poll_once("TOKEN", timeout_sec=1, offset_path=own, commands_path=tmp_path / "cmds.json")
    assert n == 2
    assert proc.call_count == 2
    assert own.exists()
    assert json.loads(own.read_text())["offset"] == 290668294  # max+1


def test_own_file_floor_skips_only_below(tmp_path: Path):
    own = tmp_path / "zeus_live_tg_offset.json"
    own.write_text(json.dumps({"offset": 290668293}))
    updates = {
        "ok": True,
        "result": [
            {"update_id": 290668292, "message": {"text": "old", "from": {"id": 1}, "chat": {"id": 1}}},
            {"update_id": 290668294, "message": {"text": "/reset_kill", "from": {"id": 1}, "chat": {"id": 1}}},
        ],
    }
    with patch.object(tlc, "_api", return_value=updates), patch.object(
        tlc, "process_update", return_value=True
    ) as proc, patch.dict("os.environ", {"TELEGRAM_ADMIN_ID": "1"}, clear=False):
        n = tlc.poll_once("TOKEN", timeout_sec=1, offset_path=own, commands_path=tmp_path / "cmds.json")
    assert n == 1
    assert proc.call_count == 1
    assert json.loads(own.read_text())["offset"] == 290668295


def test_foreign_high_offset_does_not_blind_when_own_missing(tmp_path: Path):
    """Regression 21.10: poisoned 774M last_uid must not set floor on migrate."""
    own = tmp_path / "zeus_live_tg_offset.json"
    cmds = tmp_path / "zeus_live_commands.json"
    cmds.write_text(json.dumps({"commands": [], "last_tg_update_id": 774152827}))
    updates = {
        "ok": True,
        "result": [
            {"update_id": 290668292, "message": {"text": "x", "from": {"id": 1}, "chat": {"id": 1}}},
        ],
    }
    with patch.object(tlc, "_api", return_value=updates), patch.object(
        tlc, "process_update", return_value=True
    ) as proc, patch.dict("os.environ", {"TELEGRAM_ADMIN_ID": "1"}, clear=False):
        n = tlc.poll_once("TOKEN", timeout_sec=1, offset_path=own, commands_path=cmds)
    assert n == 1
    assert proc.call_count == 1


def test_max_id_grows_from_actual_uids_not_seed_offset(tmp_path: Path):
    own = tmp_path / "zeus_live_tg_offset.json"
    # own file with high wrong value would still floor-skip; migration path tested above.
    # Here own exists with live-native offset.
    own.write_text(json.dumps({"offset": 100}))
    updates = {
        "ok": True,
        "result": [
            {"update_id": 150, "message": {"text": "a", "from": {"id": 1}, "chat": {"id": 1}}},
            {"update_id": 151, "message": {"text": "b", "from": {"id": 1}, "chat": {"id": 1}}},
        ],
    }
    with patch.object(tlc, "_api", return_value=updates), patch.object(
        tlc, "process_update", return_value=True
    ), patch.dict("os.environ", {"TELEGRAM_ADMIN_ID": "1"}, clear=False):
        tlc.poll_once("TOKEN", timeout_sec=1, offset_path=own)
    assert json.loads(own.read_text())["offset"] == 152
