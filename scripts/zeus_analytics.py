#!/usr/bin/env python3
"""Build a deterministic, read-only Zeus analytics report.

Inputs: models/*.jsonl, selected models/*.json snapshots, reports/**/*.json[l].
Output: reports/zeus_analytics/summary.md only. Never run from a workflow/cron.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from astra_bot.decision.live_session import MSK, raw_bucket  # noqa: E402

PAPER = "models/zeus_paper_trades.jsonl"
LIVE_TRADES = "models/zeus_live_trades.jsonl"
LIVE_ENTRIES = "models/zeus_live_entries.jsonl"
LIVE_JOURNAL = "models/zeus_live_journal.jsonl"
ZONE_STATE = "models/zone_shadow_approach.json"
STRATEGIES = ("channel", "wedge", "multi-top")


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def epoch(value: Any) -> float | None:
    result = number(value)
    if result is None or result <= 0:
        return None
    return result / 1000 if result > 100_000_000_000 else result


def stamp(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        result = epoch(row.get(key))
        if result is not None:
            return result
    return None


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    malformed = 0
    if not path.is_file():
        return rows, malformed
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                malformed += 1
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows, malformed


def read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, (dict, list)) else None


def period(stamps: Iterable[float | None]) -> str:
    values = sorted(value for value in stamps if value is not None and value > 0)
    if not values:
        return "нет данных"
    first = datetime.fromtimestamp(values[0], timezone.utc).astimezone(MSK)
    last = datetime.fromtimestamp(values[-1], timezone.utc).astimezone(MSK)
    return f"{first:%Y-%m-%d} — {last:%Y-%m-%d} MSK"


def fmt(value: float | None, digits: int = 2) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return f"{value:.{digits}f}"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        rows = [["нет данных за период"] + ["—"] * (len(headers) - 1)]
    result = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    result.extend(
        "| " + " | ".join(str(cell).replace("|", r"\|").replace("\n", " ") for cell in row) + " |"
        for row in rows
    )
    return "\n".join(result)


def strategy(value: Any) -> str:
    name = str(value or "").lower()
    if any(token in name for token in ("multi_top", "triple_top", "triple_bottom")):
        return "multi-top"
    if "wedge" in name:
        return "wedge"
    if "channel" in name:
        return "channel"
    return "other"


def contour(row: dict[str, Any]) -> str:
    for key in ("environment", "env", "contour", "account_environment"):
        value = str(row.get(key) or "").strip().lower()
        if value in {"vst", "demo", "virtual", "testnet"} or "vst" in value:
            return "VST"
        if value in {"real", "mainnet", "production"} or "mainnet" in value:
            return "REAL"
    for key in ("is_vst", "vst"):
        if row.get(key) is True or str(row.get(key)).lower() in {"1", "true", "yes"}:
            return "VST"
    for key in ("is_real", "mainnet"):
        if row.get(key) is True or str(row.get(key)).lower() in {"1", "true", "yes"}:
            return "REAL"
    return "UNKNOWN"


def paper_trades(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coalesce close legs by id so one paper trade is counted once."""
    grouped: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        key = str(row.get("id") or f"row-{index}")
        item = grouped.setdefault(
            key,
            {
                "id": key,
                "symbol": row.get("symbol") or "unknown",
                "side": row.get("direction") or row.get("side") or "unknown",
                "strategy": row.get("strategy") or "unknown",
                "pnl": 0.0,
                "fees": 0.0,
                "r_multiple": 0.0,
                "opened_at": None,
                "closed_at": None,
            },
        )
        for field in ("pnl", "fees", "r_multiple"):
            item[field] += number(row.get(field)) or 0.0
        for field in ("opened_at", "closed_at"):
            value = epoch(row.get(field))
            if value is not None:
                old = item[field]
                item[field] = value if old is None else (
                    min(old, value) if field == "opened_at" else max(old, value)
                )
    return list(grouped.values())


