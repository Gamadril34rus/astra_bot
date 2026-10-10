"""TG stop/flat/resume → models/zeus_live_commands.json (enqueue).

Polls getUpdates (TELEGRAM_BOT_TOKEN), writes pending commands for
zeus_live_clock.apply_pending. Does not touch paper-clock.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from astra_bot.decision.live_commands import enqueue

logger = logging.getLogger(__name__)

OFFSET_PATH = Path("models/zeus_tg_commands_offset.json")
CMD_MAP = {
    "/stop": "stop",
    "/flat": "flat",
    "/resume": "resume",
}
REPLIES = {
    "stop": "стоп: новые входы запрещены",
    "flat": "флэт: выполняю",
    "resume": "разрешено",
}


def parse_command(text: str | None) -> str | None:
    """Return canonical cmd (stop|flat|resume) or None."""
    if not text:
        return None
    first = text.strip().split()[0] if text.strip() else ""
    first = first.split("@", 1)[0].lower()
    return CMD_MAP.get(first)


def load_offset(path: Path = OFFSET_PATH) -> int:
    if not path.exists():
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return int(raw.get("offset") or 0)
    except Exception:
        return 0


def save_offset(offset: int, path: Path = OFFSET_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"offset": int(offset)}), encoding="utf-8")


def _api(token: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    q = urllib.parse.urlencode(params or {})
    url = f"https://api.telegram.org/bot{token}/{method}"
    if q:
        url = f"{url}?{q}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=35) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_reply(token: str, chat_id: int | str, text: str) -> None:
    try:
        _api(token, "sendMessage", {"chat_id": str(chat_id), "text": text})
    except Exception as exc:
        logger.warning("send_reply failed: %s", type(exc).__name__)


def process_update(
    update: dict[str, Any],
    *,
    commands_path: Path | None = None,
    token: str | None = None,
) -> str | None:
    """Parse one update; enqueue if command. Returns cmd or None."""
    msg = update.get("message") or update.get("edited_message") or {}
    text = msg.get("text")
    cmd = parse_command(text if isinstance(text, str) else None)
    if not cmd:
        return None
    kwargs: dict[str, Any] = {"source": "tg"}
    if commands_path is not None:
        kwargs["path"] = commands_path
    enqueue(cmd, **kwargs)
    chat = (msg.get("chat") or {}).get("id")
    if token and chat is not None:
        send_reply(token, chat, REPLIES.get(cmd, cmd))
    return cmd


def poll_once(
    token: str,
    *,
    timeout_sec: int = 25,
    offset_path: Path = OFFSET_PATH,
    commands_path: Path | None = None,
) -> int:
    """Long-poll getUpdates once window. Returns number of commands handled."""
    offset = load_offset(offset_path)
    params: dict[str, Any] = {
        "timeout": max(1, int(timeout_sec)),
        "allowed_updates": json.dumps(["message"]),
    }
    if offset:
        params["offset"] = offset
    try:
        data = _api(token, "getUpdates", params)
    except urllib.error.HTTPError as exc:
        logger.warning("getUpdates HTTP %s", exc.code)
        return 0
    except Exception as exc:
        logger.warning("getUpdates failed: %s", type(exc).__name__)
        return 0
    if not data.get("ok"):
        return 0
    handled = 0
    max_id = offset
    for upd in data.get("result") or []:
        if not isinstance(upd, dict):
            continue
        uid = int(upd.get("update_id") or 0)
        if uid >= max_id:
            max_id = uid + 1
        if process_update(upd, commands_path=commands_path, token=token):
            handled += 1
    if max_id > offset:
        save_offset(max_id, offset_path)
    return handled


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Poll TG for /stop /flat /resume")
    p.add_argument("--timeout", type=int, default=25, help="long-poll seconds")
    args = p.parse_args()
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        logger.info("TELEGRAM_BOT_TOKEN missing — skip poll")
        return 0
    n = poll_once(token, timeout_sec=args.timeout)
    logger.info("tg_live_commands: handled %d", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
