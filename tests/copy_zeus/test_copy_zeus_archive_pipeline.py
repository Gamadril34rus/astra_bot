"""Offline tests for the real-only COPY_ZEUS archive; all state stays under tmp_path."""
import asyncio
import json
import zipfile
from pathlib import Path

import pytest

from astra_bot.copy_zeus.archive import CopyZeusArchive
from scripts.copy_zeus_archive import restore_state, seal_state


def test_run_once_is_real_only(tmp_path):
    archive = CopyZeusArchive(root=tmp_path, env="vst")

    with pytest.raises(ValueError, match="real-account only"):
        asyncio.run(archive.run_once(client=object()))


def test_run_once_uses_50_rows_and_stops_at_seen_income(tmp_path):
    archive = CopyZeusArchive(root=tmp_path, env="real")
    archive.append("income", [{"tranId": "old", "time": 1}])

    class FakeClient:
        async def positions(self, limit):
            assert limit == 50
            return {"data": [{"symbol": "BTC-USDT", "positionAmt": "0"}]}

        async def all_orders(self, limit):
            assert limit == 50
            return {"data": {"orders": [{"orderId": 12, "symbol": "BTC-USDT"}]}}

        async def income(self, limit, **params):
            assert limit == 50
            assert params["startTime"] < params["endTime"]
            return {
                "data": [
                    {"tranId": "new", "time": 3, "incomeType": "REALIZED_PNL"},
                    {"tranId": "old", "time": 1, "incomeType": "TRADING_FEE"},
                ]
            }

    counts = asyncio.run(archive.run_once(client=FakeClient()))
    assert counts["positions"] == 1
    assert counts["allOrders"] == 1
    assert counts["income"] == 1
    assert counts["income_pages"] == 1
    rows = [json.loads(line) for line in archive.archive_path.read_text().splitlines()]
    assert all(row["env"] == "real" for row in rows)
    assert any(row["source"] == "positions" and row["fetched_at"] for row in rows)


def test_rotation_manifest_and_authenticated_restore(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    pending = state / "archive.jsonl"
    rows = [
        {
            "env": "real",
            "source": "income",
            "fetched_at": "2026-11-04T12:00:00+00:00",
            "event": {"tranId": "t1", "income": "-0.1"},
            "event_kind": "TRADING_FEE",
        },
        {
            "env": "real",
            "source": "allOrders",
            "fetched_at": "2026-11-04T12:00:00+00:00",
            "event": {"orderId": 1},
        },
    ]
    pending.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (state / "seen.json").write_text('["income:tranId:t1"]', encoding="utf-8")
    (state / "coverage.json").write_text('{"intervals":[],"gaps":[]}', encoding="utf-8")

    output = tmp_path / "out"
    manifest = seal_state(state, output, "existing-bingx-secret")
    assert manifest["total_rows"] == 2
    rotation = state / "copyzeus-2026-11.jsonl"
    assert rotation.exists()
    assert rotation.stat().st_size <= 10 * 1024 * 1024
    assert (output / "MANIFEST.json").exists()
    assert (output / "archive.enc").exists()
    assert not (output / "archive-state.zip").exists()

    restored = tmp_path / "restored"
    restore_state(output, restored, "existing-bingx-secret")
    assert (restored / rotation.name).read_bytes() == rotation.read_bytes()
    assert json.loads((restored / "seen.json").read_text()) == ["income:tranId:t1"]
    with zipfile.ZipFile(output / "archive.enc") as _:
        pytest.fail("ciphertext must not be a plaintext ZIP archive")


def test_wrong_secret_cannot_restore_archive(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "seen.json").write_text("[]", encoding="utf-8")
    (state / "coverage.json").write_text('{"intervals":[],"gaps":[]}', encoding="utf-8")
    output = tmp_path / "out"
    seal_state(state, output, "right-secret")
    with pytest.raises(Exception):
        restore_state(output, tmp_path / "bad-restore", "wrong-secret")


def test_rotation_is_capped_at_ten_mib(tmp_path):
    from scripts.copy_zeus_archive import ROTATION_LIMIT, rotate_pending

    state = tmp_path / "state"
    state.mkdir()
    # A valid single JSONL row must be rotated without exceeding the cap.
    row = {"fetched_at": "2026-11-04T00:00:00+00:00", "event": {"payload": "x" * 100}}
    (state / "archive.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    rotate_pending(state)
    files = list(state.glob("copyzeus-*.jsonl"))
    assert len(files) == 1
    assert files[0].stat().st_size <= ROTATION_LIMIT
    assert not (state / "archive.jsonl").exists()
