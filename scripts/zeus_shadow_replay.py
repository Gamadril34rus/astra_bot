#!/usr/bin/env python3
"""Offline JSONL replay runner. Reads only supplied events; never opens a network client."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
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


def external_path(raw: str, label: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError(f"{label} must be outside repository checkout")
    return path


def load_replay(path: Path) -> ShadowReplay:
    if not path.exists():
        return ShadowReplay()
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return ShadowReplay.import_state(payload)


def save_replay(path: Path, replay: ShadowReplay) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(
            replay.export_state(),
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
        if event_id in replay.seen_event_ids:
            return {"duplicate": True, "event_id": event_id}
        replay.seen_event_ids.add(event_id)
        return replay.apply_funding_coverage(
            variant=str(event["variant"]),
            position_id=str(event["position_id"]),
            through_ms=int(event["through_ms"]),
            cumulative_cash_amount=float(event["cumulative_cash_amount"]),
            source_verified=bool(event.get("source_verified", False)),
        )

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

    replay = load_replay(state_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with events_path.open("r", encoding="utf-8") as source, output_path.open(
        "a", encoding="utf-8"
    ) as output:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            event = json.loads(line)
            result = handle_event(replay, event)
            if not result.get("duplicate"):
                output.write(
                    json.dumps(
                        {
                            "line": line_number,
                            "event_id": event.get("event_id"),
                            "result": result,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    + "\n"
                )
                output.flush()
                count += 1
            save_replay(state_path, replay)

    print(
        json.dumps(
            {
                "processed_non_duplicate_events": count,
                "portfolios": {
                    name: portfolio.metrics()
                    for name, portfolio in replay.portfolios.items()
                },
                "state": str(state_path),
                "output": str(output_path),
            },
            ensure_ascii=False,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
