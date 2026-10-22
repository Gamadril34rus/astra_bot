#!/usr/bin/env python3
"""F6.2: models/zeus_live_tg_offset.json = max(local, remote/theirs)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

P = Path("models/zeus_live_tg_offset.json")


def _read(path: Path | None = None) -> int:
    path = path or P
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("offset") or 0)
    except Exception:
        return 0


def _write(val: int) -> None:
    P.parent.mkdir(parents=True, exist_ok=True)
    P.write_text(json.dumps({"offset": int(val)}), encoding="utf-8")


def max_with_origin(ref: str = "origin/master") -> int:
    local = _read()
    remote = 0
    for cand in (ref, "origin/master", "origin/main"):
        try:
            raw = subprocess.check_output(
                ["git", "show", f"{cand}:models/zeus_live_tg_offset.json"],
                stderr=subprocess.DEVNULL,
            )
            remote = int(json.loads(raw.decode()).get("offset") or 0)
            break
        except Exception:
            continue
    final = max(local, remote)
    _write(final)
    print(f"F6.2 offset monotonic local={local} remote={remote} -> {final}")
    return final


def max_ours_theirs() -> int:
    """During rebase conflict: max of working-tree and --theirs."""
    ours = _read()
    theirs = 0
    try:
        subprocess.check_call(
            ["git", "show", ":3:models/zeus_live_tg_offset.json"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        raw = subprocess.check_output(
            ["git", "show", ":3:models/zeus_live_tg_offset.json"],
            stderr=subprocess.DEVNULL,
        )
        theirs = int(json.loads(raw.decode()).get("offset") or 0)
    except Exception:
        try:
            subprocess.call(["git", "checkout", "--theirs", "--", str(P)], stderr=subprocess.DEVNULL)
            theirs = _read()
        except Exception:
            pass
    final = max(ours, theirs)
    _write(final)
    print(f"F6.2 conflict offset max ours={ours} theirs={theirs} -> {final}")
    return final


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "origin"
    if mode == "conflict":
        max_ours_theirs()
    else:
        ref = sys.argv[2] if len(sys.argv) > 2 else "origin/master"
        max_with_origin(ref)
