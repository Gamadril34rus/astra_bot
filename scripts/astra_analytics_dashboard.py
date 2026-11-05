#!/usr/bin/env python3
"""Build a read-only ASTRA dashboard from local repository journals."""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from astra_bot.decision.live_session import MSK, raw_bucket  # noqa: E402
from scripts.zeus_analytics import (  # noqa: E402
    LIVE_ENTRIES, LIVE_JOURNAL, LIVE_TRADES, PAPER, ZONE_STATE,
    contour, epoch, exits, metrics, number, paper_trades, read_json,
    read_jsonl, stamp, strategy,
)

LABELS = {"paper": "PAPER", "VST": "DEMO / VST", "REAL": "LIVE / REAL", "UNKNOWN": "LIVE / UNKNOWN"}
NO_TRADE_OBSERVATIONS = "models/zeus_no_trade_observations.jsonl"
NO_TRADE_OUTCOMES = "models/zeus_no_trade_outcomes.jsonl"
GOLD, GREEN, RED = "#d6b46a", "#23c982", "#ed5b62"


def _time(row: dict[str, Any], paper: bool, opening: bool = False) -> float | None:
    if paper:
        return epoch(row.get("opened_at" if opening else "closed_at")) or epoch(row.get("closed_at"))
    return epoch(row.get("opened_at")) if opening else stamp(row, "ts", "closed_at", "timestamp")


def _pnl_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = metrics(rows)
    pnl = [v for r in rows if (v := number(r.get("pnl"))) is not None]
    winners, losers = [v for v in pnl if v > 0], [v for v in pnl if v < 0]
    result.update({
        "gross_profit": sum(winners), "gross_loss": sum(losers),
        "net_pnl": sum(pnl), "wins": len(winners), "losses": len(losers),
    })
    return result


def _strategy_label(value: Any) -> str:
    raw = str(value or "unknown").strip()
    canonical = strategy(raw)
    return canonical if canonical != "other" else raw.lower().replace("_", "-")


def _timeline(rows: list[dict[str, Any]], paper: bool) -> list[dict[str, Any]]:
    ordered = sorted(
        ((ts, number(r.get("pnl"))) for r in rows
         if (ts := _time(r, paper)) is not None and number(r.get("pnl")) is not None),
        key=lambda x: x[0],
    )
    total = peak = 0.0
    out = []
    for ts, pnl in ordered:
        total += float(pnl)
        peak = max(peak, total)
        out.append({
            "timestamp": ts,
            "date": datetime.fromtimestamp(ts, timezone.utc).astimezone(MSK).isoformat(timespec="minutes"),
            "pnl": float(pnl), "cumulative_pnl": total, "drawdown": total - peak,
        })
    return out


def _sessions(rows: list[dict[str, Any]], paper: bool) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        ts = _time(row, paper, opening=True)
        if ts is None:
            continue
        dt = datetime.fromtimestamp(ts, timezone.utc).astimezone(MSK)
        groups[(raw_bucket(dt), "выходные" if dt.weekday() >= 5 else "будни")].append(row)
    out = []
    for (profile, day), items in sorted(groups.items()):
        m = _pnl_metrics(items)
        out.append({"profile": profile, "day_type": day, "trades": m["n"],
                    "win_rate": m["winrate"], "net_pnl": m["net_pnl"]})
    return out


def _session_details(rows: list[dict[str, Any]], paper: bool) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        ts = _time(row, paper, opening=True)
        if ts is None:
            continue
        dt = datetime.fromtimestamp(ts, timezone.utc).astimezone(MSK)
        side = str(row.get("direction") or row.get("side") or "unknown").lower()
        groups[(raw_bucket(dt), "выходные" if dt.weekday() >= 5 else "будни",
                str(row.get("symbol") or "unknown"), side)].append(row)
    out = []
    for (profile, day, symbol, side), items in sorted(groups.items()):
        m = _pnl_metrics(items)
        out.append({"profile": profile, "day_type": day, "symbol": symbol,
                    "side": side, "trades": m["n"], "net_pnl": m["net_pnl"],
                    "win_rate": m["winrate"]})
    return out


