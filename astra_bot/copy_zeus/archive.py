"""Append-only JSONL archive for observed COPY_ZEUS account data."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .client import CopyZeusClient, archive_directory, environment

IDENTITY_FIELDS = ("incomeId", "income_id", "tranId", "transactionId", "orderId", "id")
TIME_FIELDS = ("time", "timestamp", "createTime", "updateTime")


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _event_time(event: dict[str, Any]) -> int | None:
    for key in TIME_FIELDS:
        value = event.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
    return None


def dedup_key(source: str, event: dict[str, Any]) -> str:
    for field in IDENTITY_FIELDS:
        if event.get(field) not in (None, ""):
            return f"{source}:{field}:{event[field]}"
    canonical = json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"{source}:sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


def classify_income(event: dict[str, Any]) -> str:
    raw = str(event.get("incomeType", event.get("type", "UNKNOWN"))).upper()
    for kind in ("TRANSFER", "DEPOSIT", "WITHDRAW"):
        if kind in raw:
            return kind
    return raw or "UNKNOWN"


class CopyZeusArchive:
    def __init__(self, root: str | Path = "models", env: str | None = None):
        self.env = (env or environment()).lower()
        self.directory = archive_directory(str(root), self.env)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.archive_path = self.directory / "archive.jsonl"
        self.seen_path = self.directory / "seen.json"
        self.coverage_path = self.directory / "coverage.json"

    def append(self, source: str, events: Iterable[dict[str, Any]], fetched_at: str | None = None) -> int:
        fetched_at = fetched_at or datetime.now(UTC).isoformat()
        seen = set(_read_json(self.seen_path, []))
        added = 0
        with self.archive_path.open("a", encoding="utf-8") as stream:
            for event in events:
                if not isinstance(event, dict):
                    continue
                key = dedup_key(source, event)
                if source != "positions" and key in seen:
                    continue
                row = {
                    "env": self.env, "source": source, "fetched_at": fetched_at,
                    "event": event, "integrity": "raw_exchange_fields",
                }
                if source == "income":
                    row["event_kind"] = classify_income(event)
                stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                if source != "positions":
                    seen.add(key)
                added += 1
        self.seen_path.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return added

    def record_coverage(self, source: str, start_ms: int | None, end_ms: int | None, gap: bool = False):
        data = _read_json(self.coverage_path, {"intervals": [], "gaps": []})
        item = {"source": source, "start_ms": start_ms, "end_ms": end_ms}
        data["gaps" if gap else "intervals"].append(item)
        self.coverage_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    async def backfill_income(self, client: CopyZeusClient, start_ms: int, end_ms: int, limit: int = 5, max_pages: int = 100) -> int:
        """Fill a known interval backwards; persist a gap if pagination cannot cover it."""
        cursor = int(end_ms)
        added = 0
        oldest_seen = None
        for _ in range(max_pages):
            response = await client.income(
                limit=min(max(int(limit), 1), 5), startTime=int(start_ms), endTime=cursor,
            )
            rows = _rows(response)
            if not rows:
                break
            added += self.append("income", rows)
            times = [t for row in rows if (t := _event_time(row)) is not None]
            if not times:
                break
            oldest = min(times)
            oldest_seen = oldest if oldest_seen is None else min(oldest_seen, oldest)
            if oldest <= start_ms or len(rows) < limit:
                break
            if oldest >= cursor:
                self.record_coverage("income", start_ms, oldest_seen, gap=True)
                return added
            cursor = oldest - 1
        if oldest_seen is None or oldest_seen > start_ms:
            self.record_coverage("income", start_ms, oldest_seen, gap=True)
        else:
            self.record_coverage("income", start_ms, end_ms)
        return added

    async def run_once(self, client: CopyZeusClient | None = None, limit: int = 5, max_pages: int = 5) -> dict[str, int]:
        own_client = client is None
        if client is None:
            client = CopyZeusClient(env=self.env)
        counts: dict[str, int] = {}
        try:
            fetched_at = datetime.now(UTC).isoformat()
            positions = await client.positions(limit=limit)
            counts["positions"] = self.append("positions", _rows(positions), fetched_at)
            self.record_coverage("positions", None, None)

            orders = await client.all_orders(limit=limit)
            order_rows = _rows(orders)
            counts["allOrders"] = self.append("allOrders", order_rows, fetched_at)
            times = [t for row in order_rows if (t := _event_time(row)) is not None]
            self.record_coverage("allOrders", min(times) if times else None, max(times) if times else None)

            now_ms = int(datetime.now(UTC).timestamp() * 1000)
            coverage = _read_json(self.coverage_path, {"intervals": []})
            covered_ends = [
                int(item["end_ms"]) for item in coverage.get("intervals", [])
                if item.get("source") == "income" and item.get("end_ms") is not None
            ]
            if covered_ends:
                counts["income"] = await self.backfill_income(
                    client, max(0, max(covered_ends) + 1), now_ms,
                    limit=limit, max_pages=max_pages,
                )
                return counts
            income_rows = 0
            cursor = now_ms
            oldest_seen = None
            exhausted = True
            for _ in range(max_pages):
                response = await client.income(limit=min(max(int(limit), 1), 5), endTime=cursor)
                rows = _rows(response)
                if not rows:
                    break
                income_rows += self.append("income", rows, fetched_at)
                times = [t for row in rows if (t := _event_time(row)) is not None]
                if not times:
                    break
                oldest = min(times)
                self.record_coverage("income", oldest, max(times))
                if len(rows) < limit:
                    exhausted = False
                    break
                cursor = oldest - 1
            if oldest_seen is not None:
                self.record_coverage("income", oldest_seen, now_ms, gap=exhausted)
            counts["income"] = income_rows
            return counts
        finally:
            if own_client:
                await client.close()


def _rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    data = response.get("data", [])
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("data", "list", "rows", "orders", "positions", "income"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        return [data] if data else []
    return []
