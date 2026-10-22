"""F6: monotonic TG offset + equity snapshot + concurrency."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.monotonic_tg_offset import max_with_origin
from scripts.zeus_live_clock import write_live_equity


def test_monotonic_offset_takes_max(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    models = tmp_path / "models"
    models.mkdir()
    off = models / "zeus_live_tg_offset.json"
    off.write_text(json.dumps({"offset": 100}))
    # no origin — stays local
    # simulate remote by writing higher then calling max on two locals
    off.write_text(json.dumps({"offset": 100}))
    # function reads local and tries git show — without git remote keeps local
    # unit: max(100,200)=200 via direct write of "remote" path logic
    from scripts import monotonic_tg_offset as m

    monkeypatch.setattr(
        m.subprocess,
        "check_output",
        lambda *a, **k: json.dumps({"offset": 200}).encode()
        if "show" in str(a)
        else b"master\n",
    )
    final = m.max_with_origin("origin/master")
    assert final == 200
    assert json.loads(off.read_text())["offset"] == 200


def test_monotonic_keeps_higher_local(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    off = tmp_path / "models" / "zeus_live_tg_offset.json"
    off.write_text(json.dumps({"offset": 300}))
    from scripts import monotonic_tg_offset as m

    monkeypatch.setattr(
        m.subprocess,
        "check_output",
        lambda *a, **k: json.dumps({"offset": 200}).encode()
        if "show" in str(a)
        else b"master\n",
    )
    assert m.max_with_origin() == 300


def test_write_live_equity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_live_equity(12345.67)
    p = Path("models/zeus_live_equity.json")
    assert p.exists()
    raw = json.loads(p.read_text())
    assert raw["equity"] == 12345.67
    assert isinstance(raw["ts"], float)


def test_concurrency_block_in_yml():
    yml = Path(".github/workflows/zeus-live-clock.yml").read_text(encoding="utf-8")
    assert "concurrency:" in yml
    assert "cancel-in-progress: false" in yml
    assert "zeus_live_equity.json" in yml
    assert "monotonic_tg_offset.py" in yml


def test_save_bash_n():
    import subprocess
    import tempfile

    yml = Path(".github/workflows/zeus-live-clock.yml").read_text(encoding="utf-8")
    save = yml.split("Save live commands state", 1)[1]
    run = save.split("run: |", 1)[1]
    lines = []
    for ln in run.splitlines():
        if ln.startswith("          "):
            lines.append(ln[10:])
        elif not ln.strip():
            lines.append("")
        elif ln.startswith("      -") or ln.startswith("    -"):
            break
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
        f.write("#!/bin/bash\n" + "\n".join(lines) + "\n")
        path = f.name
    r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
