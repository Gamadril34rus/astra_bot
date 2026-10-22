"""F5: robust save order + v2 warn dedup in yml."""
from __future__ import annotations

from pathlib import Path


def test_save_list_has_v2_warn_and_stage_before_reset():
    yml = Path(".github/workflows/zeus-live-clock.yml").read_text(encoding="utf-8")
    assert "models/zeus_live_v2_warn.json" in yml
    save = yml.split("Save live commands state", 1)[1].split("- name:", 1)[0]
    # first occurrence of stage_save before first reset --hard
    stage_pos = save.find("stage_save")
    reset_pos = save.find("git reset --hard")
    assert 0 <= stage_pos < reset_pos
    assert "3600" in save
    assert "save attempt" in save


def test_bash_syntax_of_save_script():
    import re
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
    script = "#!/bin/bash\n" + "\n".join(lines) + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
        f.write(script)
        path = f.name
    r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