def _no_trade_summary(
    observations: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    *,
    observations_available: bool,
    outcomes_available: bool,
) -> dict[str, Any]:
    """Summarize persisted no-trade observations and their forward outcomes.

    An observation is a per-symbol/bar evaluation record, not a count of bot
    ticks or exchange scans. Outcomes are joined by the stable observation ID.
    """
    outcome_by_id = {
        str(row.get("id")): row
        for row in outcomes
        if row.get("id") not in (None, "")
    }
    reasons: Counter[str] = Counter()
    stages: Counter[str] = Counter()
    regimes: Counter[str] = Counter()
    symbols: Counter[str] = Counter()
    candidate_strategies: Counter[str] = Counter()
    candidate_directions: Counter[str] = Counter()
    observations_with_outcome = 0
    hypothetical_r: list[float] = []
    horizon_values: dict[str, dict[str, list[float]]] = {
        horizon: {"future_return": [], "max_up": [], "max_down": []}
        for horizon in ("1", "3", "6", "12", "24")
    }

    for observation in observations:
        reasons[str(observation.get("reason_code") or "UNKNOWN")] += 1
        stages[str(observation.get("rejection_stage") or "UNATTRIBUTED")] += 1
        regimes[str(observation.get("market_regime") or "UNKNOWN")] += 1
        if observation.get("symbol"):
            symbols[str(observation["symbol"])] += 1

        candidate = observation.get("candidate")
        if isinstance(candidate, dict):
            candidate_strategies[str(candidate.get("strategy") or "UNKNOWN")] += 1
            candidate_directions[str(candidate.get("direction") or "UNKNOWN")] += 1

        outcome = outcome_by_id.get(str(observation.get("id") or ""))
        if not isinstance(outcome, dict):
            outcome = {}
        result = observation.get("result")
        result = result if isinstance(result, dict) else {}
        horizons = outcome.get("horizons")
        if not isinstance(horizons, dict):
            horizons = result
        has_outcome = False
        for horizon in ("1", "3", "6", "12", "24"):
            row = horizons.get(horizon) if isinstance(horizons, dict) else None
            if not isinstance(row, dict):
                continue
            for metric in ("future_return", "max_up", "max_down"):
                value = number(row.get(metric))
                if value is not None:
                    horizon_values[horizon][metric].append(value * 100.0)
                    if metric == "future_return":
                        has_outcome = True
        if has_outcome:
            observations_with_outcome += 1

        hyp = outcome.get("hypothetic_r")
        if isinstance(hyp, dict):
            value = number(hyp.get("r"))
            if value is not None:
                hypothetical_r.append(value)

    horizon_summary = {}
    for horizon, values in horizon_values.items():
        future = values["future_return"]
        horizon_summary[horizon] = {
            "samples": len(future),
            "avg_future_return_pct": sum(future) / len(future) if future else None,
            "avg_mfe_pct": (
                sum(values["max_up"]) / len(values["max_up"])
                if values["max_up"] else None
            ),
            "avg_mae_pct": (
                sum(values["max_down"]) / len(values["max_down"])
                if values["max_down"] else None
            ),
        }

    r_count = len(hypothetical_r)
    return {
        "observations_available": observations_available,
        "outcomes_available": outcomes_available,
        "observations_count": len(observations) if observations_available else None,
        "outcome_rows": len(outcomes) if outcomes_available else None,
        "observations_with_outcome": (
            observations_with_outcome
            if observations_available or outcomes_available else None
        ),
        "outcome_coverage_pct": (
            observations_with_outcome * 100.0 / len(observations)
            if observations and (observations_available or outcomes_available) else None
        ),
        "candidate_count": sum(
            1 for row in observations if isinstance(row.get("candidate"), dict)
        ) if observations_available else None,
        "reason_codes": dict(sorted(reasons.items())),
        "rejection_stages": dict(sorted(stages.items())),
        "market_regimes": dict(sorted(regimes.items())),
        "symbols": dict(sorted(symbols.items())),
        "candidate_strategies": dict(sorted(candidate_strategies.items())),
        "candidate_directions": dict(sorted(candidate_directions.items())),
        "horizons": horizon_summary,
        "hypothetic_r": {
            "samples": r_count,
            "avg_r": sum(hypothetical_r) / r_count if r_count else None,
            "positive_rate_pct": (
                sum(value > 0 for value in hypothetical_r) * 100.0 / r_count
                if r_count else None
            ),
        },
        "note": (
            "NO_TRADE observation = one persisted symbol/bar evaluation, not a tick or exchange scan. "
            "Forward outcomes are joined by stable observation ID and use only persisted horizon data."
        ),
    }