def exits(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row for row in rows
        if row.get("event") == "exit"
        and number(row.get("pnl")) is not None
        and row.get("pending_verify") is not True
    ]


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [number(row.get("pnl")) for row in rows]
    pnl_values = [value for value in pnls if value is not None]
    wins = [value for value in pnl_values if value > 0]
    losses = [value for value in pnl_values if value < 0]
    r_values = [number(row.get("r_multiple")) for row in rows]
    valid_r = [value for value in r_values if value is not None]
    gain, loss = sum(wins), abs(sum(losses))
    pf = gain / loss if loss else (math.inf if gain else None)
    return {
        "n": len(pnl_values),
        "winrate": len(wins) / len(pnl_values) if pnl_values else None,
        "pf": pf,
        "avg_r": statistics.fmean(valid_r) if valid_r else None,
        "expectancy": statistics.fmean(pnl_values) if pnl_values else None,
        "fees": sum(number(row.get("fees")) or 0 for row in rows),
    }


def trade_period(rows: list[dict[str, Any]], is_paper: bool) -> str:
    if is_paper:
        return period(
            epoch(row.get("opened_at")) or epoch(row.get("closed_at")) for row in rows
        )
    return period(stamp(row, "opened_at", "ts", "closed_at") for row in rows)


def metric_row(
    name: str, rows: list[dict[str, Any]], source: str, kind: str, is_paper: bool = False
) -> list[Any]:
    value = metrics(rows)
    pf = "∞" if value["pf"] == math.inf else fmt(value["pf"])
    count = f'{value["n"]} (мало данных)' if 0 < value["n"] < 20 else str(value["n"])
    return [
        kind, name, pf,
        fmt(value["winrate"] * 100 if value["winrate"] is not None else None),
        fmt(value["avg_r"], 3), fmt(value["expectancy"], 4), fmt(value["fees"], 4),
        trade_period(rows, is_paper), source, count,
    ]


def session_data(
    rows: list[dict[str, Any]], source: str, kind: str, is_paper: bool
) -> tuple[list[list[Any]], list[list[Any]]]:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        when = (
            epoch(row.get("opened_at")) or epoch(row.get("closed_at"))
            if is_paper else stamp(row, "opened_at", "ts")
        )
        if when is None:
            continue
        local = datetime.fromtimestamp(when, timezone.utc).astimezone(MSK)
        groups[
            (
                raw_bucket(local),
                "выходные" if local.weekday() >= 5 else "будни",
                str(row.get("symbol") or "unknown"),
                str(row.get("direction") or row.get("side") or "unknown").lower(),
            )
        ].append(row)

    summary: list[list[Any]] = []
    for profile in ("fat", "normal", "thin", "off"):
        for day in ("будни", "выходные"):
            members = [
                row for key, values in groups.items()
                if key[:2] == (profile, day) for row in values
            ]
            count = (
                f"{len(members)} (мало данных)" if 0 < len(members) < 20
                else str(len(members)) if members else "нет данных за период"
            )
            summary.append([
                kind, profile, day, count, trade_period(members, is_paper) if members else "нет данных",
                source, count,
            ])
    detail = []
    for (profile, day, symbol, side), members in sorted(groups.items()):
        count = f"{len(members)} (мало данных)" if len(members) < 20 else str(len(members))
        detail.append([
            kind, profile, day, symbol, side, count,
            trade_period(members, is_paper), source, count,
        ])
    return summary, detail


