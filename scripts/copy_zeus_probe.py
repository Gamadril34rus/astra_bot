"""One-shot, read-only COPY_ZEUS API shape probe; never writes raw responses."""
from __future__ import annotations

import asyncio
import json
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from astra_bot.copy_zeus.archive import _rows
from astra_bot.copy_zeus.client import CopyZeusClient, ENDPOINTS

IDENTIFIER_FIELDS = (
    "incomeId", "income_id", "tranId", "transactionId", "orderId", "id",
    "tradeId", "asset", "symbol", "incomeType", "type",
)


def _shape(value: Any):
    if isinstance(value, dict):
        return {str(k): _shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    return type(value).__name__


def _sample(record: dict[str, Any]):
    # Keep field names and data types; mask every actual value.
    return {key: "<masked>" for key in list(record)[:40]}


async def run_probe() -> dict[str, Any]:
    now = datetime.now(UTC)
    since_ms = int((now - timedelta(hours=24)).timestamp() * 1000)
    report: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "environment": os.getenv("COPYZEUS_ENV", "vst").strip().lower(),
        "endpoints": {},
    }
    async with CopyZeusClient() as client:
        for name, call in (
            ("positions", lambda: client.positions(limit=5)),
            ("allOrders", lambda: client.all_orders(limit=5)),
        ):
            response = await call()
            rows = _rows(response)
            report["endpoints"][name] = {
                "path": ENDPOINTS[name],
                "response_schema": _shape(response),
                "count": len(rows),
                "sample": _sample(rows[0]) if rows else None,
            }

        all_income: list[dict[str, Any]] = []
        page_counts = []
        end_time = int(now.timestamp() * 1000)
        last_response = {}
        for page in range(5):
            # First page is the requested 24-hour window; subsequent pages walk
            # backward without a lower bound so the actual available depth is measurable.
            params = {"endTime": end_time}
            if page == 0:
                params["startTime"] = since_ms
            last_response = await client.income(limit=5, **params)
            rows = _rows(last_response)[:5]
            page_counts.append(len(rows))
            all_income.extend(rows)
            timestamps = []
            for row in rows:
                for field in ("time", "timestamp", "createTime"):
                    try:
                        timestamps.append(int(row[field]))
                        break
                    except (KeyError, TypeError, ValueError):
                        continue
            if not timestamps:
                break
            oldest = min(timestamps)
            if oldest <= since_ms or len(rows) < 5:
                break
            end_time = oldest - 1

        type_counts = Counter(str(x.get("incomeType", x.get("type", "UNKNOWN"))) for x in all_income)
        fields_by_type: dict[str, set[str]] = defaultdict(set)
        for event in all_income:
            typ = str(event.get("incomeType", event.get("type", "UNKNOWN")))
            fields_by_type[typ].update(k for k in IDENTIFIER_FIELDS if k in event)
        timestamps = [
            int(row[field]) for row in all_income for field in ("time", "timestamp", "createTime")
            if row.get(field) is not None and str(row[field]).isdigit()
        ]
        report["endpoints"]["income"] = {
            "path": ENDPOINTS["income"],
            "response_schema": _shape(last_response) if last_response else {"data": "list"},
            "count": len(all_income),
            "pages": page_counts,
            "window_hours": 24,
            "history_depth_days": round((int(now.timestamp() * 1000) - min(timestamps)) / 86400000, 3) if timestamps else None,
            "type_counts": dict(sorted(type_counts.items())),
            "identifier_fields_by_type": {k: sorted(v) for k, v in sorted(fields_by_type.items())},
            "sample": _sample(all_income[0]) if all_income else None,
        }
    return report


def main():
    report = asyncio.run(run_probe())
    out = Path(os.getenv("COPYZEUS_REPORT", "probe-report.json"))
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
