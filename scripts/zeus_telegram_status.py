#!/usr/bin/env python3
"""Zeus Telegram status — body loaded from _tg_body_*.txt (MCP size limit)."""
from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parent
_parts = sorted(_DIR.glob("_tg_body_*.txt"))
if not _parts:
    raise RuntimeError("zeus_telegram_status: missing _tg_body_*.txt")
exec(compile("".join(p.read_text(encoding="utf-8") for p in _parts), str(Path(__file__).resolve()), "exec"), globals())
