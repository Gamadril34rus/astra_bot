#!/usr/bin/env python3
"""Zeus live clock — one tick: reconcile → signals → entries → exits.

MANUAL ONLY (workflow_dispatch). Inactive unless ZEUS_LIVE=1.
VST (demo) by default; main-net requires ZEUS_MAINNET=1.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("zeus_live_clock")


def _guard() -> None:
    """Hard ABORT unless ZEUS_LIVE=1 (and paper path untouched)."""
    if (os.environ.get("ZEUS_LIVE") or "").strip() != "1":
        logger.error("ABORT: ZEUS_LIVE!=1 — live clock refused")
        raise SystemExit(2)
    if (os.environ.get("ENVIRONMENT") or "").strip().lower() == "live":
        if (os.environ.get("ZEUS_LIVE") or "").strip() != "1":
            logger.error("ABORT: ENVIRONMENT=live requires ZEUS_LIVE=1")
            raise SystemExit(2)


async def _send_live_tg(text: str) -> None:
    """Minimal TG send for live rockets (token/chat from env)."""
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_LIVE_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        logger.info("arm TG skip: no TELEGRAM_BOT_TOKEN/CHAT_ID")
        return
    try:
        import aiohttp

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.warning("arm TG HTTP %s: %s", resp.status, body[:200])
    except Exception as exc:
        logger.warning("arm TG failed: %s", type(exc).__name__)


async def one_tick(*, dry: bool = False) -> int:
    _guard()
    # late imports so ABORT never loads broker on paper hosts by mistake path
    from astra_bot.adapters.bingx.client_live import LiveBingXClient
    from astra_bot.decision.live_broker import LiveBroker, LiveBrokerConfig
    from astra_bot.decision.live_commands import apply_pending

    client = LiveBingXClient({})
    await client.initialize()

    broker = LiveBroker(client=client, config=LiveBrokerConfig())

    # One-shot connectivity probe (set TG_TEST=1 in workflow / secrets)
    if (os.environ.get("TG_TEST") or "").strip() == "1":
        await _send_live_tg("✅ live TG связь есть")
        logger.info("TG_TEST=1 probe sent")
    # kill / halt hooks (optional state files)
    try:
        from astra_bot.core.kill_switch import KillSwitch

        ks = KillSwitch()
        if getattr(ks, "is_halted", lambda: False)():
            broker.set_halt("kill_switch")
    except Exception:
        pass

    cmd_lines = await apply_pending(broker)
    for ln in cmd_lines:
        logger.info("%s", ln)

    # Temporary VST balance diagnostics — flag-gated, read-only.
    if Path("models/zeus_live_debug.json").exists():
        from astra_bot.adapters.bingx.vst_diag import dump as _diag

        await _diag(client, logger)

    rec = await broker.reconcile()
    logger.info(
        "reconcile equity=%s positions=%s orders=%s missing_stops=%s",
        rec.get("equity"),
        rec.get("positions"),
        rec.get("open_orders"),
        rec.get("missing_stops"),
    )

    # P1: restore protective stops AFTER reconcile (plan or naked-close)
    try:
        rest = await broker.restore_missing_stops()
        if rest.get("restored") or rest.get("closed_naked"):
            logger.info("restore_missing_stops: %s", rest)
    except Exception as exc:
        logger.warning("restore_missing_stops failed: %s", type(exc).__name__)

    try:
        synced = await broker.sync_closed_plans(send_tg=_send_live_tg)
        logger.info("sync_closed_plans: %s", synced)
    except Exception as exc:
        logger.exception("sync_closed_plans failed: %s", type(exc).__name__)

    # P2: signal→entry ONLY when ZEUS_ARM=1 (owner enables in separate PR)
    from astra_bot.decision.live_arm import arm_enabled
    if dry or not broker.entries_allowed:
        logger.info("tick done (no entries: dry=%s allowed=%s)", dry, broker.entries_allowed)
    elif not arm_enabled():
        logger.info("tick done — ZEUS_ARM!=1 (observe only)")
    else:
        logger.info("ZEUS_ARM=1 — entry hook active (candidates via arm pipeline)")

    # B-003: risk AFTER sync (order: sync → risk → arm)
    try:
        from astra_bot.decision.live_commands import is_blocked
        from astra_bot.decision.live_risk import (
            check_day_week_limits,
            check_pf_kill,
            load_kill_halted,
        )

        if is_blocked():
            broker.block_entries()
            logger.info("entries blocked via zeus_live_blocked.json")

        prior = load_kill_halted()
        if prior:
            broker.set_halt(prior)
            logger.error("HALT from kill state: %s", prior)
        else:
            eq = await broker.equity()
            reason = check_day_week_limits(eq)
            if reason:
                broker.set_halt(reason)
                logger.error("HALT day/week: %s", reason)
            else:
                pf_reason = check_pf_kill()
                if pf_reason:
                    broker.set_halt(pf_reason)
                    logger.error("HALT PF kill: %s", pf_reason)
    except Exception as exc:
        logger.error("risk_check_failed: %s", type(exc).__name__)
        broker.set_halt("risk_check_failed")

    # ARM: function-level (NOT inside risk except) — only when armed & allowed
    if (not dry) and broker.entries_allowed and arm_enabled():
        from astra_bot.decision.live_arm_feed import FEED_SYMBOLS, produce_pending
        from astra_bot.decision.live_arm_wiring import (
            ENTRIES_PATH,
            _seen_signal_ids,
            load_pending_rows,
            run_arm_entries,
        )
        try:
            seen = _seen_signal_ids(ENTRIES_PATH)
        except Exception:
            seen = set()
        await produce_pending(client, FEED_SYMBOLS, seen_ids=seen)
        rows = load_pending_rows()

        entered = await run_arm_entries(
            broker, rows, max_entries=1, send_tg=_send_live_tg
        )
        logger.info("arm tick entries=%d", len(entered))

    session = getattr(client, "_session", None)
    if session is not None:
        await session.close()
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Zeus live clock (VST default)")
    p.add_argument("--dry", action="store_true", help="reconcile + commands only")
    args = p.parse_args()
    raise SystemExit(asyncio.run(one_tick(dry=args.dry)))


if __name__ == "__main__":
    main()
