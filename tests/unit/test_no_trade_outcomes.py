"""Append-once outcome persistence, transitional readers, and bounded retention."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from astra_bot.core import models
from astra_bot.ml.no_trade_observations import NoTradeObservation, NoTradeObservationLog
from astra_bot.ml.no_trade_outcomes import load_outcomes

STEP = 300_000


def _base_ms():
    return (int(time.time() * 1000) // STEP - 50) * STEP


def _outcome(bar_time=None, **extra):
    return {
        "bar_time": _base_ms() if bar_time is None else bar_time,
        "symbol": "BTC-USDT",
        "reason_code": "LOW_EV",
        "market_regime": "RANGE",
        "horizons": {"1": {"future_return": 0.001, "max_up": 0.002, "max_down": -0.001}},
        "computed_at": datetime.now(UTC).isoformat(),
        **extra,
    }


def _obs(oid, bar_time, **extra):
    return NoTradeObservation(
        id=oid, symbol="BTC-USDT", bar_time=bar_time, timestamp=bar_time,
        market_regime="RANGE", regime_confidence=0.5, reason_code="LOW_EV", **extra,
    )


def _candles(base):
    return [
        models.Candle(
            exchange="feed", symbol="BTC-USDT", timeframe="5m", open_time=base + i * STEP,
            open=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100"),
            volume=Decimal("10"), quote_volume=Decimal("1000"),
        )
        for i in range(31)
    ]


def _log(tmp_path, configured_suffix=".jsonl"):
    return NoTradeObservationLog(
        observations_path=tmp_path / "observations.jsonl",
        outcomes_path=tmp_path / f"outcomes{configured_suffix}",
    )


@pytest.mark.parametrize("wrapper", [True, False])
@pytest.mark.parametrize("suffix", [".json", ".jsonl"])
def test_loader_falls_back_to_legacy_only_when_jsonl_is_absent(tmp_path, wrapper, suffix):
    rows = {"old": _outcome()}
    data = {"updated": "2026-10-11", "outcomes": rows} if wrapper else rows
    legacy = tmp_path / "outcomes.json"
    legacy.write_text(json.dumps(data), encoding="utf-8")
    before = legacy.read_bytes()
    assert load_outcomes(tmp_path / f"outcomes{suffix}") == rows
    assert legacy.read_bytes() == before
    assert not (tmp_path / "outcomes.jsonl").exists()


def test_jsonl_reader_last_wins_and_skips_broken_rows(tmp_path):
    path = tmp_path / "outcomes.jsonl"
    first = _outcome(reason_code="FIRST")
    last = _outcome(reason_code="LAST", hypothetic_r={"r": -1.0})
    path.write_text(
        json.dumps({"id": "same", **first}) + "\n\n{broken\nnull\n[]\n"
        + json.dumps({"bar_time": first["bar_time"]}) + "\n"
        + json.dumps({"id": "", **first}) + "\n"
        + json.dumps({"id": "same", **last}) + "\n", encoding="utf-8",
    )
    (tmp_path / "outcomes.json").write_text(json.dumps({"outcomes": {"legacy": first}}))
    before = path.read_bytes()
    assert load_outcomes(path) == {"same": last}
    assert load_outcomes(path.with_suffix(".json")) == {"same": last}
    assert path.read_bytes() == before


@pytest.mark.parametrize("content", ["", "broken\nnull\n"])
def test_present_jsonl_never_falls_back_to_stale_legacy(tmp_path, content):
    (tmp_path / "outcomes.json").write_text(json.dumps({"outcomes": {"old": _outcome()}}))
    path = tmp_path / "outcomes.jsonl"
    path.write_text(content)
    assert load_outcomes(path) == {}


@pytest.mark.parametrize("legacy", [None, "broken", "[]", '{"outcomes": null}', '{"outcomes": {"x": 3}}'])
def test_missing_or_invalid_legacy_is_an_empty_runtime_index(tmp_path, legacy):
    if legacy is not None:
        (tmp_path / "outcomes.json").write_text(legacy)
    assert load_outcomes(tmp_path / "outcomes.jsonl") == {}


@pytest.mark.parametrize("suffix", [".json", ".jsonl"])
def test_enrichment_only_appends_new_rows_and_restart_is_idempotent(tmp_path, suffix):
    base = _base_ms()
    log = _log(tmp_path, suffix)
    path = tmp_path / "outcomes.jsonl"
    candles = {"BTC-USDT": _candles(base)}
    log.add(_obs("first", base))
    assert [o.id for o in log.enrich(candles)] == ["first"]
    first_bytes = path.read_bytes()
    inode = path.stat().st_ino
    assert log.enrich(candles) == []
    log._save_outcomes()
    assert path.read_bytes() == first_bytes

    log.add(_obs("second", base + STEP))
    assert [o.id for o in log.enrich(candles)] == ["second"]
    assert path.read_bytes().startswith(first_bytes)
    assert path.stat().st_ino == inode, "writer must append, not atomically replace the whole index"
    assert [json.loads(line)["id"] for line in path.read_text().splitlines()] == ["first", "second"]
    assert not path.with_suffix(".jsonl.tmp").exists()
    assert not path.with_suffix(".json").exists()

    restarted = _log(tmp_path, suffix)
    before_restart_tick = path.read_bytes()
    assert restarted.pending() == []
    assert restarted.enrich(candles) == []
    assert path.read_bytes() == before_restart_tick
    assert set(restarted._outcomes) == {"first", "second"}


def test_legacy_bootstrap_survives_first_append_and_restart_without_rewriting_json(tmp_path):
    base = _base_ms()
    legacy = tmp_path / "outcomes.json"
    legacy.write_text(json.dumps({"updated": "unchanged", "outcomes": {"old": _outcome(base)}}))
    before = legacy.read_bytes()
    log = _log(tmp_path, ".json")
    log.add(_obs("old", base))
    log.add(_obs("new", base + STEP))
    assert [o.id for o in log.enrich({"BTC-USDT": _candles(base)})] == ["new"]
    assert legacy.read_bytes() == before
    assert set(load_outcomes(tmp_path / "outcomes.jsonl")) == {"old", "new"}
    assert _log(tmp_path).pending() == []


@pytest.mark.parametrize("tail", ['{"id": "existing", "bar_time": 1}', '{"id": "interrupted"'])
def test_append_keeps_unterminated_tail_separate(tmp_path, tail):
    path = tmp_path / "outcomes.jsonl"
    path.write_text(tail)
    log = _log(tmp_path)
    log._unsaved_outcomes["new"] = _outcome()
    log._save_outcomes()
    assert path.read_bytes().startswith(tail.encode() + b"\n")
    assert load_outcomes(path)["new"]["symbol"] == "BTC-USDT"


def test_time_prune_bounds_memory_even_on_idle_tick_without_rewriting_jsonl(tmp_path):
    now = int(time.time() * 1000)
    path = tmp_path / "outcomes.jsonl"
    expired = _outcome(now - 31 * 86_400_000)
    fresh = _outcome(now - STEP)
    path.write_text(
        json.dumps({"id": "expired", **expired}) + "\n"
        + json.dumps({"id": "fresh", **fresh}) + "\n", encoding="utf-8",
    )
    before = path.read_bytes()
    log = _log(tmp_path)
    assert log._outcomes == {"fresh": fresh}
    log._outcomes["expired"] = expired
    assert log.enrich({}) == []
    assert set(log._outcomes) == {"fresh"}
    assert path.read_bytes() == before


def test_pending_does_not_reenrich_resolved_evicted_or_expired_observations(tmp_path):
    base = _base_ms()
    log = _log(tmp_path)
    log.add(_obs("resolved", base, result={"1": {"future_return": 0.1}}))
    log.add(_obs("expired", base - 31 * 86_400_000))
    log.add(_obs("pending", base + STEP))
    assert [o.id for o in log.pending()] == ["pending"]
    assert log.enrich({"BTC-USDT": _candles(base)})[0].id == "pending"
    assert set(load_outcomes(tmp_path / "outcomes.jsonl")) == {"pending"}


@pytest.mark.parametrize("jsonl", [True, False])
def test_edge_panel_reads_both_formats(tmp_path, monkeypatch, jsonl):
    from scripts import edge_scan

    base = _base_ms()
    rows = {f"obs-{i}": _outcome(base + i * STEP) for i in range(8)}
    path = tmp_path / "outcomes.jsonl"
    if jsonl:
        path.write_text("".join(json.dumps({"id": oid, **row}) + "\n" for oid, row in rows.items()))
    else:
        path.with_suffix(".json").write_text(json.dumps({"updated": "legacy", "outcomes": rows}))
    monkeypatch.setattr(edge_scan, "SRC", path)
    panel = edge_scan.Panel()
    assert panel.n == 8
    assert panel.syms == ["BTC-USDT"]
    assert panel.r["BTC-USDT"] == [0.001] * 8


@pytest.mark.parametrize("jsonl", [True, False])
def test_lever_scan_cli_reads_jsonl_and_legacy_fallback(tmp_path, monkeypatch, jsonl):
    from scripts import lever_scan

    path = tmp_path / "outcomes.jsonl"
    row = _outcome()
    if jsonl:
        path.write_text(json.dumps({"id": "one", **row}) + "\n")
    else:
        path.with_suffix(".json").write_text(json.dumps({"updated": "legacy", "outcomes": {"one": row}}))
    trades = tmp_path / "trades.jsonl"
    trades.write_text('{"closed_at": 1, "r_multiple": 1.0}\n')
    calls = []
    monkeypatch.setattr(lever_scan, "excursions", lambda *args: None)
    monkeypatch.setattr(lever_scan, "microstructure", lambda paths, rows, cost: calls.extend(rows))
    monkeypatch.setattr(lever_scan, "geometry", lambda *args: None)
    monkeypatch.setattr(lever_scan, "structural", lambda *args: None)
    monkeypatch.setattr(lever_scan, "learned", lambda *args: None)
    monkeypatch.setattr(lever_scan, "Trade", lambda row: SimpleNamespace(r=1.0, pnl=1.0))
    monkeypatch.setattr(lever_scan.sys, "argv", ["lever_scan", "--outcomes", str(path), "--trades", str(trades)])
    assert lever_scan.main() == 0
    assert calls == [row]


def test_failed_append_is_retried_on_idle_tick_without_losing_outcomes(tmp_path, monkeypatch):
    base = _base_ms()
    log = _log(tmp_path)
    log.add(_obs("retry", base))
    real_open = Path.open

    def fail_append(path, mode="r", *args, **kwargs):
        if path == log.outcomes_path and mode == "a":
            raise OSError("temporary write failure")
        return real_open(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", fail_append)
        with pytest.raises(OSError, match="temporary write failure"):
            log.enrich({"BTC-USDT": _candles(base)})
    assert set(log._unsaved_outcomes) == {"retry"}
    assert log.enrich({}) == []
    assert set(load_outcomes(log.outcomes_path)) == {"retry"}
    assert log._unsaved_outcomes == {}
    assert json.loads(log.observations_path.read_text())["result"] is not None