def zone_metrics(journal: list[dict[str, Any]], zone_state: Any) -> tuple[list[list[Any]], list[str]]:
    observations = [row for row in journal if row.get("event") == "zone_shadow"]
    summaries = [row for row in journal if row.get("event") == "zone_shadow_summary"]
    errors = [row for row in journal if row.get("event") == "zone_shadow_error"]
    distances: list[float] = []
    for row in observations:
        zones = row.get("zones")
        if isinstance(zones, list):
            distances.extend(
                value for zone in zones if isinstance(zone, dict)
                and (value := number(zone.get("min_distance_pct"))) is not None
                and value >= 0
            )
        for key in ("nearest_above", "nearest_below"):
            nearest = row.get(key)
            if isinstance(nearest, dict):
                value = number(nearest.get("distance_pct"))
                if value is not None:
                    distances.append(abs(value))

    has_events = bool(observations or summaries or errors)
    missing = "нет данных за период"
    period_obs = period(stamp(row, "ts") for row in observations)
    relation = "наблюдения, не сделки; trade_id отсутствует"
    rows = [
        ["approach observations",
         sum(bool(row.get("zones") or row.get("nearest_above") or row.get("nearest_below")) for row in observations) if has_events else missing,
         period_obs, LIVE_JOURNAL, "live (env не записан)", relation],
        ["zone_shadow ticks", len(observations) if has_events else missing,
         period_obs, LIVE_JOURNAL, "live (env не записан)", relation],
        ["in-zone observations",
         sum(row.get("in_zone") is True for row in observations) if has_events else missing,
         period_obs, LIVE_JOURNAL, "live (env не записан)", relation],
        ["minimum observed distance, %",
         fmt(min(distances), 4) if distances else missing,
         period(stamp(row, "ts") for row in observations), LIVE_JOURNAL,
         "live (env не записан)", relation],
        ["zone summaries", len(summaries) if has_events else missing,
         period(stamp(row, "ts") for row in summaries), LIVE_JOURNAL,
         "live (env не записан)", relation],
        ["zone errors", len(errors) if has_events else missing,
         period(stamp(row, "ts") for row in errors), LIVE_JOURNAL,
         "live (env не записан)", relation],
    ]
    notes = []
    if not has_events:
        notes.append("нет данных за период: models/zeus_live_journal.jsonl не содержит zone_shadow events.")
    if isinstance(zone_state, dict) and zone_state:
        count = 0
        mins: list[float] = []
        times: list[float | None] = []
        for item in zone_state.values():
            if not isinstance(item, dict):
                continue
            times.append(epoch(item.get("bar_ts")))
            zones = item.get("zones")
            if isinstance(zones, dict):
                count += len(zones)
                mins.extend(value for raw in zones.values() if (value := number(raw)) is not None)
        rows.append([
            "current snapshot (not historical count)",
            f"{count} zones; min distance {_fmt_or_missing(min(mins) if mins else None)}",
            period(times), ZONE_STATE, "live (env не записан)",
            "snapshot, не сделки; без trade_id",
        ])
    return rows, notes


def _fmt_or_missing(value: float | None) -> str:
    return fmt(value, 4) if value is not None else "нет данных"


def report_json_rows(reports: Path, output: Path) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    if not reports.exists():
        return result
    dashboard_output = reports / "astra_dashboard"
    for path in sorted(
        candidate for candidate in reports.rglob("*")
        if candidate.is_file()
        and candidate.suffix.lower() in {".json", ".jsonl"}
        and output not in candidate.parents
        and dashboard_output not in candidate.parents
    ):
        if path.suffix.lower() == ".jsonl":
            rows, _ = read_jsonl(path)
        else:
            raw = read_json(path)
            rows = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
        result.extend(
            (path.relative_to(reports.parent).as_posix(), row)
            for row in rows if isinstance(row, dict)
        )
    return result


