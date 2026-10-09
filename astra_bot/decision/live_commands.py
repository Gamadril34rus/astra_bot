"""TG stop buttons via models/zeus_live_commands.json.

TG writes a command; live-clock reads, executes, marks done.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path("models/zeus_live_commands.json")
VALID = {"stop", "flat", "resume"}


def load_commands(path: Path = DEFAULT_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict) and "commands" in raw:
        return [x for x in raw["commands"] if isinstance(x, dict)]
    return []


def save_commands(cmds: list[dict[str, Any]], path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"commands": cmds}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def enqueue(cmd: str, path: Path = DEFAULT_PATH, source: str = "tg") -> None:
    c = str(cmd).strip().lower().lstrip("/")
    if c not in VALID:
        raise ValueError(f"unknown command: {cmd}")
    cmds = load_commands(path)
    cmds.append(
        {
            "cmd": c,
            "ts": int(time.time() * 1000),
            "source": source,
            "status": "pending",
        }
    )
    save_commands(cmds, path)


def pending(path: Path = DEFAULT_PATH) -> list[dict[str, Any]]:
    return [c for c in load_commands(path) if c.get("status") == "pending"]


def mark_done(cmd_row: dict[str, Any], path: Path = DEFAULT_PATH, result: str = "ok") -> None:
    cmds = load_commands(path)
    ts = cmd_row.get("ts")
    for c in cmds:
        if c.get("ts") == ts and c.get("cmd") == cmd_row.get("cmd"):
            c["status"] = "done"
            c["result"] = result
            c["done_ts"] = int(time.time() * 1000)
    save_commands(cmds, path)


async def apply_pending(broker: Any, path: Path = DEFAULT_PATH) -> list[str]:
    """Execute pending stop/flat/resume on broker. Returns log lines."""
    lines: list[str] = []
    rows = pending(path)
    # Ревью арбитра: если в батче есть и stop, и resume — исполняем
    # ТОЛЬКО более поздний по ts, второй помечаем done/"superseded".
    # Иначе два клика подряд в одном батче гасят друг друга.
    stop_resume = {r.get("cmd"): r for r in rows if r.get("cmd") in ("stop", "resume")}
    if "stop" in stop_resume and "resume" in stop_resume:
        loser = min(
            (stop_resume["stop"], stop_resume["resume"]),
            key=lambda r: int(r.get("ts") or 0),
        )
        winner = stop_resume["resume"] if loser.get("cmd") == "stop" else stop_resume["stop"]
        mark_done(loser, path, result="superseded")
        lines.append(f"cmd {loser.get('cmd')}: superseded by {winner.get('cmd')}")
        rows = [r for r in rows if r is not loser]
    for row in rows:
        cmd = row.get("cmd")
        try:
            if cmd == "stop":
                broker.block_entries()
                lines.append("cmd stop: entries blocked")
            elif cmd == "resume":
                broker.allow_entries()
                broker.clear_halt()
                lines.append("cmd resume: entries allowed")
            elif cmd == "flat":
                broker.block_entries()
                res = await broker.flat_all()
                lines.append(f"cmd flat: {res}")
            mark_done(row, path, result="ok")
        except Exception as exc:
            mark_done(row, path, result=f"err:{type(exc).__name__}")
            lines.append(f"cmd {cmd} failed: {type(exc).__name__}")
            logger.exception("live command failed")
    return lines
