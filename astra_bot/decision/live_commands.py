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
VALID = {"stop", "flat", "resume", "reset_kill"}

BLOCKED_PATH = Path("models/zeus_live_blocked.json")
CMD_TTL_MS = 60 * 60 * 1000  # ≤1h
DEDUP_WINDOW_MS = 15 * 60 * 1000  # owner №26: same cmd within 15m → skip


def is_blocked(path: Path = BLOCKED_PATH) -> bool:
    if not path.exists():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return bool(raw.get("blocked"))
    except Exception:
        return True  # unreadable → treat as blocked (safe)


def set_blocked(blocked: bool, path: Path = BLOCKED_PATH, reason: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"blocked": bool(blocked), "reason": reason, "ts": int(time.time() * 1000)}, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)




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


def enqueue(cmd: str, path: Path = DEFAULT_PATH, source: str = "tg") -> bool:
    """Enqueue cmd. Returns False if deduped (pending or done within 15m)."""
    c = str(cmd).strip().lower().lstrip("/")
    if c not in VALID:
        raise ValueError(f"unknown command: {cmd}")
    now = int(time.time() * 1000)
    cmds = load_commands(path)
    for existing in cmds:
        if existing.get("cmd") != c:
            continue
        st = existing.get("status")
        ts = int(existing.get("ts") or 0)
        if st == "pending":
            logger.info("enqueue dedup: %s already pending ts=%s", c, ts)
            return False
        if st == "done" and now - ts < DEDUP_WINDOW_MS:
            logger.info(
                "enqueue dedup: %s done within %dms window ts=%s",
                c,
                DEDUP_WINDOW_MS,
                ts,
            )
            return False
    cmds.append(
        {
            "cmd": c,
            "ts": now,
            "source": source,
            "status": "pending",
        }
    )
    save_commands(cmds, path)
    return True


def pending(path: Path = DEFAULT_PATH) -> list[dict[str, Any]]:
    now = int(time.time() * 1000)
    out = []
    for c in load_commands(path):
        if c.get("status") != "pending":
            continue
        ts = int(c.get("ts") or 0)
        if ts and now - ts > CMD_TTL_MS:
            c["status"] = "expired"
            continue
        out.append(c)
    return out


def mark_done(cmd_row: dict[str, Any], path: Path = DEFAULT_PATH, result: str = "ok") -> None:
    cmds = load_commands(path)
    ts = cmd_row.get("ts")
    for c in cmds:
        if c.get("ts") == ts and c.get("cmd") == cmd_row.get("cmd"):
            c["status"] = "done"
            c["result"] = result
            c["done_ts"] = int(time.time() * 1000)
    save_commands(cmds, path)


async def apply_pending(
    broker: Any,
    path: Path = DEFAULT_PATH,
    send_tg: Any = None,
) -> list[str]:
    """Execute pending stop/flat/resume on broker. Returns log lines.

    After each cmd: optional TG ack (owner №23) — no silent execution.
    """
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
                set_blocked(True, reason="stop")
                lines.append("cmd stop: entries blocked (persisted)")
            elif cmd == "resume":
                broker.allow_entries()
                set_blocked(False, reason="resume")
                # resume clears operator block, not kill-state halt
                if not getattr(broker, "_halt_reason", "").startswith("PF=") and "kill" not in str(getattr(broker, "_halt_reason", "")).lower():
                    if getattr(broker, "_halt_reason", "") not in ("risk_check_failed",) and "day_loss" not in str(getattr(broker, "_halt_reason", "")) and "week_loss" not in str(getattr(broker, "_halt_reason", "")):
                        pass
                lines.append("cmd resume: entries allowed (block cleared)")
            elif cmd == "flat":
                broker.block_entries()
                set_blocked(True, reason="flat")
                res = await broker.flat_all()
                lines.append(f"cmd flat: {res}")
            elif cmd == "reset_kill":
                from astra_bot.decision.live_risk import KILL_STATE_PATH
                # Idempotent: no kill file → already clear, no TG spam (owner №26)
                if not KILL_STATE_PATH.exists():
                    mark_done(row, path, result="already_clear")
                    lines.append("cmd reset_kill: already_clear (no kill file)")
                    continue
                try:
                    KILL_STATE_PATH.unlink()
                except Exception:
                    pass
                broker.clear_halt()
                lines.append("cmd reset_kill: kill state cleared")
            mark_done(row, path, result="ok")
            lines.append(f"cmd {cmd}: ok")
            if send_tg is not None:
                try:
                    await send_tg(f"✅ {cmd}: исполнен live-тиком")
                except Exception as tg_exc:
                    logger.warning("apply_pending TG ok: %s", type(tg_exc).__name__)
        except Exception as exc:
            mark_done(row, path, result=f"err:{type(exc).__name__}")
            lines.append(f"cmd {cmd} failed: {type(exc).__name__}")
            logger.exception("live command failed")
            if send_tg is not None:
                try:
                    await send_tg(f"⚠️ {cmd}: не вышел — {type(exc).__name__}: {exc}")
                except Exception as tg_exc:
                    logger.warning("apply_pending TG err: %s", type(tg_exc).__name__)
    return lines
