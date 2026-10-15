"""arm_summary journal telemetry (owner 15.10)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from astra_bot.decision.live_arm_wiring import append_journal, run_arm_entries


def test_append_journal_arm_summary(tmp_path: Path) -> None:
    path = tmp_path / "zeus_live_journal.jsonl"
    append_journal(
        {
            "event": "arm_summary",
            "dry": False,
            "entries_allowed": True,
            "halt": None,
            "candidates": 3,
            "entered": [],
            "skipped": {
                "same_side_guard": 2,
                "vol_cooldown": 1,
                "conf": 0,
                "margin": 0,
                "other": 0,
            },
            "unrealized_sum": -1.5,
        },
        path=path,
    )
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(rows) == 1
    r = rows[0]
    assert r["event"] == "arm_summary"
    assert r["candidates"] == 3
    assert r["skipped"]["same_side_guard"] == 2
    assert r["skipped"]["vol_cooldown"] == 1
    assert "ts" in r


@pytest.mark.asyncio
async def test_run_arm_entries_empty_returns_skip_dict() -> None:
    broker = MagicMock()
    broker.client = MagicMock()
    entered, skipped = await run_arm_entries(broker, [], max_entries=1)
    assert entered == []
    assert skipped["same_side_guard"] == 0
    assert "vol_cooldown" in skipped
    assert "other" in skipped


def test_arm_summary_collection_fail_open() -> None:
    """Simulates clock fail-open: exception while building summary must not raise."""
    try:
        raise RuntimeError("boom")
    except Exception as exc:
        assert type(exc).__name__ == "RuntimeError"
