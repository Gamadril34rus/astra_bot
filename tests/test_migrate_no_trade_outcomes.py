"""Safe, deterministic migration of Zeus and the frozen twin's legacy JSON."""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest
from astra_bot.ml.no_trade_outcomes import load_outcomes
from scripts import migrate_no_trade_outcomes as migration

NAMES = ("zeus_no_trade_outcomes", "no_trade_outcomes")
STAMP = "20261011T080151Z"


def _source(tmp_path, name="zeus_no_trade_outcomes", rows=None):
    path = tmp_path / "models" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if rows is None:
        # Deliberately not in chronological order; tie-break by computed_at.
        rows = {
            "latest": {"bar_time": 1791705000000, "computed_at": "2026-10-11T07:58:00Z", "extra": "тест"},
            "same-bar-late": {"bar_time": 1791405000000, "computed_at": "2026-10-08T07:58:00Z"},
            "earliest": {"bar_time": 1791005000000, "computed_at": "2026-10-01T07:58:00Z"},
            "same-bar-early": {"bar_time": 1791405000000, "computed_at": "2026-10-08T07:57:00Z"},
        }
    path.write_text(json.dumps({"updated": "legacy", "outcomes": rows}, ensure_ascii=False, indent=1) + "\n")
    return path


@pytest.mark.parametrize("name", NAMES)
def test_dry_run_has_no_filesystem_side_effects(tmp_path, name):
    source = _source(tmp_path, name)
    before = source.read_bytes()
    result = migration.migrate_file(source, dry_run=True, stamp=STAMP)
    assert result["skipped"] == "dry_run"
    assert result["total_rows"] == result["kept_rows"] == 4
    assert source.read_bytes() == before
    assert not source.with_suffix(".jsonl").exists()
    assert not (source.parent / "archive").exists()
    assert list(source.parent.iterdir()) == [source]


@pytest.mark.parametrize("name", NAMES)
def test_migration_sorts_snapshots_exact_bytes_and_removes_legacy(tmp_path, name):
    source = _source(tmp_path, name)
    before = source.read_bytes()
    original = json.loads(before)["outcomes"]
    result = migration.migrate_file(source, stamp=STAMP)
    assert result["migrated"] is True
    assert not source.exists()
    archive = source.parent / "archive" / f"{name}_{STAMP}.json.gz"
    assert gzip.decompress(archive.read_bytes()) == before
    path = source.with_suffix(".jsonl")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["id"] for row in rows] == ["earliest", "same-bar-early", "same-bar-late", "latest"]
    assert load_outcomes(path) == original
    assert result["limit"] == 24_000


@pytest.mark.parametrize("name", NAMES)
def test_live_tail_uses_real_24000_limit_and_full_snapshot_preserves_evicted_rows(tmp_path, name):
    rows = {
        f"id-{i:05d}": {"bar_time": 1791705000000 + i, "computed_at": "2026-10-11T07:58:00Z"}
        for i in reversed(range(24_002))
    }
    source = _source(tmp_path, name, rows)
    before = source.read_bytes()
    result = migration.migrate_file(source, stamp=STAMP)
    assert result["kept_rows"] == 24_000
    assert result["archived_only_rows"] == 2
    live = list(load_outcomes(source.with_suffix(".jsonl")))
    assert len(live) == 24_000
    assert live[0] == "id-00002"
    assert live[-1] == "id-24001"
    assert gzip.decompress(Path(result["archive"]).read_bytes()) == before


@pytest.mark.parametrize("name", NAMES)
def test_rerun_is_noop_even_after_new_jsonl_appends(tmp_path, name):
    source = _source(tmp_path, name)
    migration.migrate_file(source, stamp=STAMP)
    path = source.with_suffix(".jsonl")
    with path.open("a") as stream:
        stream.write('{"id": "new", "bar_time": 1791706000000}\n')
    before = path.read_bytes()
    archives = {p.name: p.read_bytes() for p in (source.parent / "archive").iterdir()}
    result = migration.migrate_file(source, stamp="20261011T090000Z")
    assert result == {
        "source": str(source), "destination": str(path), "migrated": False, "skipped": "already_migrated",
    }
    assert path.read_bytes() == before
    assert {p.name: p.read_bytes() for p in (source.parent / "archive").iterdir()} == archives


