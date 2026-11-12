from __future__ import annotations
import subprocess
from pathlib import Path
from scripts.ci_state_lock import acquire, release

def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=path, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()

def _repo(tmp_path: Path) -> Path:
    bare, work = tmp_path / "remote.git", tmp_path / "work"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(bare), str(work)], check=True, capture_output=True)
    _git(work, "config", "user.name", "test"); _git(work, "config", "user.email", "test@example.com")
    (work / "seed.txt").write_text("seed\\n", encoding="utf-8")
    _git(work, "add", "seed.txt"); _git(work, "commit", "-m", "seed")
    _git(work, "branch", "-M", "master"); _git(work, "push", "-u", "origin", "master")
    return work

def _args(wait: int = 0, stale: int = 2700):
    import argparse
    return argparse.Namespace(remote="origin", wait_seconds=wait, stale_after_seconds=stale, poll_seconds=0.01)

def test_lock_is_exclusive_and_owner_can_release(tmp_path: Path, monkeypatch):
    work = _repo(tmp_path); monkeypatch.chdir(work)
    monkeypatch.setenv("GITHUB_RUN_ID", "101"); monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    assert acquire(_args()) == 0
    monkeypatch.setenv("GITHUB_RUN_ID", "202")
    assert acquire(_args()) == 1
    assert release(_args()) == 0
    monkeypatch.setenv("GITHUB_RUN_ID", "101"); assert release(_args()) == 0
    monkeypatch.setenv("GITHUB_RUN_ID", "202")
    assert acquire(_args()) == 0; assert release(_args()) == 0

def test_stale_lock_can_be_reclaimed(tmp_path: Path, monkeypatch):
    work = _repo(tmp_path); monkeypatch.chdir(work)
    monkeypatch.setenv("GITHUB_RUN_ID", "old-run"); monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    assert acquire(_args()) == 0
    monkeypatch.setenv("GITHUB_RUN_ID", "new-run")
    assert acquire(_args(stale=0)) == 0; assert release(_args()) == 0
