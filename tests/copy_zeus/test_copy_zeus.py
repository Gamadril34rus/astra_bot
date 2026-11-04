"""Offline tests for COPY_ZEUS; all writes are confined to pytest tmp_path."""
import asyncio

from astra_bot.copy_zeus.archive import CopyZeusArchive, classify_income
from astra_bot.copy_zeus.client import (
    BINGX_MAINNET, BINGX_VST, CopyZeusClient, archive_directory, base_url_for, mask_identifier,
)


def test_dedup_preserves_distinct_events(tmp_path):
    archive = CopyZeusArchive(root=tmp_path, env="vst")
    one = {"incomeId": 1, "income": "1.0"}
    other = {"incomeId": 2, "income": "1.0"}
    assert archive.append("income", [one, other, one]) == 2
    assert archive.append("income", [one, other]) == 0
    assert len(archive.archive_path.read_text().splitlines()) == 2


def test_backfill_fills_artificial_gap(tmp_path):
    archive = CopyZeusArchive(root=tmp_path, env="vst")

    class FakeClient:
        async def income(self, limit=5, **params):
            assert params["startTime"] == 100
            assert params["endTime"] == 300
            return {"data": [{"incomeId": 7, "time": 200, "incomeType": "REALIZED_PNL"}]}

    count = asyncio.run(archive.backfill_income(FakeClient(), 100, 300))
    assert count == 1
    assert archive.coverage_path.exists()
    assert '"start_ms": 100' in archive.coverage_path.read_text()


def test_deposit_is_preserved_as_separate_event(tmp_path):
    archive = CopyZeusArchive(root=tmp_path, env="real")
    event = {"incomeId": "d1", "incomeType": "DEPOSIT", "income": "25"}
    assert classify_income(event) == "DEPOSIT"
    archive.append("income", [event])
    row = archive.archive_path.read_text()
    assert '"event_kind":"DEPOSIT"' in row
    assert '"incomeType":"DEPOSIT"' in row


def test_environment_switches_url_and_archive_directory(tmp_path):
    assert base_url_for("vst") == BINGX_VST
    assert base_url_for("real") == BINGX_MAINNET
    assert str(archive_directory(str(tmp_path), "vst")).endswith("copy_zeus/vst")
    assert str(archive_directory(str(tmp_path), "real")).endswith("copy_zeus/real")


def test_secret_masking():
    assert mask_identifier("0123456789") == "***6789"
    assert mask_identifier("x") == "***"
    assert "0123456789" not in mask_identifier("0123456789")


def test_post_is_forbidden_by_get_only_interface():
    assert set(CopyZeusClient.allowed_paths) == {
        "/openApi/swap/v2/user/positions",
        "/openApi/swap/v2/trade/allOrders",
        "/openApi/swap/v2/user/income",
    }
    assert not hasattr(CopyZeusClient, "post")
    assert not hasattr(CopyZeusClient, "delete")
    # Even the internal dispatcher rejects unapproved paths before networking.
    client = object.__new__(CopyZeusClient)
    with __import__("pytest").raises(PermissionError):
        asyncio.run(client.get("/openApi/swap/v2/trade/order"))


def test_positions_are_independent_observations(tmp_path):
    archive = CopyZeusArchive(root=tmp_path, env="vst")
    snapshot = {"symbol": "BTC-USDT", "positionAmt": "0"}
    assert archive.append("positions", [snapshot], "2026-11-04T10:00:00Z") == 1
    assert archive.append("positions", [snapshot], "2026-11-04T10:01:00Z") == 1
    assert len(archive.archive_path.read_text().splitlines()) == 2
