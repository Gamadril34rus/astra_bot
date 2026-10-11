"""Shared NO_TRADE outcome reader during the JSON → append-only JSONL transition.

A JSONL row is ``{id, bar_time (ms), ..., computed_at}``. Repeated ids are
resolved last-wins. JSONL is authoritative, even when empty or damaged;
legacy JSON (``{updated, outcomes: {id: record}}`` or a bare map) is read
only when the JSONL file is absent. Neither reader mutates state.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def outcomes_jsonl_path(path: Path) -> Path:
    """Also accept a legacy .json configuration path during the rollout."""
    return path.with_suffix(".jsonl")


def legacy_outcomes(data: Any) -> dict[str, dict[str, Any]]:
    """Decode legacy JSON strictly, so migration cannot silently lose rows."""
    if not isinstance(data, dict):
        raise ValueError("outcomes JSON must be an object")
    rows = data.get("outcomes", data)
    if not isinstance(rows, dict):
        raise ValueError("outcomes must be an id → record map")
    decoded: dict[str, dict[str, Any]] = {}
    for oid, row in rows.items():
        if not oid or not isinstance(row, dict):
            raise ValueError(f"invalid outcome record: {oid!r}")
        decoded[str(oid)] = {key: value for key, value in row.items() if key != "id"}
    return decoded


def outcome_sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, str, str]:
    """Chronological migration/batch order; id makes exact ties deterministic."""
    oid, row = item
    return int(row.get("bar_time") or 0), str(row.get("computed_at") or ""), oid


def load_outcomes(path: Path) -> dict[str, dict[str, Any]]:
    """Read JSONL (streaming, last-wins), else the corresponding legacy JSON."""
    jsonl = outcomes_jsonl_path(path)
    if jsonl.exists():
        rows: dict[str, dict[str, Any]] = {}
        try:
            with jsonl.open(encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
                        continue
                    rows[row["id"]] = {key: value for key, value in row.items() if key != "id"}
        except OSError as exc:
            logger.debug("no_trade: could not read %s: %s", jsonl, exc)
        return rows

    legacy = path.with_suffix(".json")
    try:
        return legacy_outcomes(json.loads(legacy.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as exc:
        logger.debug("no_trade: could not read %s: %s", legacy, exc)
        return {}