def build_payload(root: Path) -> dict[str, Any]:
    root = root.resolve()
    paper_raw, paper_bad = read_jsonl(root / PAPER)
    live_rows, live_bad = read_jsonl(root / LIVE_TRADES)
    entry_rows, entry_bad = read_jsonl(root / LIVE_ENTRIES)
    journal, journal_bad = read_jsonl(root / LIVE_JOURNAL)
    no_trade_obs_path = root / NO_TRADE_OBSERVATIONS
    no_trade_outcomes_path = root / NO_TRADE_OUTCOMES
    no_trade_observations, no_trade_obs_bad = read_jsonl(no_trade_obs_path)
    no_trade_outcomes, no_trade_outcomes_bad = read_jsonl(no_trade_outcomes_path)
    no_trade = _no_trade_summary(
        no_trade_observations, no_trade_outcomes,
        observations_available=no_trade_obs_path.is_file(),
        outcomes_available=no_trade_outcomes_path.is_file(),
    )
    paper_rows, live_closed = paper_trades(paper_raw), exits(live_rows)
    entry_times = {
        str(r.get("signal_id")): stamp(r, "ts")
        for r in live_rows if r.get("event") == "entry" and r.get("signal_id")
    }
    for row in live_closed:
        if row.get("signal_id"):
            row["opened_at"] = entry_times.get(str(row["signal_id"]))

    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {
        k: defaultdict(list) for k in ("paper", "VST", "REAL", "UNKNOWN")
    }
    for row in paper_rows:
        grouped["paper"][_strategy_label(row.get("strategy"))].append(row)
    for row in live_closed:
        grouped[contour(row)][_strategy_label(row.get("strategy") or str(row.get("signal_id", "")).split("|")[0])].append(row)

    strategy_rows = []
    for mode, by_strategy in grouped.items():
        for name, items in sorted(by_strategy.items()):
            if items:
                strategy_rows.append({"mode": LABELS[mode], "strategy": name, **_pnl_metrics(items)})

    timelines = {
        mode: _timeline(paper_rows if mode == "paper" else [r for r in live_closed if contour(r) == mode],
                        paper=(mode == "paper"))
        for mode in ("paper", "VST", "REAL", "UNKNOWN")
    }
    sessions = {
        mode.upper(): _sessions(paper_rows if mode == "paper"
                                else [r for r in live_closed if contour(r) == mode],
                                paper=(mode == "paper"))
        for mode in ("paper", "VST", "REAL", "UNKNOWN")
    }
    session_details = {
        mode.upper(): _session_details(paper_rows if mode == "paper"
                                       else [r for r in live_closed if contour(r) == mode],
                                       paper=(mode == "paper"))
        for mode in ("paper", "VST", "REAL", "UNKNOWN")
    }

    pending = [r for r in journal if r.get("event") == "pending_signal"]
    arm = [r for r in journal if r.get("event") == "arm_summary"]
    skips: dict[str, int] = defaultdict(int)
    for row in arm:
        if isinstance(row.get("skipped"), dict):
            for reason, count in row["skipped"].items():
                skips[str(reason)] += int(number(count) or 0)
    if not skips:
        for row in journal:
            if row.get("event") == "arm_skip":
                skips[str(row.get("reason") or "unknown")] += 1

    zones = [r for r in journal if r.get("event") == "zone_shadow"]
    zone_errors = [r for r in journal if r.get("event") == "zone_shadow_error"]
    distances = []
    symbols = set()
    for row in zones:
        if row.get("symbol"):
            symbols.add(str(row["symbol"]))
        if isinstance(row.get("zones"), list):
            for zone in row["zones"]:
                if isinstance(zone, dict) and (v := number(zone.get("min_distance_pct"))) is not None and v >= 0:
                    distances.append(v)
        for key in ("nearest_above", "nearest_below"):
            nearest = row.get(key)
            if isinstance(nearest, dict) and (v := number(nearest.get("distance_pct"))) is not None:
                distances.append(abs(v))
    snapshot = read_json(root / ZONE_STATE)
    snapshot_per_symbol = {
        str(symbol): len(item.get("zones", {}))
        for symbol, item in (snapshot.items() if isinstance(snapshot, dict) else [])
        if isinstance(item, dict) and isinstance(item.get("zones"), dict)
    }
    snapshot_zones = sum(snapshot_per_symbol.values())

    errors = [r for r in journal if str(r.get("event") or "").endswith("_error")
              or r.get("error") is not None or r.get("error_msg") is not None]
    protection = [r for r in live_rows if r.get("event") in {"restore_tp_failed", "protect_place_failed"}]
    reconcile = [r for r in live_rows if r.get("event") in {"qty_reconcile", "position_reconcile", "ghost_position"}]
    rate = [
        (source, row) for source, rows in ((LIVE_TRADES, live_rows), (LIVE_ENTRIES, entry_rows), (LIVE_JOURNAL, journal))
        for row in rows if "100410" in json.dumps(row, ensure_ascii=False, default=str)
    ]

    return {
        "title": "ASTRA BOT by Gamadril",
        "generated_at_msk": datetime.now(timezone.utc).astimezone(MSK).isoformat(timespec="seconds"),
        "sources": [
            PAPER, LIVE_TRADES, LIVE_ENTRIES, LIVE_JOURNAL, ZONE_STATE,
            NO_TRADE_OBSERVATIONS, NO_TRADE_OUTCOMES,
        ],
        "data_quality": {
            "paper_source_rows": len(paper_raw), "paper_trades": len(paper_rows),
            "live_rows": len(live_rows), "verified_live_exits": len(live_closed),
            "live_entry_events": sum(r.get("event") == "entry" for r in live_rows),
            "journal_rows": len(journal),
            "malformed_jsonl": {"paper": paper_bad, "live_trades": live_bad,
                                "live_entries": entry_bad, "live_journal": journal_bad,
                                "no_trade_observations": no_trade_obs_bad,
                                "no_trade_outcomes": no_trade_outcomes_bad},
            "no_trade_observation_rows": len(no_trade_observations) if no_trade_obs_path.is_file() else None,
            "no_trade_outcome_rows": len(no_trade_outcomes) if no_trade_outcomes_path.is_file() else None,
            "environment_classification": {
                mode: sum(contour(r) == mode for r in live_closed) for mode in ("VST", "REAL", "UNKNOWN")
            },
        },
        "strategy_metrics": strategy_rows,
        "timelines": timelines,
        "sessions": sessions,
        "session_details": session_details,
        "volume_zone_shadow": {
            "available": bool(zones or zone_errors),
            "observations": len(zones) if zones or zone_errors else None,
            "errors": len(zone_errors) if zones or zone_errors else None,
            "in_zone_observations": sum(r.get("in_zone") is True for r in zones) if zones or zone_errors else None,
            "min_distance_pct": min(distances) if distances else None,
            "symbols": sorted(symbols), "snapshot_zones": snapshot_zones,
            "snapshot_per_symbol": snapshot_per_symbol,
            "snapshot_symbols": len(snapshot) if isinstance(snapshot, dict) else 0,
            "note": "Snapshot is current state, not historical approach count; zone observations are not trades.",
        },
        "no_trade_analytics": no_trade,
        "signal_funnel": {
            "scan_attempts": None,
            "pending_candidates": len(pending) if journal else None,
            "arm_candidate_total": sum(int(number(r.get("candidates")) or 0) for r in arm) if journal else None,
            "skip_reasons": dict(sorted(skips.items())),
            "entry_events": sum(r.get("event") == "entry" for r in live_rows),
            "failed_entry_events": sum(str(r.get("event") or "").startswith(("entry_failed", "entry_rejected")) for r in entry_rows),
            "verified_exits": len(live_closed),
        },
        "system_health": {
            "journal_errors": len(errors) if journal else None,
            "protective_order_failures": len(protection) if live_rows else None,
            "reconciliation_events": len(reconcile) if live_rows else None,
            "bingx_100410_occurrences": len(rate),
            "bingx_100410_sources": sorted({source for source, _ in rate}),
            "workflow_tick_intervals": None,
        },
        "notes": [
            "PAPER, VST, REAL and UNKNOWN remain separate; missing row-level env stays UNKNOWN.",
            "Cumulative PnL is not an exchange account balance.",
            "NO_TRADE evaluations are counted from persisted zeus_no_trade_observations.jsonl rows; they are symbol/bar observations, not workflow ticks.",
            "Forward no-trade quality uses zeus_no_trade_outcomes.jsonl joined by stable observation ID and only persisted horizons.",
            "Exact workflow-run intervals remain unavailable unless a workflow-run source is persisted.",
            "No network calls; models/ and trading logic are never written.",
        ],
    }


