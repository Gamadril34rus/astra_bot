"""LiveBroker — body from _live_br_p*.txt."""
from __future__ import annotations
from pathlib import Path
_DIR = Path(__file__).resolve().parent
_parts = sorted(_DIR.glob("_live_br_p*.txt"))
if not _parts:
    raise RuntimeError("live_broker: missing _live_br_p*.txt")
exec(compile("".join(p.read_text(encoding="utf-8") for p in _parts), str(Path(__file__).resolve()), "exec"), globals())