def test_identical_tail_from_interrupted_migration_can_be_resumed(tmp_path):
    source = _source(tmp_path)
    before = source.read_bytes()
    rows = json.loads(before)["outcomes"]
    path = source.with_suffix(".jsonl")
    path.write_text("".join(
        json.dumps({"id": oid, **row}, ensure_ascii=False, separators=(",", ":")) + "\n"
        for oid, row in sorted(rows.items(), key=migration.outcome_sort_key)
    ))
    inode = path.stat().st_ino
    assert migration.migrate_file(source, stamp=STAMP)["migrated"] is True
    assert path.stat().st_ino == inode
    assert gzip.decompress((source.parent / "archive" / f"{source.stem}_{STAMP}.json.gz").read_bytes()) == before
    assert not source.exists()


def test_different_existing_jsonl_is_never_overwritten(tmp_path):
    source = _source(tmp_path)
    before = source.read_bytes()
    path = source.with_suffix(".jsonl")
    path.write_text('{"id": "live-append", "bar_time": 1791706000000}\n')
    live = path.read_bytes()
    with pytest.raises(ValueError, match="refusing to overwrite"):
        migration.migrate_file(source, stamp=STAMP)
    assert source.read_bytes() == before
    assert path.read_bytes() == live
    assert not (source.parent / "archive").exists()


@pytest.mark.parametrize("raw", ["broken", "[]", '{"outcomes": []}', '{"outcomes": {"id": 3}}'])
def test_invalid_json_is_not_removed_or_silently_migrated(tmp_path, raw):
    source = _source(tmp_path)
    source.write_text(raw)
    with pytest.raises(ValueError):
        migration.migrate_file(source, stamp=STAMP)
    assert source.read_text() == raw
    assert not source.with_suffix(".jsonl").exists()
    assert not (source.parent / "archive").exists()


def test_snapshot_collision_preserves_both_existing_archive_and_source(tmp_path):
    source = _source(tmp_path)
    before = source.read_bytes()
    archive = source.parent / "archive" / f"{source.stem}_{STAMP}.json.gz"
    archive.parent.mkdir()
    archive.write_bytes(gzip.compress(b"another snapshot"))
    snapshot = archive.read_bytes()
    with pytest.raises(ValueError, match="snapshot collision"):
        migration.migrate_file(source, stamp=STAMP)
    assert source.read_bytes() == before
    assert archive.read_bytes() == snapshot
    assert not source.with_suffix(".jsonl").exists()


def test_snapshot_write_failure_does_not_publish_tail_or_remove_legacy(tmp_path):
    source = _source(tmp_path)
    before = source.read_bytes()
    (source.parent / "archive").write_text("not a directory")
    with pytest.raises(OSError):
        migration.migrate_file(source, stamp=STAMP)
    assert source.read_bytes() == before
    assert not source.with_suffix(".jsonl").exists()


def test_missing_source_is_a_noop(tmp_path):
    source = tmp_path / "models" / "zeus_no_trade_outcomes.json"
    assert migration.migrate_file(source)["skipped"] == "missing"
    assert not source.parent.exists()


def test_cli_dry_run_and_repeat_migrate_both_contours(tmp_path):
    originals = {name: _source(tmp_path, name).read_bytes() for name in NAMES}
    script = Path(migration.__file__)
    command = [sys.executable, str(script), "--root", str(tmp_path)]
    dry = subprocess.run([*command, "--dry-run"], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert dry.returncode == 0, dry.stderr
    assert len(dry.stdout.splitlines()) == 2
    assert not (tmp_path / "models" / "archive").exists()
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert all(json.loads(line)["migrated"] for line in result.stdout.splitlines())
    for name, raw in originals.items():
        assert not (tmp_path / "models" / f"{name}.json").exists()
        archives = list((tmp_path / "models" / "archive").glob(f"{name}_*.json.gz"))
        assert len(archives) == 1
        assert gzip.decompress(archives[0].read_bytes()) == raw
    repeat = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert repeat.returncode == 0, repeat.stderr
    assert all(json.loads(line)["skipped"] == "already_migrated" for line in repeat.stdout.splitlines())


def test_failed_tail_publish_preserves_snapshot_and_source_and_can_resume(tmp_path, monkeypatch):
    source = _source(tmp_path)
    before = source.read_bytes()
    real_replace = Path.replace

    def fail_publish(path, target):
        if target == source.with_suffix(".jsonl"):
            raise OSError("tail publication failed")
        return real_replace(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_publish)
        with pytest.raises(OSError, match="tail publication failed"):
            migration.migrate_file(source, stamp=STAMP)
    archive = source.parent / "archive" / f"{source.stem}_{STAMP}.json.gz"
    assert gzip.decompress(archive.read_bytes()) == before
    assert source.read_bytes() == before
    assert not source.with_suffix(".jsonl").exists()
    assert migration.migrate_file(source, stamp=STAMP)["migrated"] is True
    assert len(list(archive.parent.iterdir())) == 1
    assert not source.with_suffix(".jsonl.tmp").exists()