def _money(v: Any) -> str:
    if v is None:
        return "нет данных"
    if math.isinf(float(v)):
        return "∞" if float(v) > 0 else "−∞"
    if not math.isfinite(float(v)):
        return "нет данных"
    return f"{float(v):,.2f}"


def _line(points: list[dict[str, Any]], key: str, title: str) -> str:
    values = [number(p.get(key)) for p in points]
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return '<div class="empty">Нет достаточных данных</div>'
    low, high = min(values), max(values)
    if low == high:
        high = low + 1
    coords = []
    for i, p in enumerate(points):
        v = number(p.get(key))
        if v is not None:
            x = 40 + i * 620 / max(1, len(points)-1)
            y = 180 - (v-low) * 130 / (high-low)
            coords.append(f"{x:.1f},{y:.1f}")
    return (f'<svg viewBox="0 0 700 220" role="img" aria-label="{html.escape(title)}">'
            f'<path d="M40 180 H660" stroke="#34404d"/>'
            f'<polyline points="{" ".join(coords)}" fill="none" stroke="{GOLD}" stroke-width="3" '
            'stroke-linecap="round" stroke-linejoin="round"/>'
            f'<text x="40" y="207" class="axis">{_money(low)}</text>'
            f'<text x="660" y="207" text-anchor="end" class="axis">{_money(high)}</text></svg>')


