#!/usr/bin/env python3
"""Offline JSONL replay runner. Reads only supplied events; never opens a network client."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from astra_bot.research.isolated_shadow import (
    ExecutionQuote,
    ReplayBar,
    ShadowReplay,
    Signal,
    classify_zone,
)


def json_safe(value):
    """Convert non-finite analytics (e.g. PF with no losses) to explicit strings."""
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "UNKNOWN"
        return "INF" if value > 0 else "-INF"
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def external_path(raw: str, label: str) -> Path:
    raw_path = Path(raw).expanduser()
    if raw_path.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    path = raw_path.resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError(f"{label} must be outside repository checkout")
    return path


def save_replay(path: Path, replay: ShadowReplay) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.is_symlink():
        temporary.unlink()
    elif temporary.exists():
        temporary.unlink()
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(
            json_safe(replay.export_state()),
            handle,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        handle.write("\n")
        handle.flush()
    temporary.replace(path)


def quotes_from_event(rows: list[dict[str, Any]]) -> list[ExecutionQuote]:
    return [ExecutionQuote(**row) for row in rows]


def handle_event(replay: ShadowReplay, event: dict[str, Any]) -> dict[str, Any]:
    event_id = str(event.get("event_id") or "")
    if not event_id:
        raise ValueError("event_id is required for restart-safe replay")
    kind = event.get("type")

    if kind == "signal":
        source = Signal(**event["signal"])
        zone_payload = event.get("zone") or {}
        zone = classify_zone(
            side=source.side,
            price=source.predecision_price,
            zones=zone_payload.get("zones"),
            market_received_at_ms=zone_payload.get("market_received_at_ms"),
            zone_received_at_ms=zone_payload.get("zone_received_at_ms"),
            decision_at_ms=source.decision_at_ms,
        )
        return replay.apply_signal_from_quotes(
            event_id=event_id,
            signal=source,
            zone=zone,
            quotes=quotes_from_event(event.get("quotes") or []),
        )

    if kind == "bar":
        return replay.apply_bar_event(
            event_id=event_id,
            bar=ReplayBar(**event["bar"]),
            quotes=quotes_from_event(event.get("quotes") or []),
        )

    if kind == "funding":
        return replay.apply_funding_event(
            event_id=event_id,
            variant=str(event["variant"]),
            position_id=str(event["position_id"]),
            cash_amount=event.get("cash_amount"),
            ts_ms=int(event["ts_ms"]),
        )

    if kind == "funding_coverage":
        result = replay.apply_funding_coverage(
            event_id=event_id,
            variant=str(event["variant"]),
            position_id=str(event["position_id"]),
            through_ms=int(event["through_ms"]),
            cumulative_cash_amount=float(event["cumulative_cash_amount"]),
            source_verified=bool(event.get("source_verified", False)),
        )
        return result

    if kind == "mark":
        return replay.mark_to_market_event(
            event_id=event_id,
            marks={str(key): float(value) for key, value in event["marks"].items()},
            timestamp_ms=int(event["timestamp_ms"]),
        )

    raise ValueError(f"unsupported event type: {kind!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", required=True, help="external JSONL event tape")
    parser.add_argument("--state", required=True, help="external restart state JSON")
    parser.add_argument("--output", required=True, help="external JSONL result log")
    args = parser.parse_args()

    events_path = external_path(args.events, "events")
    state_path = external_path(args.state, "state")
    output_path = external_path(args.output, "output")
    if not events_path.is_file():
        raise FileNotFoundError(events_path)
    if len({events_path, state_path, output_path}) != 3:
        raise ValueError("events, state and output paths must be distinct")

    replay = ShadowReplay()
    result_rows = []
    with events_path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            event = json.loads(line)
            result = handle_event(replay, event)
            if not result.get("duplicate"):
                result_rows.append(
                    {
                        "line": line_number,
                        "event_id": event.get("event_id"),
                        "result": result,
                    }
                )

    # The event tape is the source of truth. Rebuild state and output from the
    # complete tape on every invocation, then replace checkpoints atomically.
    # A crash between replacements is recoverable by replaying the same tape.
    save_replay(state_path, replay)
    output_tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    if output_tmp.is_symlink():
        output_tmp.unlink()
    elif output_tmp.exists():
        output_tmp.unlink()
    with output_tmp.open("x", encoding="utf-8") as output:
        for row in result_rows:
            output.write(
                json.dumps(
                    json_safe(row),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            )
        output.flush()
    output_tmp.replace(output_path)

    print(
        json.dumps(
            json_safe(
                {
                    "processed_non_duplicate_events": len(result_rows),
                    "portfolios": {
                        name: portfolio.metrics()
                        for name, portfolio in replay.portfolios.items()
                    },
                    "state": str(state_path),
                    "output": str(output_path),
                    "restart_mode": "full_event_tape_replay",
                }
            ),
            ensure_ascii=False,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