def build_report(root: Path) -> str:
    root = root.resolve()
    reports = root / "reports"
    output = reports / "zeus_analytics"
    paper_raw, paper_bad = read_jsonl(root / PAPER)
    live_rows, live_bad = read_jsonl(root / LIVE_TRADES)
    entry_rows, entry_bad = read_jsonl(root / LIVE_ENTRIES)
    journal, journal_bad = read_jsonl(root / LIVE_JOURNAL)
    paper = paper_trades(paper_raw)
    closed = exits(live_rows)

    entries_by_id = {
        str(row.get("signal_id")): stamp(row, "ts")
        for row in live_rows if row.get("event") == "entry" and row.get("signal_id")
    }
    # Attach entry timestamps only in memory for session bucketing.
    for row in closed:
        signal_id = row.get("signal_id")
        if signal_id:
            row["opened_at"] = entries_by_id.get(str(signal_id))

    by_kind: dict[str, dict[str, list[dict[str, Any]]]] = {
        "VST": defaultdict(list), "REAL": defaultdict(list), "UNKNOWN": defaultdict(list)
    }
    paper_by_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in paper:
        paper_by_strategy[strategy(row.get("strategy"))].append(row)
    for row in closed:
        by_kind[contour(row)][strategy(row.get("strategy") or str(row.get("signal_id", "")).split("|")[0])].append(row)

    out = [
        "# ZEUS analytics", "",
        "Read-only отчёт из репозиторных JSON/JSONL. Генератор не меняет models/ или исходные reports/.",
        "Paper, явно маркированный VST, явно маркированный REAL и live UNKNOWN разделены. UNKNOWN не считается VST.",
        "PF = gross positive pnl / abs(gross negative pnl). Fees показаны отдельно, второй раз из pnl не вычитаются.",
        "Paper partial-close rows объединены по id; PnL, fees и R суммируются по legs в рамках одной сделки.",
        "", "## 1. Стратегии", "",
    ]
    headers = [
        "контур", "стратегия", "PF", "winrate, %", "средний R",
        "expectancy, USDT/сделка", "fees, USDT", "период MSK",
        "источник", "n сделок",
    ]
    rows: list[list[Any]] = []
    for kind, grouped, source, is_paper in (
        ("paper", paper_by_strategy, PAPER, True),
        ("VST", by_kind["VST"], LIVE_TRADES, False),
        ("REAL", by_kind["REAL"], LIVE_TRADES, False),
        ("live / UNKNOWN", by_kind["UNKNOWN"], LIVE_TRADES, False),
    ):
        for item in STRATEGIES:
            trades = grouped.get(item, [])
            rows.append(
                metric_row(item, trades, source, kind, is_paper)
                if trades else [kind, item, "нет данных", "нет данных", "нет данных",
                                "нет данных", "нет данных", "нет данных", source, "0 (нет данных)"]
            )
    out.append(table(headers, rows))
    out.extend([
        "",
        "Live environment определяется только по явному env/environment/contour/is_vst/is_real в строке. Отсутствие маркировки не восполняется значением workflow по умолчанию.",
        "", "## 2. Профили сессий", "",
        "Час открытия сделки в MSK; profile вычисляет та же raw_bucket() из live_session.py. Разбивка: fat/normal/thin/off × будни/выходные × symbol × direction.",
        "",
    ])
    sh = ["контур", "профиль", "день", "n сделок", "период MSK", "источник", "n / статус"]
    dh = ["контур", "профиль", "день", "символ", "направление", "n сделок",
          "период MSK", "источник", "n / статус"]
    for kind, trades, source, is_paper in (
        ("paper", paper, PAPER, True),
        ("VST", [r for r in closed if contour(r) == "VST"], LIVE_TRADES, False),
        ("REAL", [r for r in closed if contour(r) == "REAL"], LIVE_TRADES, False),
        ("live / UNKNOWN", [r for r in closed if contour(r) == "UNKNOWN"], LIVE_TRADES, False),
    ):
        summary, detail = session_data(trades, source, kind, is_paper)
        out.extend([f"### {kind} — summary", "", table(sh, summary), "",
                    f"### {kind} — symbol × direction", "", table(dh, detail), ""])
    out.extend(["## 3. Volume-zone shadow", ""])
    zone_state = read_json(root / ZONE_STATE)
    zone_rows, zone_notes = zone_metrics(journal, zone_state)
    out.append(table(
        ["метрика", "значение", "период MSK", "источник", "контур", "связь / n сделок"],
        zone_rows,
    ))
    out.extend(["", *zone_notes, "",
                "Approach/in-zone — число наблюдений, не число сделок. Zone rows не содержат trade_id и environment."])
    out.extend(["", "## 4. Воронка сигналов", ""])
    has_journal = bool(journal)
    pending = [row for row in journal if row.get("event") == "pending_signal"]
    summaries = [row for row in journal if row.get("event") == "arm_summary"]
    skips: dict[str, int] = defaultdict(int)
    for row in summaries:
        values = row.get("skipped")
        if isinstance(values, dict):
            for key, value in values.items():
                skips[str(key)] += int(number(value) or 0)
    if not skips:
        for row in journal:
            if row.get("event") == "arm_skip":
                skips[str(row.get("reason") or "unknown")] += 1
    entry_fills = [row for row in live_rows if row.get("event") == "entry"]
    failures = [
        row for row in entry_rows
        if str(row.get("event") or "").startswith(("entry_failed", "entry_rejected"))
    ]
    result_rows = [
        ["scan attempts", "нет данных за период", "нет данных", LIVE_JOURNAL,
         "live (env не записан)", "по символам не журналируются"],
        ["pending_signal candidates", len(pending) if has_journal else "нет данных за период",
         period(stamp(row, "ts") for row in pending), LIVE_JOURNAL,
         "live (env не записан)", "не trade-level"],
        ["arm_summary candidate count",
         sum(int(number(row.get("candidates")) or 0) for row in summaries) if has_journal else "нет данных за период",
         period(stamp(row, "ts") for row in summaries), LIVE_JOURNAL,
         "live (env не записан)", "summed from arm_summary"],
        ["successful entry records", len(entry_fills),
         period(stamp(row, "ts") for row in entry_fills), LIVE_TRADES,
         "live (env не записан)", "n entry events"],
        ["failed/rejected entry records", len(failures),
         period(stamp(row, "ts") for row in failures), LIVE_ENTRIES,
         "live (env не записан)", "n failure rows"],
        ["verified exit results", len(closed),
         period(stamp(row, "ts") for row in closed), LIVE_TRADES,
         "live (env не записан)", "n exits with pnl"],
    ]
    out.append(table(["этап", "значение", "период MSK", "источник", "контур", "связь / n сделок"], result_rows))
    out.extend(["", "### Отбраковки по причинам", ""])
    skip_rows = [
        [reason, count, period(stamp(row, "ts") for row in summaries),
         LIVE_JOURNAL, "live (env не записан)", "arm_summary aggregated skips"]
        for reason, count in sorted(skips.items())
    ]
    if not has_journal:
        skip_rows = [["нет данных за период", "—", "нет данных", LIVE_JOURNAL,
                      "live (env не записан)", "журнал пуст или отсутствует"]]
    out.append(table(["причина", "n", "период MSK", "источник", "контур", "связь / n сделок"], skip_rows))
    out.extend([
        "", "Candidate/skip funnel starts at persisted pending_signal and arm_summary events. Per-symbol raw scan attempts are not logged.",
        "", "## 5. Здоровье live и COPY_ZEUS", "",
    ])
    tick_rows = [
        row for row in journal if row.get("event") in {"zone_shadow", "zone_shadow_error", "arm_summary"}
    ]
    tick_stamps = sorted(
        value for row in tick_rows if (value := stamp(row, "ts")) is not None
    )
    batches: list[float] = []
    for value in tick_stamps:
        if not batches or value - batches[-1] > 120:
            batches.append(value)
    gaps = [(batches[i] - batches[i - 1]) / 60 for i in range(1, len(batches))]
    errors = [
        row for row in journal
        if str(row.get("event") or "").endswith("_error")
        or row.get("error") is not None or row.get("error_msg") is not None
    ]
    journal_reconciliation = [
        row for row in journal
        if any(key in str(row.get("event") or "").lower()
               for key in ("reconcil", "ghost", "missing"))
    ]
    live_reconciliation = [
        row for row in live_rows
        if row.get("event") in {"qty_reconcile", "position_reconcile", "ghost_position"}
    ]
    pending_verify_rows = [
        row for row in entry_rows if row.get("event") == "entry_pending_verify"
    ] + [
        row for row in live_rows
        if row.get("event") == "exit" and row.get("pending_verify") is True
    ]
    reconciliation = journal_reconciliation + live_reconciliation + pending_verify_rows
    protective_failures = [
        row for row in live_rows
        if row.get("event") in {"restore_tp_failed", "protect_place_failed"}
    ]
    entry_failures = [
        row for row in entry_rows
        if str(row.get("event") or "").startswith(("entry_failed", "entry_rejected"))
    ]
    report_records = report_json_rows(root / "reports", output)
    rate_rows = [
        (source, row) for source, row in (
            [(LIVE_JOURNAL, row) for row in journal]
            + [(LIVE_ENTRIES, row) for row in entry_rows]
            + [(LIVE_TRADES, row) for row in live_rows]
            + report_records
        ) if "100410" in json.dumps(row, ensure_ascii=False, default=str)
    ]
    archive_rows = [
        (source, row) for source, row in report_records
        if "copy_zeus" in source.lower() or "copyzeus" in source.lower()
        or "archive" in str(row.get("event") or "").lower()
    ]
    archive_errors = [
        row for _, row in archive_rows
        if row.get("error") is not None
        or "100410" in json.dumps(row, ensure_ascii=False, default=str)
    ]
    jmissing = "нет данных за период" if not journal else 0
    health = [
        ["observed live tick batches (inferred; group gap ≤120s)",
         len(batches) if journal else jmissing, period(batches), LIVE_JOURNAL,
         "live (env не записан)", "не trade-level"],
        ["tick interval min / median / max, min",
         f"{fmt(min(gaps))} / {fmt(statistics.median(gaps))} / {fmt(max(gaps))}" if gaps else jmissing,
         period(batches), LIVE_JOURNAL, "live (env не записан)", "не trade-level"],
        ["journal errors", len(errors) if journal else jmissing,
         period(stamp(row, "ts") for row in errors), LIVE_JOURNAL,
         "live (env не записан)", "не обязательно сделки"],
        ["restore_tp_failed / protect_place_failed",
         len(protective_failures) if live_rows else "нет данных за период",
         period(stamp(row, "ts") for row in protective_failures), LIVE_TRADES,
         "live (env не записан)", "protective-order diagnostics; не сделки"],
        ["entry_failed / entry_rejected",
         len(entry_failures) if entry_rows else "нет данных за период",
         period(stamp(row, "ts") for row in entry_failures), LIVE_ENTRIES,
         "live (env не записан)", "n failed entry records"],
        ["reconciliation / ghost / pending_verify events",
         len(reconciliation) if journal or live_rows or entry_rows else "нет данных за период",
         period(stamp(row, "ts") for row in reconciliation),
         f"{LIVE_JOURNAL}; {LIVE_TRADES}; {LIVE_ENTRIES}", "live (env не записан)",
         "qty_reconcile + pending_verify; не обязательно сделки"],
        ["100410 occurrences in readable structured rows", len(rate_rows),
         period(stamp(row, "ts", "timestamp") for _, row in rate_rows),
         "; ".join(sorted({source for source, _ in rate_rows})) or "нет источника",
         "контур по источнику/метке; иначе UNKNOWN", "не обязательно сделки"],
        ["COPY_ZEUS archive ticks", len(archive_rows) if archive_rows else "нет данных за период",
         period(stamp(row, "ts", "timestamp") for _, row in archive_rows),
         "; ".join(sorted({source for source, _ in archive_rows})) or "reports/ structured files",
         "COPY_ZEUS / REAL read-only", "не сделки"],
        ["COPY_ZEUS errors / 100410", len(archive_errors) if archive_rows else "нет данных за период",
         period(stamp(row, "ts", "timestamp") for row in archive_errors),
         "; ".join(sorted({source for source, _ in archive_rows})) or "reports/ structured files",
         "COPY_ZEUS / REAL read-only", "не сделки"],
    ]
    out.append(table(["показатель", "значение", "период MSK", "источник", "контур", "связь / n сделок"], health))
    if not archive_rows:
        out.extend(["", "нет данных за период по COPY_ZEUS: encrypted Actions artifacts и console logs не входят в доступные models/reports JSON/JSONL."])
    if not journal:
        out.extend(["", "нет данных за период по zone/funnel/tick events: models/zeus_live_journal.jsonl пуст."])
    out.extend([
        "", "## Ограничения выборки", "",
        f"- Paper: {len(paper)} unique ids from {len(paper_raw)} rows/legs; source {PAPER}; это paper, не VST.",
        f"- Live exits: {len(closed)} rows with pnl and without pending_verify. VST={sum(contour(row) == 'VST' for row in closed)}, REAL={sum(contour(row) == 'REAL' for row in closed)}, UNKNOWN={sum(contour(row) == 'UNKNOWN' for row in closed)}; source {LIVE_TRADES}.",
        f"- Pending verify exits excluded from metrics: {sum(row.get('event') == 'exit' and row.get('pending_verify') is True for row in live_rows)}.",
        "- Environment metadata отсутствует в части live rows; такие записи не классифицируются как VST.",
        "- Zone events are observation-level and have no trade_id. Current approach-state JSON is shown only as a snapshot.",
        "- Tick batches are inferred from journal timestamps and are not exact GitHub Actions run counts.",
        "- COPY_ZEUS encrypted artifact is not read/decrypted by this script. Only structured reports JSON/JSONL can contribute archive telemetry.",
        f"- Malformed lines skipped: paper={paper_bad}, live_trades={live_bad}, live_entries={entry_bad}, live_journal={journal_bad}.",
        "", "## Reproducibility", "",
        "Same inputs produce identical summary.md. No network, API, Secrets, current clock, workflow, trading code or state writes.",
        "",
    ])
    return "\n".join(out)


def generate(root: Path) -> Path:
    destination = root.resolve() / "reports" / "zeus_analytics" / "summary.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = build_report(root)
    temporary = destination.with_suffix(".md.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(f"Zeus analytics written: {generate(args.root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