def _bars(items: list[dict[str, Any]], label: str, value: str) -> str:
    vals = [number(x.get(value)) for x in items]
    vals = [v for v in vals if v is not None]
    if not vals:
        return '<div class="empty">Нет данных</div>'
    maximum = max(abs(v) for v in vals) or 1
    content = []
    for i, item in enumerate(items[:12]):
        v = number(item.get(value))
        if v is None:
            continue
        width = max(1, abs(v) / maximum * 300)
        color = GREEN if v >= 0 else RED
        name = html.escape(str(item.get(label, "")))[:30]
        content.append(
            f'<g transform="translate(0 {i*25})"><text x="4" y="17" class="bar">{name}</text>'
            f'<rect x="255" y="3" width="{width:.1f}" height="16" rx="4" fill="{color}"/>'
            f'<text x="{min(660,262+width):.1f}" y="17" class="bar">{_money(v)}</text></g>'
        )
    return f'<svg viewBox="0 0 700 {max(90, len(content)*25+10)}">{"".join(content)}</svg>'


def _card(label: str, value: str, hint: str = "") -> str:
    return (f'<div class="metric"><span>{html.escape(label)}</span><b>{html.escape(value)}</b>'
            f'<small>{html.escape(hint)}</small></div>')


def render_html(p: dict[str, Any]) -> str:
    q, zone, funnel, health = p["data_quality"], p["volume_zone_shadow"], p["signal_funnel"], p["system_health"]
    no_trade = p["no_trade_analytics"]
    paper = [r for r in p["strategy_metrics"] if r["mode"] == "PAPER"]
    unknown = [r for r in p["strategy_metrics"] if r["mode"] == "LIVE / UNKNOWN"]
    paper_pnl = sum(r["net_pnl"] for r in paper)
    unknown_pnl = sum(r["net_pnl"] for r in unknown)
    strategy_bars = _bars(
        [{"name": f'{r["mode"]} · {r["strategy"]}', "pnl": r["net_pnl"]} for r in p["strategy_metrics"]],
        "name", "pnl",
    )
    session_items = [
        {"name": f'{mode} · {r["profile"]} · {r["day_type"]}', "pnl": r["net_pnl"]}
        for mode, rows in p["sessions"].items() for r in rows
    ]
    session_bars = _bars(session_items, "name", "pnl")
    detail_items = [
        {"name": f'{mode} · {r["symbol"]} · {r["profile"]} · {r["day_type"]} · {r["side"]}',
         "pnl": r["net_pnl"]}
        for mode, rows in p["session_details"].items() for r in rows
    ]
    detail_bars = _bars(detail_items, "name", "pnl")
    zone_items = [
        {"name": symbol, "count": count}
        for symbol, count in p["volume_zone_shadow"]["snapshot_per_symbol"].items()
    ]
    zone_bars = _bars(zone_items, "name", "count")
    skip_items = [{"name": key, "pnl": value} for key, value in funnel["skip_reasons"].items()]
    skip_bars = _bars(skip_items, "name", "pnl")
    no_trade_reason_bars = _bars(
        [{"name": key, "count": value} for key, value in no_trade["reason_codes"].items()],
        "name", "count",
    )
    no_trade_stage_bars = _bars(
        [{"name": key, "count": value} for key, value in no_trade["rejection_stages"].items()],
        "name", "count",
    )
    no_trade_regime_bars = _bars(
        [{"name": key, "count": value} for key, value in no_trade["market_regimes"].items()],
        "name", "count",
    )
    no_trade_symbol_bars = _bars(
        [{"name": key, "count": value} for key, value in no_trade["symbols"].items()],
        "name", "count",
    )
    no_trade_outcome_rows = []
    for horizon, values in no_trade["horizons"].items():
        cells = [
            horizon, values["samples"], _money(values["avg_future_return_pct"]),
            _money(values["avg_mfe_pct"]), _money(values["avg_mae_pct"]),
        ]
        no_trade_outcome_rows.append(
            "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in cells) + "</tr>"
        )
    table_rows = []
    for r in p["strategy_metrics"]:
        vals = [r["mode"], r["strategy"], r["n"], f'{r["wins"]}/{r["losses"]}',
                _money(r["pf"]),
                _money(r["winrate"] * 100 if r["winrate"] is not None else None),
                _money(r["avg_r"]), _money(r["expectancy"]),
                _money(r["gross_profit"]), _money(r["gross_loss"]),
                _money(r["fees"]), _money(r["net_pnl"])]
        table_rows.append("<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in vals) + "</tr>")
    tags = " · ".join(f'{k}: {v}' for k, v in q["environment_classification"].items())
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ASTRA BOT by Gamadril</title>
<style>
:root{{--bg:#0b0e13;--panel:#171f2a;--line:#303c49;--gold:{GOLD};--muted:#a5afbd;--green:{GREEN};--red:{RED};--txt:#f3f5f7}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 50% -15%,#293544,#0b0e13 55%);color:var(--txt);font:15px/1.5 system-ui,sans-serif}}
main{{max-width:1180px;margin:auto;padding:22px}}header{{padding:22px 0;border-bottom:1px solid var(--line);margin-bottom:20px}}
.kicker{{color:var(--gold);letter-spacing:.2em;text-transform:uppercase;font-size:12px}}h1{{font-size:clamp(28px,5vw,46px);margin:6px 0}}h2{{font-size:19px;margin:0 0 12px}}.sub,small{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:16px 0}}.metric,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:15px;min-width:0}}
.metric span,.metric small{{display:block;color:var(--muted);font-size:12px}}.metric b{{display:block;font-size:clamp(20px,3vw,29px);overflow-wrap:anywhere}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:14px 0}}.panel{{margin:14px 0}}svg{{width:100%;height:auto;display:block}}.axis,.bar{{fill:var(--muted);font-size:12px}}.empty{{color:var(--muted);padding:30px;text-align:center;border:1px dashed var(--line);border-radius:10px}}
table{{border-collapse:collapse;width:100%;min-width:730px;font-size:12px}}th,td{{padding:9px;border-bottom:1px solid var(--line);white-space:nowrap;text-align:right}}th{{color:var(--gold)}}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){{text-align:left}}.scroll{{overflow-x:auto}}.tag{{color:var(--muted);font-size:12px}}.note{{color:var(--muted);font-size:12px}}
@media(max-width:850px){{main{{padding:14px}}.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.two{{grid-template-columns:1fr}}}}
@media(max-width:420px){{.metric{{padding:11px}}.metric b{{font-size:20px}}}}
</style></head><body><main>
<header><div class="kicker">ASTRA BOT · by Gamadril</div><h1>Analytics dashboard</h1>
<p class="sub">Только реальные журналы · обновлено {html.escape(p["generated_at_msk"])} MSK</p>
<div class="tag">{html.escape(tags)}</div></header>
<div class="grid">
{_card("PAPER trades",str(q["paper_trades"]),"partial legs coalesced")}
{_card("PAPER net PnL",_money(paper_pnl)+" USDT","закрытые сделки")}
{_card("LIVE UNKNOWN exits",str(q["environment_classification"]["UNKNOWN"]),"не угадывается как VST")}
{_card("LIVE UNKNOWN PnL",_money(unknown_pnl)+" USDT","отдельно от REAL и VST")}
</div>
<div class="two">
<div class="panel"><h2>Cumulative realized PnL · PAPER</h2>{_line(p["timelines"]["paper"],"cumulative_pnl","PAPER cumulative PnL")}<p class="note">Накопленный PnL, не баланс биржевого счёта.</p></div>
<div class="panel"><h2>Drawdown · PAPER</h2>{_line(p["timelines"]["paper"],"drawdown","PAPER drawdown")}<p class="note">Просадка относительно пика накопленного реализованного PnL.</p></div>
</div>
<div class="two">
<div class="panel"><h2>Cumulative realized PnL · LIVE / UNKNOWN</h2>{_line(p["timelines"]["UNKNOWN"],"cumulative_pnl","LIVE UNKNOWN PnL")}<p class="note">Среда не подтверждена строковой меткой.</p></div>
<div class="panel"><h2>Drawdown · LIVE / UNKNOWN</h2>{_line(p["timelines"]["UNKNOWN"],"drawdown","LIVE UNKNOWN drawdown")}<p class="note">Просадка рассчитана по накопленному закрытому PnL, не по балансу счёта.</p></div>
<div class="panel"><h2>Результат по стратегиям</h2>{strategy_bars}</div>
</div>
<div class="two">
<div class="panel"><h2>Торговые сессии</h2>{session_bars}<p class="note">raw_bucket() и часовой пояс MSK из live_session.py.</p></div>
<div class="panel"><h2>Volume-zone Shadow</h2>
<div class="grid" style="grid-template-columns:repeat(2,minmax(0,1fr))">
{_card("zone_shadow", "нет данных" if zone["observations"] is None else str(zone["observations"]),"исторические события")}
{_card("in-zone", "нет данных" if zone["in_zone_observations"] is None else str(zone["in_zone_observations"]),"наблюдения внутри зон")}
{_card("snapshot zones",str(zone["snapshot_zones"]),f'{zone["snapshot_symbols"]} symbols')}
{_card("min distance",_money(zone["min_distance_pct"])+"%" if zone["min_distance_pct"] is not None else "нет данных","по записанным событиям")}
</div><h3>Текущее состояние по символам</h3>{zone_bars}<p class="note">{html.escape(zone["note"])}</p></div>
</div>
<div class="two">
<div class="panel"><h2>Heatmap · symbol × session × side</h2>{detail_bars}<p class="note">Разрез по реально записанным symbol, профилю, типу дня и стороне позиции.</p></div>
<div class="panel"><h2>Отбраковки arm</h2>{skip_bars}<p class="note">Только причины, реально записанные в arm_summary / arm_skip.</p></div>
</div>
<div class="two">
<div class="panel"><h2>Воронка сигналов</h2>
{_card("NO_TRADE observations","нет данных" if no_trade["observations_count"] is None else str(no_trade["observations_count"]),"persisted symbol/bar evaluations — не тики")}
{_card("Candidate records","нет данных" if no_trade["candidate_count"] is None else str(no_trade["candidate_count"]),"наблюдения с candidate object")}
{_card("Forward outcome coverage",_money(no_trade["outcome_coverage_pct"])+"%" if no_trade["outcome_coverage_pct"] is not None else "нет данных",f'{no_trade["observations_with_outcome"] if no_trade["observations_with_outcome"] is not None else "—"} / {no_trade["observations_count"] if no_trade["observations_count"] is not None else "—"} observations")}
{_card("Entry / failed / exits",f'{funnel["entry_events"]} / {funnel["failed_entry_events"]} / {funnel["verified_exits"]}',"события торговых журналов")}</div>
<div class="panel"><h2>Техническое здоровье</h2>
{_card("journal errors","нет данных" if health["journal_errors"] is None else str(health["journal_errors"]),"error events")}
{_card("protect failures","нет данных" if health["protective_order_failures"] is None else str(health["protective_order_failures"]),"protective-order errors")}
{_card("reconciliation","нет данных" if health["reconciliation_events"] is None else str(health["reconciliation_events"]),"qty / position / ghost")}
{_card("BingX 100410",str(health["bingx_100410_occurrences"]),"совпадения в журналах")}</div>
</div>
<div class="two">
<div class="panel"><h2>NO_TRADE · reason codes</h2>{no_trade_reason_bars}<p class="note">Подсчёт reason_code из zeus_no_trade_observations.jsonl; это оценки symbol/bar, а не ордера.</p></div>
<div class="panel"><h2>NO_TRADE · rejection stages</h2>{no_trade_stage_bars}<h3>Market regimes</h3>{no_trade_regime_bars}</div>
</div>
<div class="two">
<div class="panel"><h2>NO_TRADE · symbols</h2>{no_trade_symbol_bars}<p class="note">Число записанных наблюдений по символам.</p></div>
<div class="panel"><h2>Hypothetical rejected-candidate R</h2>
{_card("R samples",str(no_trade["hypothetic_r"]["samples"]),"из no_trade_outcomes.hypothetic_r")}
{_card("Average hypothetical R",_money(no_trade["hypothetic_r"]["avg_r"]),"не реализованный PnL")}
{_card("Positive R rate",_money(no_trade["hypothetic_r"]["positive_rate_pct"])+"%" if no_trade["hypothetic_r"]["positive_rate_pct"] is not None else "нет данных","гипотетические исходы")}</div>
</div>
<div class="panel"><h2>NO_TRADE · forward outcome quality</h2><div class="scroll"><table><thead><tr><th>Horizon (bars)</th><th>Samples</th><th>Avg future return %</th><th>Avg MFE %</th><th>Avg MAE %</th></tr></thead><tbody>{"".join(no_trade_outcome_rows) if no_trade_outcome_rows else "<tr><td colspan='5'>нет данных за период</td></tr>"}</tbody></table></div>
<p class="note">Forward returns, MFE и MAE рассчитаны исходной системой из будущих свечей и прочитаны из outcomes; значения не пересчитываются панелью.</p></div>
<div class="panel"><h2>Статистика стратегий</h2><div class="scroll"><table><thead><tr>
<th>Contour</th><th>Strategy</th><th>Trades</th><th>W/L</th><th>PF</th><th>Win rate</th><th>Avg R</th><th>Expectancy</th><th>Gross profit</th><th>Gross loss</th><th>Fees</th><th>Net PnL</th>
</tr></thead><tbody>{"".join(table_rows) if table_rows else "<tr><td colspan='12'>нет данных за период</td></tr>"}</tbody></table></div>
<p class="note">PF = gross positive PnL / absolute gross negative PnL. Комиссии показаны отдельно.</p></div>
<div class="panel"><h2>Качество источников</h2>
<p>Paper rows: {q["paper_source_rows"]} · Paper trades: {q["paper_trades"]} · Live rows: {q["live_rows"]} · Verified exits: {q["verified_live_exits"]} · Journal rows: {q["journal_rows"]}</p>
<p>NO_TRADE observations: {no_trade["observations_count"] if no_trade["observations_count"] is not None else "нет данных"} · Outcome rows: {no_trade["outcome_rows"] if no_trade["outcome_rows"] is not None else "нет данных"} · Malformed observations/outcomes: {q["malformed_jsonl"]["no_trade_observations"]} / {q["malformed_jsonl"]["no_trade_outcomes"]}</p>
<p class="note">Источники: {html.escape(", ".join(p["sources"]))}. Пропуски остаются «нет данных», не заменяются выдуманными нулями.</p></div>
<footer class="note">Read-only ASTRA BOT analytics · no trading, risk, keys or live-loop modifications.</footer>
</main></body></html>"""


def generate(root: Path) -> Path:
    root = root.resolve()
    reports = root / "reports"
    destination = reports / "astra_dashboard"
    reports.mkdir(parents=True, exist_ok=True)
    payload = build_payload(root)
    temp = Path(tempfile.mkdtemp(prefix=".astra_dashboard.", dir=reports))
    backup = reports / ".astra_dashboard.previous"
    try:
        (temp / "dashboard.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (temp / "index.html").write_text(render_html(payload), encoding="utf-8")
        if backup.exists():
            shutil.rmtree(backup)
        if destination.exists():
            os.replace(destination, backup)
        try:
            os.replace(temp, destination)
        except Exception:
            if backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return destination
    finally:
        if temp.exists():
            shutil.rmtree(temp, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(f"ASTRA dashboard written: {generate(args.root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
