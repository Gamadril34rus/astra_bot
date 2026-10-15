#!/usr/bin/env python3
"""Zeus LIVE clock — one tick. INACTIVE without ZEUS_LIVE=1.

Paper path (run_paper_zeus / PaperBroker) is never imported here for trading.
VST by default (ZEUS_VST=1). Main-net only with ZEUS_MAINNET=1.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("zeus_live")


def _abort_tg(reason: str) -> None:
    """Best-effort alert on guard ABORT (sync; no broker)."""
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (
        (os.environ.get("TELEGRAM_ADMIN_ID") or "").strip()
        or (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    )
    if not token or not chat:
        return
    try:
        import json
        import urllib.request

        body = json.dumps({"chat_id": chat, "text": f"⛔ live ABORT: {reason}"[:4000]}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc:
        logger.warning("abort TG failed: %s", type(exc).__name__)


def _guard() -> None:
    env = (os.environ.get("ENVIRONMENT") or "").strip().lower()
    live = (os.environ.get("ZEUS_LIVE") or "").strip()
    if env == "live" and live != "1":
        logger.error("ABORT: ENVIRONMENT=live requires ZEUS_LIVE=1")
        _abort_tg("ENVIRONMENT=live requires ZEUS_LIVE=1")
        sys.exit(2)
    if live != "1":
        logger.error("ABORT: ZEUS_LIVE!=1 — live clock inactive")
        _abort_tg("ZEUS_LIVE!=1")
        sys.exit(2)
    if env != "live":
        logger.error("ABORT: ZEUS_LIVE=1 requires ENVIRONMENT=live (got %r)", env)
        _abort_tg(f"ENVIRONMENT={env!r} not live")
        sys.exit(2)
    mainnet = (os.environ.get("ZEUS_MAINNET") or "").strip()
    vst = (os.environ.get("ZEUS_VST") or "1").strip()
    if mainnet == "1" and vst in ("1", "true", "yes"):
        logger.error("ABORT: ZEUS_MAINNET=1 conflicts with ZEUS_VST=1")
        _abort_tg("MAINNET conflicts with VST")
        sys.exit(2)
    if mainnet == "1":
        logger.warning("MAINNET mode — real money path enabled by owner flag")
    else:
        logger.info("VST mode (demo futures) — default")


async def _send_live_tg(msg: str) -> None:
    """Same chat as paper: prefer TELEGRAM_ADMIN_ID (paper path), then CHAT_ID."""
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (
        (os.environ.get("TELEGRAM_ADMIN_ID") or "").strip()
        or (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    )
    if not token or not chat:
        logger.warning("arm TG skip: no TELEGRAM_BOT_TOKEN/ADMIN_ID/CHAT_ID")
        return
    try:
        import aiohttp

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        async with aiohttp.ClientSession() as session, session.post(
            url, json={"chat_id": chat, "text": msg[:4000]}
        ) as resp:
            if resp.status >= 400:
                body = (await resp.text())[:200]
                logger.warning("arm TG HTTP %s body=%s", resp.status, body)
    except Exception as exc:
        logger.warning("arm TG failed: %s", type(exc).__name__)


async def one_tick(*, dry: bool = False) -> int:
    _guard()
    from astra_bot.adapters.bingx.client_live import LiveBingXClient
    from astra_bot.decision.live_broker import LiveBroker, LiveBrokerConfig
    from astra_bot.decision.live_commands import apply_pending

    client = LiveBingXClient({})
    await client.initialize()

    broker = LiveBroker(client=client, config=LiveBrokerConfig())
    try:
        from astra_bot.decision.vol_cd_hooks import apply_live_sync_hook

        apply_live_sync_hook(broker)
    except Exception as _vol_exc:
        logger.warning("vol_cd live hook FAILED: %s", _vol_exc)

    if (os.environ.get("TG_TEST") or "").strip() == "1":
        await _send_live_tg("✅ live TG связь есть")
        logger.info("TG_TEST=1 probe sent")
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

    try:
        from astra_bot.adapters.bingx.income_backfill import run_income_backfill

        bf = await run_income_backfill(client)
        if bf.get("updated"):
            logger.info("income_backfill: %s", bf)
    except Exception as _bf_exc:
        logger.warning("income_backfill FAILED: %s", type(_bf_exc).__name__)

    from astra_bot.decision.live_arm import arm_enabled

    if dry or not broker.entries_allowed:
        logger.info("tick done (no entries: dry=%s allowed=%s)", dry, broker.entries_allowed)
    elif not arm_enabled():
        logger.info("tick done — ZEUS_ARM!=1 (observe only)")
    else:
        logger.info("ZEUS_ARM=1 — entry hook active (candidates via arm pipeline)")

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

        entered, skipped = await run_arm_entries(
            broker, rows, max_entries=3, send_tg=_send_live_tg
        )
        logger.info("arm tick entries=%d skipped=%s", len(entered), skipped)
        try:
            from astra_bot.decision.live_arm_wiring import append_journal

            entered_syms = []
            for e in entered:
                if isinstance(e, dict):
                    sym = e.get("symbol") or e.get("sym")
                    side = e.get("side")
                    if sym:
                        entered_syms.append(f"{sym}|{side or ''}")
            unrealized = None
            try:
                if hasattr(broker, "unrealized_total"):
                    unrealized = float(broker.unrealized_total())  # type: ignore[operator]
                elif hasattr(broker, "unrealized_sum"):
                    unrealized = float(broker.unrealized_sum)  # type: ignore[arg-type]
            except Exception:
                unrealized = None
            append_journal(
                {
                    "event": "arm_summary",
                    "dry": bool(dry),
                    "entries_allowed": bool(broker.entries_allowed),
                    "halt": getattr(broker, "_halt_reason", None) or None,
                    "candidates": len(rows),
                    "entered": entered_syms,
                    "skipped": dict(skipped),
                    "unrealized_sum": unrealized,
                }
            )
        except Exception as _sum_exc:
            logger.warning("arm_summary FAILED: %s", type(_sum_exc).__name__)

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
