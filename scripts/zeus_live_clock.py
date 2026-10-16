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
import time
from decimal import Decimal
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


async def sync_partial_breakeven(
    broker,
    *,
    positions_path: Path | None = None,
    send_tg=None,
) -> dict:
    """C: after TP1 (~40% filled), remaining ~60% → move stop to entry (breakeven).

    Called EVERY tick (owner rule). Detects exchange qty ≈ 0.6 * plan qty,
    plan has tp1, not yet be_moved. Moves protective stop to entry via
    broker.move_stop, sets be_moved in plan, journals + optional TG.
    Fail-open: any exception → skip symbol, continue.
    """
    from astra_bot.decision.live_arm_wiring import append_journal
    from astra_bot.decision.live_positions import load_positions, upsert_plan

    path = positions_path or Path("models/zeus_live_positions.json")
    moved: list[str] = []
    skipped: list[str] = []

    try:
        positions = await broker.client.get_positions()
    except Exception as exc:
        logger.warning("sync_partial_breakeven: get_positions failed: %s", type(exc).__name__)
        return {"moved": [], "skipped": [], "error": type(exc).__name__}

    plans = load_positions(path)
    if not plans:
        return {"moved": [], "skipped": []}

    by_sym: dict[str, object] = {}
    for p in positions:
        sym = getattr(p, "symbol", "") or ""
        qty = getattr(p, "quantity", None) or Decimal("0")
        if sym and qty and qty > 0:
            by_sym[sym] = p

    for _key, plan in list(plans.items()):
        sym = str(plan.get("symbol") or _key.split("|")[0] or "")
        if not sym:
            continue
        if plan.get("be_moved"):
            continue
        # need explicit tp1 (partial channel); legacy full-TP plans skip
        if plan.get("tp1") is None:
            continue
        if plan.get("entry") is None:
            skipped.append(sym)
            continue
        pos = by_sym.get(sym)
        if pos is None:
            continue
        try:
            exch_qty = Decimal(str(getattr(pos, "quantity", 0) or 0))
            plan_qty = Decimal(str(plan.get("qty") or 0))
        except Exception:
            skipped.append(sym)
            continue
        if plan_qty <= 0 or exch_qty <= 0:
            continue
        # remaining ≈ 60% of original (TP1 took 40%); tolerance ±15%
        ratio = float(exch_qty / plan_qty)
        if not (0.45 <= ratio <= 0.75):
            continue
        side = str(plan.get("side") or "long").lower()
        try:
            entry_px = Decimal(str(plan["entry"]))
            await broker.move_stop(sym, side, exch_qty, entry_px)
            upsert_plan(
                symbol=sym,
                side=side,
                entry=entry_px,
                stop=entry_px,
                qty=exch_qty,
                path=path,
                tp=plan.get("tp"),
                tp1=plan.get("tp1"),
                be_moved=True,
                signal_id=plan.get("signal_id"),
            )
            moved.append(sym)
            base = (sym.split("-")[0] if "-" in sym else sym).replace("USDT", "")
            side_ru = "ЛОНГ" if side == "long" else "ШОРТ"
            msg = f"🔒 {base} {side_ru}: BE после TP1 (stop→entry)"
            try:
                append_journal(
                    {
                        "event": "partial_breakeven",
                        "symbol": sym,
                        "side": side,
                        "entry": str(entry_px),
                        "qty_remaining": str(exch_qty),
                        "plan_qty": str(plan_qty),
                        "ratio": round(ratio, 4),
                        "ts": time.time(),
                    }
                )
            except Exception as jexc:
                logger.warning("sync_partial journal: %s", type(jexc).__name__)
            if send_tg is not None:
                try:
                    await send_tg(msg)
                except Exception:
                    pass
            logger.info(
                "sync_partial_breakeven: %s ratio=%.3f stop→entry %s",
                sym, ratio, entry_px,
            )
        except Exception as exc:
            logger.warning(
                "sync_partial_breakeven: %s failed: %s",
                sym, type(exc).__name__,
            )
            skipped.append(sym)

    return {"moved": moved, "skipped": skipped}


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

    # Telemetry: order_audit every tick — stop/tp counts per plan symbol
    try:
        from astra_bot.decision.live_arm_wiring import append_journal
        from astra_bot.decision.live_positions import load_positions

        plans = load_positions()
        if plans:
            try:
                open_orders = await client.get_open_orders()
            except Exception:
                open_orders = []
            by_sym: dict[str, list] = {}
            for o in open_orders:
                by_sym.setdefault(getattr(o, "symbol", "") or "", []).append(o)
            for _key, plan in plans.items():
                sym = str(plan.get("symbol") or _key.split("|")[0] or "")
                if not sym:
                    continue
                orders = by_sym.get(sym, [])
                n_stop = sum(
                    1
                    for o in orders
                    if "stop" in str(getattr(o, "order_type", "")).lower()
                    and "take_profit" not in str(getattr(o, "order_type", "")).lower()
                )
                n_tp = sum(
                    1
                    for o in orders
                    if "take_profit" in str(getattr(o, "order_type", "")).lower()
                )
                append_journal(
                    {
                        "event": "order_audit",
                        "symbol": sym,
                        "side": plan.get("side"),
                        "n_stop": n_stop,
                        "n_tp": n_tp,
                        "be_moved": bool(plan.get("be_moved")),
                        "ts": time.time(),
                    }
                )
    except Exception as _oa_exc:
        logger.warning("order_audit FAILED: %s", type(_oa_exc).__name__)


    # C: partial TP1 → BE — EVERY tick (owner rule), after restore so stop is present
    try:
        partial = await sync_partial_breakeven(broker, send_tg=_send_live_tg)
        if partial.get("moved") or partial.get("skipped"):
            logger.info("sync_partial_breakeven: %s", partial)
    except Exception as exc:
        logger.warning("sync_partial_breakeven failed: %s", type(exc).__name__)

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
            eq = await broker.client.get_balance_usdt()  # owner 16.10: day/week % от РЕАЛЬНОГО счёта
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
