#!/usr/bin/env python3
"""Zeus LIVE clock — one tick. INACTIVE without ZEUS_LIVE=1.

Paper path (run_paper_zeus / PaperBroker) is never imported here for trading.
VST by default (ZEUS_VST=1). Main-net only with ZEUS_MAINNET=1.
"""
from __future__ import annotations

import argparse
import asyncio
import json
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



def write_live_equity(equity: float | None, path: Path | None = None) -> None:
    """F6.3: refresh models/zeus_live_equity.json every tick (ts=now) for TG balance."""
    p = path or Path("models/zeus_live_equity.json")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        import time as _time
        payload = {"equity": float(equity) if equity is not None else None, "ts": _time.time()}
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("write_live_equity failed: %s", type(exc).__name__)


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



# F15: margin-reject warn + tp_parity_v1 (imports after sys.path insert above)
from astra_bot.decision.live_margin_warn import (  # noqa: E402
    maybe_warn_margin_reject as _maybe_warn_margin_reject,
)
from astra_bot.decision.live_margin_warn import (  # noqa: E402
    set_send_tg as _set_margin_warn_tg,
)

_TP_PARITY_DONE = "tp_parity_v1"



_INITIAL_STOP_DONE = "initial_stop_v1"


def migrate_initial_stop_v1() -> dict:
    """One-shot: fill plan.initial_stop for open plans (F15b).

    Sources (priority):
      1) journal entry row for signal_id with field stop
      2) from tp_parity_* : |entry − old_tp| / 1.5  → reconstruct initial risk
      3) current stop if be_moved is false
    """
    from astra_bot.decision.live_arm_wiring import append_journal
    from astra_bot.decision.live_positions import DEFAULT_PATH, load_positions, save_positions

    out = {"checked": 0, "migrated": 0, "skipped": 0}
    plans = load_positions(DEFAULT_PATH)
    if not plans:
        return out
    # index entry stops from journal (best-effort, last wins)
    entry_stop_by_sid: dict[str, str] = {}
    entry_stop_by_sym: dict[str, str] = {}
    parity_old_tp: dict[str, str] = {}
    jpath = Path("models/zeus_live_journal.jsonl")
    tpath = Path("models/zeus_live_trades.jsonl")
    for path in (jpath, tpath):
        if not path.exists():
            continue
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    ev = str(o.get("event") or "")
                    if ev == "entry" and o.get("stop") is not None:
                        sid = str(o.get("signal_id") or "")
                        sym = str(o.get("symbol") or "")
                        if sid:
                            entry_stop_by_sid[sid] = str(o["stop"])
                        if sym:
                            entry_stop_by_sym[sym] = str(o["stop"])
                    if ev in ("tp_parity_replace", "tp_parity_failed") and o.get("old_tp"):
                        sym = str(o.get("symbol") or "")
                        if sym and sym not in parity_old_tp:
                            parity_old_tp[sym] = str(o["old_tp"])
        except Exception:
            pass
    changed = False
    for key, plan in list(plans.items()):
        if not isinstance(plan, dict):
            continue
        out["checked"] += 1
        if plan.get("initial_stop") is not None and plan.get(_INITIAL_STOP_DONE):
            out["skipped"] += 1
            continue
        if plan.get("initial_stop") is not None:
            plan[_INITIAL_STOP_DONE] = True
            plans[key] = plan
            changed = True
            out["skipped"] += 1
            continue
        sym = str(plan.get("symbol") or key.split("|")[0])
        sid = str(plan.get("signal_id") or "")
        init = None
        src = None
        if sid and sid in entry_stop_by_sid:
            init, src = entry_stop_by_sid[sid], "journal_entry_sid"
        elif sym in entry_stop_by_sym:
            init, src = entry_stop_by_sym[sym], "journal_entry_sym"
        elif sym in parity_old_tp and plan.get("entry") is not None:
            try:
                entry = Decimal(str(plan["entry"]))
                old_tp = Decimal(str(parity_old_tp[sym]))
                # pre-parity TP was ~1.5R from entry with initial stop
                risk = abs(old_tp - entry) / Decimal("1.5")
                if plan.get("side", "").lower() == "long":
                    init = str(entry - risk)
                else:
                    init = str(entry + risk)
                src = "parity_old_tp_1_5"
            except Exception:
                init = None
        if init is None and not plan.get("be_moved") and plan.get("stop") is not None:
            init, src = str(plan["stop"]), "current_stop"
        if init is None:
            out["skipped"] += 1
            continue
        plan["initial_stop"] = init
        plan[_INITIAL_STOP_DONE] = True
        plans[key] = plan
        changed = True
        out["migrated"] += 1
        try:
            append_journal({
                "event": "migrate_initial_stop",
                "symbol": sym,
                "initial_stop": init,
                "source": src,
                "ts": time.time(),
            })
        except Exception:
            pass
    if changed:
        save_positions(plans, DEFAULT_PATH)
    return out


async def apply_tp_parity_v1(broker, send_tg=None) -> dict:
    """One-shot: open plans with RR outside [2.0,2.3] → place new TP then cancel old.

    REPLACE-FIRST: place clamped TP (qty=exch*0.3) → success → cancel old TPs.
    Never leave a window without a TP. If cancel fails because the old leg
    already filled → journal tp_parity_old_filled (not a hard failure).
    Stop orders are never touched.
    """
    from decimal import Decimal

    from astra_bot.decision.exit_plan import clamp_take_rr
    from astra_bot.decision.live_arm_wiring import append_journal
    from astra_bot.decision.live_positions import DEFAULT_PATH, load_positions, save_positions

    out = {"checked": 0, "replaced": 0, "failed": 0, "skipped": 0, "old_filled": 0}
    plans = load_positions(DEFAULT_PATH)
    if not plans:
        return out
    try:
        positions = await broker.client.get_positions()
    except Exception as exc:
        return {**out, "error": type(exc).__name__}
    by_sym = {}
    for p in positions or []:
        sym = getattr(p, "symbol", None) or getattr(p, "symbol_name", None)
        if not sym:
            continue
        by_sym[str(sym)] = p
    for key, plan in list(plans.items()):
        if not isinstance(plan, dict):
            continue
        if plan.get(_TP_PARITY_DONE):
            out["skipped"] += 1
            continue
        sym = plan.get("symbol") or key.split("|")[0]
        side = (plan.get("side") or "").lower()
        entry = plan.get("entry")
        stop = plan.get("stop")
        tp = plan.get("tp") or plan.get("tp1")
        if entry is None or stop is None or tp is None:
            out["skipped"] += 1
            continue
        try:
            entry_d = Decimal(str(entry))
            stop_d = Decimal(str(stop))
            tp_d = Decimal(str(tp))
            # F15b: RR and clamp ONLY from initial_stop (never current/BE stop)
            init_raw = plan.get("initial_stop")
            if init_raw is not None:
                try:
                    init_d = Decimal(str(init_raw))
                except Exception:
                    init_d = stop_d
            else:
                init_d = stop_d
            risk = abs(entry_d - init_d)
            if risk <= 0:
                out["skipped"] += 1
                continue
            rr = abs(tp_d - entry_d) / risk
            out["checked"] += 1
            if Decimal("2.0") <= rr <= Decimal("2.3"):
                plan[_TP_PARITY_DONE] = True
                plans[key] = plan
                save_positions(plans)
                out["skipped"] += 1
                continue
            # Early TP1 (ZEUS_TP1_RR default 0.6): intentional sub-2R leg.
            # Parity must NOT widen early locks to 2.0–2.3 (owner 30.10 — #279).
            if rr < Decimal("2.0"):
                plan[_TP_PARITY_DONE] = True
                plan["early_tp1"] = True
                plans[key] = plan
                save_positions(plans)
                append_journal(
                    {
                        "event": "tp_parity_early_preserved",
                        "symbol": sym,
                        "side": side,
                        "tp": str(tp_d),
                        "rr": float(rr),
                        "ts": __import__("time").time(),
                    }
                )
                out["skipped"] += 1
                continue
            # rr > 2.3 only: clamp down to corridor
            new_tp = clamp_take_rr(entry_d, init_d, tp_d, side, 2.0, 2.3)
            pos = by_sym.get(sym)
            exch_qty = Decimal(
                str(
                    getattr(pos, "quantity", None)
                    or getattr(pos, "qty", None)
                    or plan.get("qty")
                    or 0
                )
            )
            if exch_qty <= 0:
                out["skipped"] += 1
                continue
            frac = Decimal(str(plan.get("tp_frac") or plan.get("tp_frac_1") or "0.3"))
            if frac <= 0 or frac >= 1:
                frac = Decimal("0.3")
            q_tp = (exch_qty * frac).quantize(Decimal("0.00000001"))
            old_tp = str(tp)
            plan["tp"] = str(new_tp)
            plan["tp1"] = str(new_tp)
            plan["tp_frac"] = float(frac)
            # Collect old TP ids BEFORE place (replace-first: place new, then cancel)
            old_tp_ids: list[str] = []
            try:
                if hasattr(broker, "client") and hasattr(broker.client, "get_open_orders"):
                    orders = await broker.client.get_open_orders(sym)
                    for o in orders or []:
                        ot = str(
                            getattr(o, "type", "") or getattr(o, "order_type", "") or ""
                        ).upper()
                        if "TAKE_PROFIT" in ot:
                            oid = getattr(o, "id", None) or getattr(o, "order_id", None)
                            if oid:
                                old_tp_ids.append(str(oid))
            except Exception:
                old_tp_ids = []
            ps = "LONG" if side == "long" else "SHORT"
            stop_side = "SELL" if side == "long" else "BUY"
            try:
                # 1) PLACE new TP first — never naked
                await broker.client.place_order(
                    symbol=sym,
                    side=stop_side,
                    order_type="TAKE_PROFIT_MARKET",
                    quantity=q_tp,
                    stop_price=new_tp,
                    position_side=ps,
                    reduce_only=False,
                    close_position=False,
                )
                # 2) CANCEL old TPs only after place succeeded
                for oid in old_tp_ids:
                    try:
                        await broker.client.cancel_order(sym, oid)
                        try:
                            from astra_bot.decision.live_broker import journal_order_cancel
                            journal_order_cancel(
                                symbol=sym,
                                order_id=oid,
                                order_type="TAKE_PROFIT",
                                reason="tp_parity_replace",
                                path="apply_tp_parity_v1",
                                side=side,
                            )
                        except Exception:
                            pass
                    except Exception as cexc:
                        cerr = str(cexc).lower()
                        if any(
                            x in cerr
                            for x in (
                                "filled",
                                "not found",
                                "does not exist",
                                "already",
                                "cancel",
                            )
                        ):
                            append_journal(
                                {
                                    "event": "tp_parity_old_filled",
                                    "symbol": sym,
                                    "side": side,
                                    "order_id": oid,
                                    "error": f"{type(cexc).__name__}: {cexc}",
                                    "ts": __import__("time").time(),
                                }
                            )
                            out["old_filled"] += 1
                        else:
                            append_journal(
                                {
                                    "event": "tp_parity_cancel_failed",
                                    "symbol": sym,
                                    "side": side,
                                    "order_id": oid,
                                    "error": f"{type(cexc).__name__}: {cexc}",
                                    "ts": __import__("time").time(),
                                }
                            )
                plan[_TP_PARITY_DONE] = True
                plans[key] = plan
                save_positions(plans)
                append_journal(
                    {
                        "event": "tp_parity_replace",
                        "symbol": sym,
                        "side": side,
                        "old_tp": old_tp,
                        "new_tp": str(new_tp),
                        "rr_old": float(rr),
                        "qty": str(q_tp),
                        "ts": __import__("time").time(),
                    }
                )
                out["replaced"] += 1
            except Exception as exc:
                append_journal(
                    {
                        "event": "tp_parity_failed",
                        "symbol": sym,
                        "side": side,
                        "old_tp": old_tp,
                        "new_tp": str(new_tp),
                        "error": f"{type(exc).__name__}: {exc}",
                        "ts": __import__("time").time(),
                    }
                )
                _maybe_warn_margin_reject(sym, str(exc), send_tg)
                out["failed"] += 1
        except Exception:
            out["skipped"] += 1
    return out




async def repair_early_tp_after_parity(broker, send_tg=None) -> dict:
    """One-shot: plans already parity-clamped to 2.xR → restore ZEUS_TP1_RR leg.

    After #279, apply_tp_parity_v1 had widened intentional 0.6R TPs to 2.0–2.3R.
    Replace-first restore early TP once per plan (flag early_tp_repair_v1).

    If early TP level is already through the mark (short: mark<=new_tp,
    long: mark>=new_tp) — do NOT place invalid TP: market-close frac leg +
    move_stop to entry (same as open_with_protection TP-past). Always set the
    repair flag so we do not spam TG every tick (SOL 30.10).
    """
    import os
    import time
    from decimal import Decimal

    from astra_bot.decision.live_arm_wiring import append_journal
    from astra_bot.decision.live_positions import DEFAULT_PATH, load_positions, save_positions

    out = {
        "checked": 0,
        "repaired": 0,
        "past_level": 0,
        "failed": 0,
        "skipped": 0,
    }
    plans = load_positions(DEFAULT_PATH)
    if not plans:
        return out
    try:
        early_rr = Decimal(str(os.environ.get("ZEUS_TP1_RR", "0.6") or "0.6"))
        if early_rr <= 0 or early_rr > 3:
            early_rr = Decimal("0.6")
    except Exception:
        early_rr = Decimal("0.6")
    try:
        positions = await broker.client.get_positions()
    except Exception as exc:
        return {**out, "error": type(exc).__name__}
    by_sym: dict = {}
    for p in positions or []:
        sym = getattr(p, "symbol", None) or getattr(p, "symbol_name", None)
        if sym:
            by_sym[str(sym)] = p

    def _mark_of(pos) -> Decimal | None:
        if pos is None:
            return None
        for attr in ("mark_price", "markPrice", "mark", "last_price", "lastPrice"):
            v = getattr(pos, attr, None)
            if v is not None:
                try:
                    d = Decimal(str(v))
                    if d > 0:
                        return d
                except Exception:
                    pass
        return None

    def _price_past(side: str, new_tp: Decimal, mark: Decimal | None) -> bool:
        if mark is None or mark <= 0:
            return False
        if side == "short":
            return mark <= new_tp  # need TP < mark
        return mark >= new_tp  # long: need TP > mark

    for key, plan in list(plans.items()):
        if not isinstance(plan, dict):
            continue
        if plan.get("early_tp_repair_v1") or plan.get("early_tp1"):
            out["skipped"] += 1
            continue
        if not plan.get(_TP_PARITY_DONE):
            out["skipped"] += 1
            continue
        sym = plan.get("symbol") or key.split("|")[0]
        side = (plan.get("side") or "").lower()
        entry = plan.get("entry")
        init_raw = plan.get("initial_stop") or plan.get("stop")
        tp = plan.get("tp") or plan.get("tp1")
        if entry is None or init_raw is None or tp is None:
            out["skipped"] += 1
            continue
        try:
            entry_d = Decimal(str(entry))
            init_d = Decimal(str(init_raw))
            tp_d = Decimal(str(tp))
            risk = abs(entry_d - init_d)
            if risk <= 0:
                out["skipped"] += 1
                continue
            rr = abs(tp_d - entry_d) / risk
            out["checked"] += 1
            if rr < Decimal("2.0"):
                plan["early_tp1"] = True
                plan["early_tp_repair_v1"] = True
                plans[key] = plan
                save_positions(plans)
                out["skipped"] += 1
                continue
            if side == "long":
                new_tp = entry_d + risk * early_rr
            else:
                new_tp = entry_d - risk * early_rr
            pos = by_sym.get(sym)
            mark = _mark_of(pos)
            exch_qty = Decimal(
                str(
                    getattr(pos, "quantity", None)
                    or getattr(pos, "qty", None)
                    or plan.get("qty")
                    or 0
                )
            )
            if exch_qty <= 0:
                out["skipped"] += 1
                continue
            frac = Decimal(str(plan.get("tp_frac") or "0.3"))
            if frac <= 0 or frac >= 1:
                frac = Decimal("0.3")
            q_tp = (exch_qty * frac).quantize(Decimal("0.00000001"))
            old_tp = str(tp)
            ps = "LONG" if side == "long" else "SHORT"
            stop_side = "SELL" if side == "long" else "BUY"

            async def _cancel_old_tps() -> None:
                try:
                    if not (hasattr(broker, "client") and hasattr(broker.client, "get_open_orders")):
                        return
                    orders = await broker.client.get_open_orders(sym)
                    for o in orders or []:
                        ot = str(
                            getattr(o, "type", "") or getattr(o, "order_type", "") or ""
                        ).upper()
                        if "TAKE_PROFIT" not in ot:
                            continue
                        oid = getattr(o, "id", None) or getattr(o, "order_id", None)
                        if not oid:
                            continue
                        try:
                            await broker.client.cancel_order(sym, str(oid))
                        except Exception as cexc:
                            append_journal(
                                {
                                    "event": "early_tp_repair_cancel_failed",
                                    "symbol": sym,
                                    "order_id": str(oid),
                                    "error": f"{type(cexc).__name__}: {cexc}",
                                    "ts": time.time(),
                                }
                            )
                except Exception:
                    pass

            async def _past_level_market() -> bool:
                """Market-close frac leg + BE stop; always marks repair done."""
                market_ok = False
                be_ok = False
                try:
                    await broker.client.place_order(
                        symbol=sym,
                        side=stop_side,
                        order_type="MARKET",
                        quantity=q_tp,
                        position_side=ps,
                        reduce_only=False,
                        close_position=False,
                    )
                    market_ok = True
                except Exception as mex:
                    append_journal(
                        {
                            "event": "early_tp_repair_market_failed",
                            "symbol": sym,
                            "side": side,
                            "qty": str(q_tp),
                            "error": f"{type(mex).__name__}: {mex}",
                            "ts": time.time(),
                        }
                    )
                if market_ok and hasattr(broker, "move_stop"):
                    try:
                        await broker.move_stop(sym, side, exch_qty - q_tp if exch_qty > q_tp else exch_qty, entry_d)
                        be_ok = True
                        plan["be_moved"] = True
                    except Exception as bex:
                        append_journal(
                            {
                                "event": "early_tp_repair_be_failed",
                                "symbol": sym,
                                "error": f"{type(bex).__name__}: {bex}",
                                "ts": time.time(),
                            }
                        )
                await _cancel_old_tps()
                plan["tp"] = str(new_tp)
                plan["tp1"] = str(new_tp)
                plan["early_tp1"] = True
                plan["early_tp_repair_v1"] = True
                if market_ok:
                    plan["early_scaled"] = True
                    try:
                        q_left = exch_qty - q_tp
                        if q_left > 0:
                            if side == "long":
                                runner_tp = entry_d + risk * Decimal("1.5")
                            else:
                                runner_tp = entry_d - risk * Decimal("1.5")
                            await broker.client.place_order(
                                symbol=sym,
                                side=stop_side,
                                order_type="TAKE_PROFIT_MARKET",
                                quantity=q_left,
                                stop_price=runner_tp,
                                position_side=ps,
                                reduce_only=False,
                                close_position=False,
                            )
                            plan["tp"] = str(runner_tp)
                            plan["runner_tp_rr"] = 1.5
                            append_journal(
                                {
                                    "event": "runner_tp_after_scale",
                                    "symbol": sym,
                                    "side": side,
                                    "qty": str(q_left),
                                    "tp": str(runner_tp),
                                    "rr": 1.5,
                                    "source": "past_level",
                                    "ts": time.time(),
                                }
                            )
                    except Exception as rtex:
                        append_journal(
                            {
                                "event": "runner_tp_after_scale_failed",
                                "symbol": sym,
                                "error": f"{type(rtex).__name__}: {rtex}",
                                "ts": time.time(),
                            }
                        )
                plans[key] = plan
                save_positions(plans)
                append_journal(
                    {
                        "event": "early_tp_repair_past_level",
                        "symbol": sym,
                        "side": side,
                        "old_tp": old_tp,
                        "new_tp": str(new_tp),
                        "mark": str(mark) if mark is not None else None,
                        "qty": str(q_tp),
                        "market_ok": market_ok,
                        "be_ok": be_ok,
                        "early_scaled": bool(plan.get("early_scaled")),
                        "ts": time.time(),
                    }
                )
                return market_ok

            # Pre-check: level already through mark → market path (no invalid TP)
            if _price_past(side, new_tp, mark):
                ok = await _past_level_market()
                if ok:
                    out["past_level"] += 1
                else:
                    out["failed"] += 1
                    if send_tg:
                        try:
                            await send_tg(
                                f"⚠️ {sym}: early TP уже за mark — market-нога не прошла"
                            )
                        except Exception:
                            pass
                continue

            try:
                await broker.client.place_order(
                    symbol=sym,
                    side=stop_side,
                    order_type="TAKE_PROFIT_MARKET",
                    quantity=q_tp,
                    stop_price=new_tp,
                    position_side=ps,
                    reduce_only=False,
                    close_position=False,
                )
                await _cancel_old_tps()
                plan["tp"] = str(new_tp)
                plan["tp1"] = str(new_tp)
                plan["early_tp1"] = True
                plan["early_tp_repair_v1"] = True
                plans[key] = plan
                save_positions(plans)
                append_journal(
                    {
                        "event": "early_tp_repair",
                        "symbol": sym,
                        "side": side,
                        "old_tp": old_tp,
                        "new_tp": str(new_tp),
                        "rr_old": float(rr),
                        "rr_new": float(early_rr),
                        "qty": str(q_tp),
                        "ts": time.time(),
                    }
                )
                out["repaired"] += 1
            except Exception as exc:
                err = str(exc).lower()
                past = any(
                    x in err
                    for x in (
                        "should be lower",
                        "should be higher",
                        "price should be",
                        "would trigger",
                        "immediately trigger",
                    )
                )
                if past:
                    ok = await _past_level_market()
                    if ok:
                        out["past_level"] += 1
                    else:
                        out["failed"] += 1
                        if send_tg:
                            try:
                                await send_tg(
                                    f"⚠️ {sym}: early TP past mark — market-нога не прошла"
                                )
                            except Exception:
                                pass
                    continue
                # Hard fail: still mark done to avoid TG spam every tick
                plan["early_tp_repair_v1"] = True
                plans[key] = plan
                save_positions(plans)
                append_journal(
                    {
                        "event": "early_tp_repair_failed",
                        "symbol": sym,
                        "side": side,
                        "old_tp": old_tp,
                        "new_tp": str(new_tp),
                        "error": f"{type(exc).__name__}: {exc}",
                        "ts": time.time(),
                    }
                )
                if send_tg:
                    try:
                        await send_tg(f"⚠️ {sym}: early TP repair failed — {exc}")
                    except Exception:
                        pass
                out["failed"] += 1
        except Exception:
            out["skipped"] += 1
    return out




async def heal_ratchet_and_runner_tp(broker, send_tg=None) -> dict:
    """Owner 30.10: apply post-BE ratchet + missing runner TP on live plans.

    Exit-engine can miss ticks (shadow/import/path). Explicit heal each clock:
    - be_moved + MFE>=0.75 from initial_stop → tighten stop to 0.25R profit (or classic >=1.0)
    - early_tp1/early_scaled + no TP on exchange → place 1.5R runner TP
    - dead flat: open >6h, peak MFE <0.25, not be_moved → market close (stop-out zone edge)
    """
    import os
    import time
    from decimal import Decimal

    from astra_bot.decision.live_arm_wiring import append_journal
    from astra_bot.decision.live_exit_engine import ratchet_e_stop_candidate, stop_exchange_valid
    from astra_bot.decision.live_positions import DEFAULT_PATH, load_positions, save_positions

    out = {"ratchet": 0, "runner_tp": 0, "dead_flat": 0, "skipped": 0}
    plans = load_positions(DEFAULT_PATH)
    if not plans:
        return out
    try:
        positions = await broker.client.get_positions()
    except Exception as exc:
        return {**out, "error": type(exc).__name__}
    by_sym: dict = {}
    for p in positions or []:
        sym = getattr(p, "symbol", None) or getattr(p, "symbol_name", None)
        if sym:
            by_sym[str(sym)] = p

    def _mark(pos):
        if pos is None:
            return None
        for a in ("mark_price", "markPrice", "current_price", "price", "mark"):
            v = getattr(pos, a, None)
            if v is not None:
                try:
                    d = Decimal(str(v))
                    if d > 0:
                        return d
                except Exception:
                    pass
        return None

    now = time.time()
    for key, plan in list(plans.items()):
        if not isinstance(plan, dict):
            continue
        sym = plan.get("symbol") or key.split("|")[0]
        side = (plan.get("side") or "").lower()
        entry = plan.get("entry")
        init_raw = plan.get("initial_stop") or plan.get("stop")
        if entry is None or init_raw is None or side not in ("long", "short"):
            out["skipped"] += 1
            continue
        try:
            entry_d = Decimal(str(entry))
            init_d = Decimal(str(init_raw))
            stop_d = Decimal(str(plan.get("stop") or init_raw))
            risk = abs(entry_d - init_d)
        except Exception:
            out["skipped"] += 1
            continue
        if risk <= 0:
            out["skipped"] += 1
            continue
        pos = by_sym.get(sym)
        mark = _mark(pos)
        qty = Decimal(
            str(
                getattr(pos, "quantity", None)
                or getattr(pos, "qty", None)
                or plan.get("qty")
                or 0
            )
        )
        if qty <= 0 or mark is None:
            out["skipped"] += 1
            continue
        peak_h = plan.get("peak_high")
        peak_l = plan.get("peak_low")
        try:
            hi = Decimal(str(peak_h)) if peak_h is not None else None
            lo = Decimal(str(peak_l)) if peak_l is not None else None
        except Exception:
            hi, lo = None, None
        # update peaks from mark
        if side == "long":
            if hi is None or mark > hi:
                hi = mark
                plan["peak_high"] = str(mark)
        else:
            if lo is None or mark < lo:
                lo = mark
                plan["peak_low"] = str(mark)
        be_moved = bool(plan.get("be_moved"))
        # MFE from initial
        if side == "long":
            mfe_r = float(((hi or mark) - entry_d) / risk)
        else:
            mfe_r = float((entry_d - (lo or mark)) / risk)

        # Dead flat: >6h open, never 0.25R, no BE → close
        opened = plan.get("opened_at_ms") or plan.get("ts")
        try:
            opened_s = float(opened) / 1000.0 if float(opened) > 1e12 else float(opened)
        except Exception:
            opened_s = now
        age_h = (now - opened_s) / 3600.0
        if (
            age_h >= 6.0
            and (not be_moved)
            and mfe_r < 0.25
            and os.environ.get("ZEUS_DEAD_FLAT_EXIT", "1").strip().lower()
            not in ("0", "false", "no", "off")
        ):
            try:
                close_side = "SELL" if side == "long" else "BUY"
                ps = "LONG" if side == "long" else "SHORT"
                await broker.client.place_order(
                    symbol=sym,
                    side=close_side,
                    order_type="MARKET",
                    quantity=qty,
                    position_side=ps,
                    reduce_only=False,
                    close_position=False,
                )
                append_journal(
                    {
                        "event": "dead_flat_exit",
                        "symbol": sym,
                        "side": side,
                        "age_h": round(age_h, 2),
                        "mfe_r": round(mfe_r, 4),
                        "mark": str(mark),
                        "ts": now,
                    }
                )
                out["dead_flat"] += 1
                continue
            except Exception as dex:
                append_journal(
                    {
                        "event": "dead_flat_exit_failed",
                        "symbol": sym,
                        "error": f"{type(dex).__name__}: {dex}",
                        "ts": now,
                    }
                )

        # Ratchet heal after BE
        if be_moved:
            cand = ratchet_e_stop_candidate(
                side,
                entry_d,
                stop_d,
                hi,
                lo,
                initial_stop=init_d,
                mark=mark,
                be_moved=True,
            )
            if cand is not None and stop_exchange_valid(side, cand, mark):
                tighter = (side == "long" and cand > stop_d) or (
                    side == "short" and cand < stop_d
                )
                if tighter and hasattr(broker, "move_stop"):
                    try:
                        await broker.move_stop(sym, side, qty, cand)
                        plan["stop"] = str(cand)
                        plans[key] = plan
                        save_positions(plans)
                        append_journal(
                            {
                                "event": "STOP_UPDATE",
                                "symbol": sym,
                                "side": side,
                                "stop_before": str(stop_d),
                                "stop_after": str(cand),
                                "source": "heal_ratchet",
                                "ok": True,
                                "action": "ratchet_e",
                                "mfe_r": round(mfe_r, 4),
                                "ts": now,
                            }
                        )
                        out["ratchet"] += 1
                        stop_d = cand
                    except Exception as rex:
                        append_journal(
                            {
                                "event": "heal_ratchet_failed",
                                "symbol": sym,
                                "error": f"{type(rex).__name__}: {rex}",
                                "ts": now,
                            }
                        )

        # Runner TP if missing (early path left n_tp=0)
        need_tp = bool(plan.get("early_tp1") or plan.get("early_scaled") or be_moved)
        if need_tp and not plan.get("runner_tp_healed_v1"):
            try:
                orders = await broker.client.get_open_orders(sym)
                n_tp = 0
                for o in orders or []:
                    ot = str(getattr(o, "type", "") or getattr(o, "order_type", "") or "").upper()
                    if "TAKE_PROFIT" in ot:
                        n_tp += 1
                if n_tp == 0:
                    if side == "long":
                        runner_tp = entry_d + risk * Decimal("1.5")
                    else:
                        runner_tp = entry_d - risk * Decimal("1.5")
                    # only if TP still on profitable side of mark
                    valid = (side == "long" and runner_tp > mark) or (
                        side == "short" and runner_tp < mark
                    )
                    if valid:
                        stop_side = "SELL" if side == "long" else "BUY"
                        ps = "LONG" if side == "long" else "SHORT"
                        await broker.client.place_order(
                            symbol=sym,
                            side=stop_side,
                            order_type="TAKE_PROFIT_MARKET",
                            quantity=qty,
                            stop_price=runner_tp,
                            position_side=ps,
                            reduce_only=False,
                            close_position=False,
                        )
                        plan["tp"] = str(runner_tp)
                        plan["runner_tp_rr"] = 1.5
                        plan["runner_tp_healed_v1"] = True
                        plans[key] = plan
                        save_positions(plans)
                        append_journal(
                            {
                                "event": "runner_tp_heal",
                                "symbol": sym,
                                "side": side,
                                "tp": str(runner_tp),
                                "qty": str(qty),
                                "ts": now,
                            }
                        )
                        out["runner_tp"] += 1
                    else:
                        plan["runner_tp_healed_v1"] = True  # avoid spam; level past
                        plans[key] = plan
                        save_positions(plans)
            except Exception as tex:
                append_journal(
                    {
                        "event": "runner_tp_heal_failed",
                        "symbol": sym,
                        "error": f"{type(tex).__name__}: {tex}",
                        "ts": now,
                    }
                )

        plans[key] = plan
    try:
        save_positions(plans)
    except Exception:
        pass
    return out


async def scale_out_early_if_due(broker, send_tg=None) -> dict:
    """If MFE >= ZEUS_TP1_RR and exchange TP still at far RR (>=2), market 30% once.

    Covers: parity left 2.xR leg; price already through 0.6R; be_moved runners
    that never scaled out (SOL 30.10 qty full, tp far). Flag early_scaled.
    """
    import os
    import time
    from decimal import Decimal

    from astra_bot.decision.live_arm_wiring import append_journal
    from astra_bot.decision.live_positions import DEFAULT_PATH, load_positions, save_positions

    out = {"checked": 0, "scaled": 0, "failed": 0, "skipped": 0}
    plans = load_positions(DEFAULT_PATH)
    if not plans:
        return out
    try:
        early_rr = Decimal(str(os.environ.get("ZEUS_TP1_RR", "0.6") or "0.6"))
        if early_rr <= 0 or early_rr > 3:
            early_rr = Decimal("0.6")
    except Exception:
        early_rr = Decimal("0.6")
    try:
        positions = await broker.client.get_positions()
    except Exception as exc:
        return {**out, "error": type(exc).__name__}
    by_sym: dict = {}
    for p in positions or []:
        sym = getattr(p, "symbol", None) or getattr(p, "symbol_name", None)
        if sym:
            by_sym[str(sym)] = p

    def _mark(pos):
        if pos is None:
            return None
        for a in ("mark_price", "markPrice", "current_price", "price", "mark"):
            v = getattr(pos, a, None)
            if v is not None:
                try:
                    d = Decimal(str(v))
                    if d > 0:
                        return d
                except Exception:
                    pass
        return None

    for key, plan in list(plans.items()):
        if not isinstance(plan, dict):
            continue
        if plan.get("early_scaled") or plan.get("early_tp1"):
            # early_tp1 means leg already at early RR or handled
            if plan.get("early_scaled") or (
                plan.get("early_tp1") and plan.get("early_tp_repair_v1")
            ):
                out["skipped"] += 1
                continue
        sym = plan.get("symbol") or key.split("|")[0]
        side = (plan.get("side") or "").lower()
        entry = plan.get("entry")
        init_raw = plan.get("initial_stop") or plan.get("stop")
        tp = plan.get("tp") or plan.get("tp1")
        if entry is None or init_raw is None:
            out["skipped"] += 1
            continue
        try:
            entry_d = Decimal(str(entry))
            init_d = Decimal(str(init_raw))
            risk = abs(entry_d - init_d)
            if risk <= 0:
                out["skipped"] += 1
                continue
            pos = by_sym.get(sym)
            mark = _mark(pos)
            if mark is None:
                out["skipped"] += 1
                continue
            if side == "long":
                mfe_r = float((mark - entry_d) / risk)
            else:
                mfe_r = float((entry_d - mark) / risk)
            if mfe_r < float(early_rr):
                out["skipped"] += 1
                continue
            # Far TP? if no tp or rr>=2.0 → scale out due
            far = True
            if tp is not None:
                try:
                    tp_d = Decimal(str(tp))
                    rr_tp = abs(tp_d - entry_d) / risk
                    far = rr_tp >= Decimal("1.5")
                except Exception:
                    far = True
            if not far and plan.get("early_tp1"):
                out["skipped"] += 1
                continue
            out["checked"] += 1
            exch_qty = Decimal(
                str(
                    getattr(pos, "quantity", None)
                    or getattr(pos, "qty", None)
                    or plan.get("qty")
                    or 0
                )
            )
            if exch_qty <= 0:
                out["skipped"] += 1
                continue
            frac = Decimal(str(plan.get("tp_frac") or "0.3"))
            if frac <= 0 or frac >= 1:
                frac = Decimal("0.3")
            q_tp = (exch_qty * frac).quantize(Decimal("0.00000001"))
            ps = "LONG" if side == "long" else "SHORT"
            stop_side = "SELL" if side == "long" else "BUY"
            try:
                await broker.client.place_order(
                    symbol=sym,
                    side=stop_side,
                    order_type="MARKET",
                    quantity=q_tp,
                    position_side=ps,
                    reduce_only=False,
                    close_position=False,
                )
                # cancel far TP legs
                try:
                    orders = await broker.client.get_open_orders(sym)
                    for o in orders or []:
                        ot = str(getattr(o, "type", "") or getattr(o, "order_type", "") or "").upper()
                        if "TAKE_PROFIT" in ot:
                            oid = getattr(o, "id", None) or getattr(o, "order_id", None)
                            if oid:
                                try:
                                    await broker.client.cancel_order(sym, str(oid))
                                except Exception:
                                    pass
                except Exception:
                    pass
                new_qty = exch_qty - q_tp
                if new_qty < 0:
                    new_qty = Decimal("0")
                plan["qty"] = str(new_qty)
                plan["early_scaled"] = True
                plan["early_tp1"] = True
                plan["early_tp_repair_v1"] = True
                runner_tp_ok = False
                try:
                    if new_qty > 0 and risk > 0:
                        if side == "long":
                            runner_tp = entry_d + risk * Decimal("1.5")
                        else:
                            runner_tp = entry_d - risk * Decimal("1.5")
                        await broker.client.place_order(
                            symbol=sym,
                            side=stop_side,
                            order_type="TAKE_PROFIT_MARKET",
                            quantity=new_qty,
                            stop_price=runner_tp,
                            position_side=ps,
                            reduce_only=False,
                            close_position=False,
                        )
                        plan["tp"] = str(runner_tp)
                        plan["runner_tp_rr"] = 1.5
                        runner_tp_ok = True
                        append_journal(
                            {
                                "event": "runner_tp_after_scale",
                                "symbol": sym,
                                "side": side,
                                "qty": str(new_qty),
                                "tp": str(runner_tp),
                                "rr": 1.5,
                                "source": "scale_out",
                                "ts": time.time(),
                            }
                        )
                except Exception as rtex:
                    append_journal(
                        {
                            "event": "runner_tp_after_scale_failed",
                            "symbol": sym,
                            "error": f"{type(rtex).__name__}: {rtex}",
                            "ts": time.time(),
                        }
                    )
                plans[key] = plan
                save_positions(plans)
                append_journal(
                    {
                        "event": "early_scale_out",
                        "symbol": sym,
                        "side": side,
                        "mfe_r": round(mfe_r, 4),
                        "qty": str(q_tp),
                        "qty_left": str(new_qty),
                        "mark": str(mark),
                        "runner_tp_ok": runner_tp_ok,
                        "ts": time.time(),
                    }
                )
                out["scaled"] += 1
            except Exception as exc:
                plan["early_scaled"] = True  # prevent spam; manual review
                plans[key] = plan
                save_positions(plans)
                append_journal(
                    {
                        "event": "early_scale_out_failed",
                        "symbol": sym,
                        "side": side,
                        "error": f"{type(exc).__name__}: {exc}",
                        "ts": time.time(),
                    }
                )
                if send_tg:
                    try:
                        await send_tg(f"⚠️ {sym}: early scale-out failed — {exc}")
                    except Exception:
                        pass
                out["failed"] += 1
        except Exception:
            out["skipped"] += 1
    return out


# F17: L3 latch be_earned from 5m extremes (owner 24.10)
_F17_5M_CACHE: dict[str, list] = {}


async def _f17_5m_bars(client, symbol: str, since_ms: int | None) -> list | None:
    """Public 5m klines for symbol since entry; tick-level cache. Fail-open → None."""
    global _F17_5M_CACHE
    if symbol in _F17_5M_CACHE:
        return _F17_5M_CACHE[symbol]
    try:
        get = getattr(client, "get_candles", None) or getattr(client, "get_recent_candles", None)
        if get is None:
            return None
        try:
            bars = await get(symbol, "5m", limit=500)
        except TypeError:
            try:
                bars = await get(symbol, "5m")
            except Exception:
                return None
        if not bars:
            return None
        out = []
        for b in bars:
            ot = int(getattr(b, "open_time", 0) or 0)
            # BingX open_time may be ms or s
            if ot < 10_000_000_000:
                ot_ms = ot * 1000
            else:
                ot_ms = ot
            if since_ms is not None and ot_ms + 300_000 < int(since_ms):
                # bar fully before entry — skip
                continue
            out.append(b)
        _F17_5M_CACHE[symbol] = out
        return out
    except Exception as exc:
        logger.warning("F17 5m bars %s failed: %s", symbol, type(exc).__name__)
        return None


def _f17_be_earned_from_bars(
    bars: list | None,
    *,
    side: str,
    net_be: Decimal,
) -> bool:
    """long: any high >= net_be; short: any low <= net_be."""
    if not bars:
        return False
    side_l = (side or "").lower()
    for b in bars:
        try:
            hi = Decimal(str(getattr(b, "high", 0) or 0))
            lo = Decimal(str(getattr(b, "low", 0) or 0))
        except Exception:
            continue
        if side_l == "long" and hi >= net_be:
            return True
        if side_l == "short" and lo <= net_be:
            return True
    return False


async def sync_partial_breakeven(
    broker,
    *,
    positions_path: Path | None = None,
    send_tg=None,
    trigger_r: float | None = None,
) -> dict:
    """P2: BE when mark reaches +trigger_r MFE (not TP1 fill). Net BE + only-tighten.

    Owner 19.10 / arena B2-B3: net_breakeven_price when available; never widen stop.
    One-shot via plan.be_moved + journal event=price_breakeven.

    F10: deferred BE catch-up — place entry-stop only when mark allows (long mark>=entry);
    else be_pending, no place_order; journal be_catchup_pending (dedup 1h).

    Owner 30.10: MFE in R uses initial_stop (canonical risk), not current stop —
    after any tighten, current-stop R inflated/deflated and BE never fired at 0.6–0.7R.
    Default trigger_r = ZEUS_BE_TRIGGER_R or 0.5 (aligned with early TP / BE-gate).
    """
    if trigger_r is None:
        try:
            trigger_r = float(os.environ.get("ZEUS_BE_TRIGGER_R", "0.5") or "0.5")
        except Exception:
            trigger_r = 0.5
        if trigger_r <= 0 or trigger_r > 3:
            trigger_r = 0.5
    from astra_bot.decision.live_arm_wiring import append_journal
    from astra_bot.decision.live_margin_budget import only_tighter_stop
    from astra_bot.decision.live_positions import load_positions, upsert_plan
    from astra_bot.decision.live_stop_telemetry import record_stop_change

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
        if plan.get("entry") is None or plan.get("stop") is None:
            skipped.append(sym)
            continue
        pos = by_sym.get(sym)
        if pos is None:
            continue
        try:
            exch_qty = Decimal(str(getattr(pos, "quantity", 0) or 0))
            entry_px = Decimal(str(plan["entry"]))
            stop_px = Decimal(str(plan["stop"]))
            side = str(plan.get("side") or "long").lower()
            plan_qty_raw = plan.get("plan_qty") or plan.get("qty") or plan.get("quantity")
            try:
                plan_qty = Decimal(str(plan_qty_raw)) if plan_qty_raw is not None else Decimal("0")
            except Exception:
                plan_qty = Decimal("0")
            if plan_qty <= 0:
                plan_qty = exch_qty

            # F10: deferred BE catch-up — only place stop at entry when mark allows
            # long: mark >= entry; short: mark <= entry. Else be_pending, no place_order.
            partial_gap = (
                plan_qty > 0
                and exch_qty > 0
                and exch_qty <= plan_qty * Decimal("0.65")
            )
            pending_path = Path("models/zeus_be_pending.json")
            try:
                pending = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.exists() else {}
            except Exception:
                pending = {}
            if not isinstance(pending, dict):
                pending = {}
            pkey = f"{sym}|{side}"
            is_pending = bool(pending.get(pkey) or plan.get("be_pending"))

            mark_for_be = (
                getattr(pos, "mark_price", None)
                or getattr(pos, "markPrice", None)
                or getattr(pos, "current_price", None)
                or getattr(pos, "price", None)
            )
            mark_px_be: Decimal | None = None
            if mark_for_be is not None:
                try:
                    mark_px_be = Decimal(str(mark_for_be))
                except Exception:
                    mark_px_be = None

            # F17 / F15: target for BE = net_breakeven when available, else entry
            net_be = entry_px
            be_mode = "gross"
            try:
                if hasattr(broker, "net_breakeven_price"):
                    _p = type("P", (), {
                        "entry_price": entry_px,
                        "side": side,
                        "quantity": exch_qty,
                        "symbol": sym,
                    })()
                    net_be = Decimal(str(broker.net_breakeven_price(_p)))
                    be_mode = "net"
            except Exception:
                net_be = entry_px
                be_mode = "gross"

            # F17 L3: latch be_earned from 5m extremes since entry (fail-open → mark only)
            be_earned = bool(plan.get("be_earned"))
            eligible_source = "mark"
            if not be_earned:
                since_ms = None
                for k in ("opened_at_ms", "entry_ts_ms", "ts_ms"):
                    if plan.get(k) is not None:
                        try:
                            since_ms = int(float(plan[k]))
                            if since_ms < 10_000_000_000:
                                since_ms *= 1000
                            break
                        except Exception:
                            pass
                if since_ms is None and plan.get("ts") is not None:
                    try:
                        since_ms = int(float(plan["ts"]) * 1000)
                    except Exception:
                        since_ms = None
                bars = None
                try:
                    bars = await _f17_5m_bars(broker.client, sym, since_ms)
                except Exception:
                    bars = None
                if _f17_be_earned_from_bars(bars, side=side, net_be=net_be):
                    be_earned = True
                    eligible_source = "5m"
                    plan["be_earned"] = True
                    try:
                        from astra_bot.decision.live_positions import save_positions
                        plans[_key] = plan
                        save_positions(plans, path)
                    except Exception:
                        pass
                    try:
                        append_journal({
                            "event": "be_earned_5m",
                            "symbol": sym,
                            "side": side,
                            "net_be": str(net_be),
                            "be_mode": be_mode,
                            "ts": time.time(),
                        })
                    except Exception:
                        pass
                elif mark_px_be is not None:
                    if (side == "long" and mark_px_be >= net_be) or (
                        side == "short" and mark_px_be <= net_be
                    ):
                        be_earned = True
                        eligible_source = "mark"
                        plan["be_earned"] = True

            # F10 exchange-validity: place only when mark is on the correct side of net_be
            mark_ok = False
            if mark_px_be is not None:
                try:
                    if side == "long":
                        mark_ok = mark_px_be >= net_be
                    else:
                        mark_ok = mark_px_be <= net_be
                except Exception:
                    mark_ok = False

            if (
                not plan.get("be_moved")
                and (partial_gap or is_pending)
            ):
                # L3 may have latched be_earned via 5m; still require mark_ok for place
                if not mark_ok or not be_earned:
                    # Defer: no place_order; journal once / 1h
                    if partial_gap or is_pending:
                        now = time.time()
                        last_j = float((pending.get(pkey) or {}).get("journal_ts") or 0)
                        pending[pkey] = {
                            "be_pending": True,
                            "exch_qty": str(exch_qty),
                            "plan_qty": str(plan_qty),
                            "entry": str(entry_px),
                            "mark": str(mark_px_be) if mark_px_be is not None else None,
                            "ts": now,
                            "journal_ts": last_j if now - last_j < 3600 else now,
                        }
                        try:
                            pending_path.write_text(
                                json.dumps(pending, ensure_ascii=False), encoding="utf-8"
                            )
                        except Exception:
                            pass
                        if now - last_j >= 3600 or last_j == 0:
                            try:
                                append_journal({
                                    "event": "be_catchup_pending",
                                    "symbol": sym,
                                    "side": side,
                                    "exch_qty": str(exch_qty),
                                    "plan_qty": str(plan_qty),
                                    "entry": str(entry_px),
                                    "mark": str(mark_px_be) if mark_px_be is not None else None,
                                    "reason": "mark_not_at_entry",
                                    "ts": now,
                                })
                            except Exception:
                                pass
                            pending[pkey]["journal_ts"] = now
                            try:
                                pending_path.write_text(
                                    json.dumps(pending, ensure_ascii=False), encoding="utf-8"
                                )
                            except Exception:
                                pass
                        logger.info(
                            "be_catchup_pending: %s mark=%s entry=%s (no place_order)",
                            sym, mark_px_be, entry_px,
                        )
                    continue

                # mark allows BE at entry — place once
                target = entry_px
                placed_ok = False
                try:
                    if only_tighter_stop(side, stop_px, target) is not None:
                        await broker.move_stop(sym, side, exch_qty, target)
                    placed_ok = True
                except Exception as move_exc:
                    logger.warning(
                        "be_catchup move_stop %s failed: %s: %s",
                        sym, type(move_exc).__name__, move_exc,
                    )
                    # tick continues; do NOT write be_catchup / be_moved
                    continue
                if not placed_ok:
                    continue
                ratio = float(exch_qty / plan_qty) if plan_qty > 0 else None
                upsert_plan(
                    sym,
                    side=side,
                    entry=str(entry_px),
                    stop=str(target),
                    qty=str(exch_qty),
                    path=path,
                    tp=plan.get("tp"),
                    tp1=plan.get("tp1"),
                    be_moved=True,
                    signal_id=plan.get("signal_id"),
                    plan_qty=str(plan_qty),
                    qty_ratio=ratio,
                )
                pending.pop(pkey, None)
                try:
                    pending_path.write_text(
                        json.dumps(pending, ensure_ascii=False), encoding="utf-8"
                    )
                except Exception:
                    pass
                try:
                    record_stop_change(
                        symbol=sym,
                        stop_price=str(target),
                        source="be_catchup",
                        reason="deferred_mark_ok",
                        side=side,
                    )
                except Exception:
                    pass
                try:
                    append_journal({
                        "event": "be_catchup",
                        "symbol": sym,
                        "side": side,
                        "exch_qty": str(exch_qty),
                        "plan_qty": str(plan_qty),
                        "qty_ratio": ratio,
                        "entry": str(entry_px),
                        "net_be": str(net_be),
                        "mark": str(mark_px_be) if mark_px_be is not None else None,
                        "stop_before": str(stop_px),
                        "stop_after": str(target),
                        "be_eligible_source": eligible_source if be_earned else "mark",
                        "be_mode": be_mode,
                        "ts": time.time(),
                    })
                except Exception:
                    pass
                try:
                    trades_path = Path("models/zeus_live_trades.jsonl")
                    fill_qty = plan_qty - exch_qty
                    row = {
                        "event": "partial_fill",
                        "symbol": sym,
                        "side": side,
                        "qty": str(fill_qty),
                        "exch_qty": str(exch_qty),
                        "plan_qty": str(plan_qty),
                        "price": None,
                        "ts": time.time(),
                        "source": "be_catchup_qty_gap",
                    }
                    with trades_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(row, ensure_ascii=False) + chr(10))
                except Exception:
                    pass
                moved.append(sym)
                logger.info(
                    "be_catchup: %s exch=%s plan=%s stop→entry %s mark=%s",
                    sym, exch_qty, plan_qty, target, mark_px_be,
                )
                continue

            # F7.4: LiveBingX Position stores mark in current_price (client_live get_positions)
            mark = (
                getattr(pos, "mark_price", None)
                or getattr(pos, "markPrice", None)
                or getattr(pos, "current_price", None)
                or getattr(pos, "price", None)
            )
            if mark is None:
                logger.warning("sync_partial_breakeven: %s no mark (attrs checked)", sym)
                skipped.append(sym)
                continue
            mark_px = Decimal(str(mark))
            # Canonical risk from initial_stop (F15b); fallback current stop
            try:
                init_raw = plan.get("initial_stop")
                init_px = Decimal(str(init_raw)) if init_raw is not None else stop_px
            except Exception:
                init_px = stop_px
            r_abs = abs(entry_px - init_px)
            if r_abs <= 0:
                r_abs = abs(entry_px - stop_px)
            if r_abs <= 0 or exch_qty <= 0:
                continue
            side = str(plan.get("side") or "long").lower()
            if side == "long":
                mfe_r = float((mark_px - entry_px) / r_abs)
            else:
                mfe_r = float((entry_px - mark_px) / r_abs)
            try:
                append_journal({
                    "event": "be_mark_tick",
                    "symbol": sym,
                    "side": side,
                    "mark": str(mark_px),
                    "entry": str(entry_px),
                    "stop": str(stop_px),
                    "initial_stop": str(init_px),
                    "mfe_r": round(mfe_r, 4),
                    "trigger_r": float(trigger_r),
                    "be_moved": bool(plan.get("be_moved")),
                    "ts": time.time(),
                })
            except Exception:
                pass
            if mfe_r < float(trigger_r):
                continue

            # B2: net BE (paper parity). Missing method → LOUD gross fallback.
            be_px = entry_px
            used_gross_fallback = False
            if hasattr(broker, "net_breakeven_price"):
                try:
                    class _P:
                        pass
                    _p = _P()
                    _p.direction = side
                    _p.entry_price = entry_px
                    _p.fill_price = entry_px
                    _p.quantity = exch_qty
                    _p.entry_fee_per_unit = Decimal("0")
                    _p.bars_held = 0
                    be_px = Decimal(str(broker.net_breakeven_price(_p)))
                except Exception as _nb_exc:
                    used_gross_fallback = True
                    be_px = entry_px
                    logger.warning(
                        "be_gross_fallback %s: net_breakeven_price failed (%s) — using entry",
                        sym, type(_nb_exc).__name__,
                    )
            else:
                used_gross_fallback = True
                logger.warning(
                    "be_gross_fallback %s: broker has no net_breakeven_price — using entry (gross)",
                    sym,
                )

            # B3: only tighten
            target = only_tighter_stop(side, stop_px, be_px)
            if target is None:
                skipped.append(sym)
                continue

            await broker.move_stop(sym, side, exch_qty, target)
            upsert_plan(
                symbol=sym,
                side=side,
                entry=entry_px,
                stop=target,
                qty=exch_qty,
                path=path,
                tp=plan.get("tp"),
                tp1=plan.get("tp1"),
                be_moved=True,
                signal_id=plan.get("signal_id"),
            )
            moved.append(sym)
            try:
                record_stop_change(
                    symbol=sym,
                    stop_price=str(target),
                    source="price_breakeven",
                    reason=f"mfe_r>={trigger_r}",
                    side=side,
                )
            except Exception:
                pass
            try:
                append_journal(
                    {
                        "event": "price_breakeven",
                        "be_gross_fallback": used_gross_fallback,
                        "be_mode": ("gross" if used_gross_fallback else "net"),
                        "symbol": sym,
                        "side": side,
                        "entry": str(entry_px),
                        "stop": str(target),
                        "stop_before": str(stop_px),
                        "mark": str(mark_px),
                        "mfe_r": round(mfe_r, 4),
                        "trigger_r": trigger_r,
                        "qty": str(exch_qty),
                        "ts": time.time(),
                    }
                )
            except Exception as jexc:
                logger.warning("sync_partial journal: %s", type(jexc).__name__)
            if used_gross_fallback:
                try:
                    append_journal(
                        {
                            "event": "be_gross_fallback",
                            "symbol": sym,
                            "side": side,
                            "entry": str(entry_px),
                            "be_px": str(be_px),
                            "ts": time.time(),
                        }
                    )
                except Exception:
                    pass
            logger.info(
                "price_breakeven: %s mfe_r=%.3f stop %s→%s",
                sym, mfe_r, stop_px, target,
            )
        except Exception as exc:
            logger.warning(
                "sync_partial_breakeven: %s failed: %s: %s",
                sym, type(exc).__name__, exc,
            )
            skipped.append(sym)

    return {"moved": moved, "skipped": skipped}




async def heal_dead_tp_legs(broker, *, positions_path: Path | None = None) -> dict:
    """F7.3a: TP leg NEW but mark crossed trigger 2 ticks → replace-first then cancel old.

    Never leave position naked: place new TP at same level, code=0, then cancel old.
    """
    from astra_bot.decision.live_arm_wiring import append_journal
    from astra_bot.decision.live_positions import load_positions

    path = positions_path or Path("models/zeus_live_positions.json")
    state_path = Path("models/zeus_tp_heal_state.json")
    healed: list[str] = []
    watched: list[str] = []
    try:
        plans = load_positions(path)
        positions = await broker.client.get_positions()
    except Exception as exc:
        logger.warning("heal_dead_tp_legs setup failed: %s", type(exc).__name__)
        return {"healed": [], "error": type(exc).__name__}

    by_sym: dict[str, object] = {}
    for p in positions:
        sym = getattr(p, "symbol", "") or ""
        qty = getattr(p, "quantity", None) or Decimal("0")
        if sym and qty and qty > 0:
            by_sym[sym] = p

    try:
        st = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except Exception:
        st = {}
    if not isinstance(st, dict):
        st = {}

    for _key, plan in list(plans.items()):
        sym = str(plan.get("symbol") or _key.split("|")[0] or "")
        if not sym or sym not in by_sym:
            continue
        pos = by_sym[sym]
        mark = (
            getattr(pos, "mark_price", None)
            or getattr(pos, "markPrice", None)
            or getattr(pos, "current_price", None)
            or getattr(pos, "price", None)
        )
        if mark is None:
            continue
        mark_px = Decimal(str(mark))
        side = str(plan.get("side") or "long").lower()
        tp_level = plan.get("tp1") or plan.get("tp") or plan.get("take_profit_price")
        if tp_level is None:
            continue
        tp_px = Decimal(str(tp_level))
        try:
            orders = await broker.client.get_open_orders(sym)
        except Exception as exc:
            logger.warning("heal_dead_tp: get_open_orders %s: %s", sym, type(exc).__name__)
            continue
        tp_orders = []
        for o in orders or []:
            ot = str(getattr(o, "order_type", "") or "").upper()
            if "TAKE_PROFIT" not in ot:
                continue
            st_o = str(getattr(o, "status_raw", None) or getattr(o, "status", None) or "").upper()
            if st_o and st_o not in ("NEW", "PENDING", ""):
                continue
            stop_p = getattr(o, "stop_price", None) or getattr(o, "price", None)
            if stop_p is None:
                continue
            tp_orders.append(o)
        if not tp_orders:
            continue
        # crossed?
        if side == "short":
            crossed = mark_px <= tp_px
        else:
            crossed = mark_px >= tp_px
        key = f"{sym}|{side}"
        if not crossed:
            st.pop(key, None)
            continue
        ticks = int(st.get(key, {}).get("ticks", 0)) + 1
        st[key] = {"ticks": ticks, "tp": str(tp_px), "mark": str(mark_px), "ts": time.time()}
        watched.append(sym)
        if ticks < 2:
            logger.info("heal_dead_tp: %s crossed tick %s/2 mark=%s tp=%s", sym, ticks, mark_px, tp_px)
            continue
        # replace-first
        old = tp_orders[0]
        old_id = getattr(old, "id", None) or getattr(old, "exchange_order_id", None)
        qty = getattr(old, "quantity", None)
        if qty is None or Decimal(str(qty)) <= 0:
            # 40% of position
            qty = (Decimal(str(getattr(pos, "quantity", 0))) * Decimal("0.4")).quantize(Decimal("0.00000001"))
        stop_side = "BUY" if side == "short" else "SELL"
        ps = "SHORT" if side == "short" else "LONG"
        try:
            new_o = await broker.client.place_order(
                symbol=sym,
                side=stop_side,
                order_type="TAKE_PROFIT_MARKET",
                quantity=Decimal(str(qty)),
                stop_price=tp_px,
                position_side=ps,
                reduce_only=False,
                close_position=False,
                working_type="MARK_PRICE",
            )
            append_journal({
                "event": "tp_heal_place",
                "symbol": sym,
                "side": side,
                "stop_price": str(tp_px),
                "qty": str(qty),
                "mark": str(mark_px),
                "old_id": str(old_id or ""),
                "new_id": str(getattr(new_o, "id", None) or ""),
                "ts": time.time(),
            })
            # cancel old only after success
            if old_id and hasattr(broker.client, "cancel_order"):
                try:
                    await broker.client.cancel_order(sym, str(old_id))
                    try:
                        from astra_bot.decision.live_broker import journal_order_cancel
                        journal_order_cancel(
                            symbol=sym,
                            order_id=str(old_id),
                            order_type="TAKE_PROFIT",
                            reason="tp_heal_replace",
                            path="tp_heal",
                            side=side,
                        )
                    except Exception:
                        pass
                    append_journal({
                        "event": "tp_heal_cancel_old",
                        "symbol": sym,
                        "old_id": str(old_id),
                        "ts": time.time(),
                    })
                except Exception as cexc:
                    logger.warning("heal_dead_tp cancel old %s: %s", sym, cexc)
                    append_journal({
                        "event": "tp_heal_cancel_failed",
                        "symbol": sym,
                        "old_id": str(old_id),
                        "error": f"{type(cexc).__name__}: {cexc}",
                        "ts": time.time(),
                    })
            healed.append(sym)
            st.pop(key, None)
            logger.info("heal_dead_tp: %s replaced TP @ %s (mark=%s)", sym, tp_px, mark_px)
        except Exception as exc:
            logger.warning("heal_dead_tp place %s failed: %s: %s", sym, type(exc).__name__, exc)
            append_journal({
                "event": "tp_heal_place_failed",
                "symbol": sym,
                "error": f"{type(exc).__name__}: {exc}",
                "ts": time.time(),
            })

    try:
        state_path.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return {"healed": healed, "watched": watched}



async def detect_position_missing_with_orders(broker, *, positions_path: Path | None = None) -> dict:
    """F9.2: plan has protective orders but get_positions has no row — journal only.

    No trading action on guesswork. Dedup 1h via models/zeus_pos_missing_warn.json.
    """
    from astra_bot.decision.live_arm_wiring import append_journal
    from astra_bot.decision.live_positions import load_positions

    path = positions_path or Path("models/zeus_live_positions.json")
    flag = Path("models/zeus_pos_missing_warn.json")
    missing: list[str] = []
    try:
        plans = load_positions(path)
        positions = await broker.client.get_positions()
    except Exception as exc:
        logger.warning("detect_position_missing: setup %s", type(exc).__name__)
        return {"missing": [], "error": type(exc).__name__}

    by_sym = {getattr(p, "symbol", "") for p in positions if getattr(p, "symbol", "")}
    try:
        st = json.loads(flag.read_text(encoding="utf-8")) if flag.exists() else {}
    except Exception:
        st = {}
    if not isinstance(st, dict):
        st = {}
    now = time.time()

    for _key, plan in list(plans.items()):
        sym = str(plan.get("symbol") or _key.split("|")[0] or "")
        if not sym or sym in by_sym:
            continue
        try:
            orders = await broker.client.get_open_orders(sym)
        except Exception:
            continue
        n_prot = 0
        for o in orders or []:
            ot = str(getattr(o, "order_type", "") or "").upper()
            if "STOP" in ot or "TAKE_PROFIT" in ot:
                n_prot += 1
        if n_prot <= 0:
            continue
        last = float(st.get(sym) or 0)
        if now - last < 3600:
            missing.append(sym)
            continue
        try:
            append_journal({
                "event": "position_missing_with_open_orders",
                "symbol": sym,
                "side": plan.get("side"),
                "n_protect_orders": n_prot,
                "ts": now,
            })
        except Exception:
            pass
        st[sym] = now
        missing.append(sym)
        logger.warning(
            "position_missing_with_open_orders: %s n_orders=%s (no TG)",
            sym, n_prot,
        )
    try:
        flag.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return {"missing": missing}


async def detect_ghost_positions(
    broker,
    *,
    positions_path: Path | None = None,
    send_tg=None,
) -> dict:
    """F23: exchange position without a live plan → journal + TG warn.

    Unknown protection state. Dedup 1h via models/zeus_ghost_position_warn.json.
    No trading action (no auto-close) — owner decides.
    """
    from astra_bot.decision.live_arm_wiring import append_journal
    from astra_bot.decision.live_positions import load_positions

    path = positions_path or Path("models/zeus_live_positions.json")
    flag = Path("models/zeus_ghost_position_warn.json")
    ghosts: list[dict] = []
    try:
        plans = load_positions(path)
        positions = await broker.client.get_positions()
    except Exception as exc:
        logger.warning("detect_ghost_positions: setup %s", type(exc).__name__)
        return {"ghosts": [], "error": type(exc).__name__}

    plan_syms: set[str] = set()
    for _key, plan in (plans or {}).items():
        if not isinstance(plan, dict):
            continue
        sym = str(plan.get("symbol") or _key.split("|")[0] or "")
        if sym:
            plan_syms.add(sym)

    try:
        st = json.loads(flag.read_text(encoding="utf-8")) if flag.exists() else {}
    except Exception:
        st = {}
    if not isinstance(st, dict):
        st = {}
    now = time.time()
    tg = send_tg

    for pos in positions or []:
        sym = str(getattr(pos, "symbol", "") or "")
        qty = getattr(pos, "quantity", None)
        try:
            qty_d = float(qty) if qty is not None else 0.0
        except (TypeError, ValueError):
            qty_d = 0.0
        if not sym or qty_d <= 0:
            continue
        if sym in plan_syms:
            continue
        entry = getattr(pos, "entry_price", None) or getattr(pos, "avg_price", None)
        side = str(getattr(pos, "side", "") or "").lower()
        last = float(st.get(sym) or 0)
        row = {
            "symbol": sym,
            "side": side,
            "qty": str(qty),
            "entry": str(entry) if entry is not None else None,
        }
        ghosts.append(row)
        if now - last < 3600:
            continue
        try:
            append_journal({
                "event": "ghost_position",
                "symbol": sym,
                "side": side,
                "qty": str(qty),
                "entry": str(entry) if entry is not None else None,
                "ts": now,
            })
        except Exception:
            pass
        st[sym] = now
        msg = f"⚠️ {sym}: позиция без плана (qty={qty}"
        if entry is not None:
            msg += f", entry={entry}"
        msg += ") — защита неизвестна"
        if tg is not None:
            try:
                await tg(msg)
            except Exception as texc:
                logger.warning("ghost_position TG %s: %s", sym, type(texc).__name__)
        logger.warning("ghost_position: %s qty=%s side=%s", sym, qty, side)

    try:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return {"ghosts": ghosts}



DIGEST_PATH = Path("models/zeus_live_digest.json")
TRADES_PATH = Path("models/zeus_live_trades.jsonl")


async def _resolve_pending_exit_sums(client, send_tg=None) -> int:
    """Follow-up rockets: dogon confirm (≥0.5·est) or 24h estimate. Fail-open."""
    from astra_bot.decision.live_income_report import resolve_pending_dogon

    tg = send_tg or _send_live_tg
    return await resolve_pending_dogon(client, tg)


async def _accumulate_funding_day(client) -> float:
    """Poll FUNDING_FEE on open plans; dedup; accumulate MSK-day sum. No TG.

    State: models/zeus_funding_day.json = {"date": "YYYY-MM-DD", "sum": float, "ts": ...}
    New MSK day resets sum. Fail-open. Returns current day sum after update.
    """
    from astra_bot.decision.live_income_report import claim_funding_event
    from astra_bot.decision.live_positions import load_positions

    FUNDING_DAY_PATH = Path("models/zeus_funding_day.json")

    def _load_day() -> dict:
        try:
            if not FUNDING_DAY_PATH.exists():
                return {}
            raw = json.loads(FUNDING_DAY_PATH.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _save_day(date_s: str, total: float) -> None:
        try:
            FUNDING_DAY_PATH.parent.mkdir(parents=True, exist_ok=True)
            FUNDING_DAY_PATH.write_text(
                json.dumps({"date": date_s, "sum": float(total), "ts": time.time()}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("funding_day save: %s", type(exc).__name__)

    try:
        today = _msk_now().strftime("%Y-%m-%d")
    except Exception:
        from datetime import datetime, timedelta, timezone
        today = (datetime.now(timezone(timedelta(hours=3)))).strftime("%Y-%m-%d")

    st = _load_day()
    if str(st.get("date") or "") != today:
        total = 0.0
    else:
        try:
            total = float(st.get("sum") or 0)
        except (TypeError, ValueError):
            total = 0.0

    plans = {}
    try:
        plans = load_positions() or {}
    except Exception as exc:
        logger.warning("funding_day load_positions: %s", type(exc).__name__)
        _save_day(today, total)
        return total

    import time as _time
    now_ms = int(_time.time() * 1000)
    start_ms = now_ms - 36 * 3600 * 1000

    for _key, plan in list(plans.items()):
        sym = str(plan.get("symbol") or "")
        if not sym or not hasattr(client, "get_realized_income"):
            continue
        try:
            rows = await client.get_realized_income(sym, start_ms=start_ms)
        except Exception as exc:
            logger.warning("funding_day poll %s: %s", sym, type(exc).__name__)
            continue
        for row in rows or []:
            it = str(row.get("_incomeType") or row.get("incomeType") or "")
            if it != "FUNDING_FEE":
                continue
            try:
                amount = float(row.get("income") or 0)
            except (TypeError, ValueError):
                continue
            tms = row.get("time") or row.get("timestamp") or row.get("ts")
            try:
                tms_i = int(float(tms))
                if tms_i < 1e12:
                    tms_i = int(tms_i * 1000)
            except (TypeError, ValueError):
                tms_i = now_ms
            try:
                if not claim_funding_event(sym, tms_i, amount):
                    continue
            except Exception:
                continue
            total += amount

    _save_day(today, total)
    return total


def _day_funding_sum(trades_path: Path, now_ts: float | None = None) -> float:
    """Sum funding_fee fields on exit rows for current MSK day (digest line)."""
    dt = _msk_now(now_ts)
    day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    start_ts = day_start.timestamp()
    total = 0.0
    if not trades_path.exists():
        return total
    try:
        for line in trades_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if str(row.get("event") or "") != "exit":
                continue
            try:
                ts = float(row.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            if ts < start_ts:
                continue
            try:
                total += float(row.get("funding_fee") or 0)
            except (TypeError, ValueError):
                continue
    except Exception:
        return total
    return total


def _msk_now(now_ts: float | None = None):
    """Return timezone-aware datetime in Europe/Moscow."""
    from datetime import datetime, timezone
    try:
        from zoneinfo import ZoneInfo
        msk = ZoneInfo("Europe/Moscow")
    except Exception:
        from datetime import timedelta
        msk = timezone(timedelta(hours=3))  # fallback fixed +3
    if now_ts is None:
        return datetime.now(msk)
    return datetime.fromtimestamp(now_ts, tz=msk)


def _digest_window_key(now_ts: float | None = None) -> str | None:
    """Return dedup key for current MSK window, or None if outside windows.

    Morning 08:55–09:15 → YYYY-MM-DD:am
    Evening 20:55–21:15 → YYYY-MM-DD:pm
    """
    dt = _msk_now(now_ts)
    hm = dt.hour * 60 + dt.minute
    day = dt.strftime("%Y-%m-%d")
    if 8 * 60 + 55 <= hm <= 9 * 60 + 15:
        return f"{day}:am"
    if 20 * 60 + 55 <= hm <= 21 * 60 + 15:
        return f"{day}:pm"
    return None


def _load_digest_last(path: Path = DIGEST_PATH) -> str | None:
    try:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return str(raw.get("last") or "") or None
    except Exception:
        return None


def _save_digest_last(key: str, path: Path = DIGEST_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"last": key, "ts": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )


def _day_exits_msk(trades_path: Path, now_ts: float | None = None) -> list[dict]:
    """Exit rows from trades.jsonl with ts >= 00:00 current MSK day.

    F22-1: include event=exit AND income-sourced closes (pnl_source income*),
    so TG rockets with income PnL enter the day sum even if event tag drifts.
    """
    dt = _msk_now(now_ts)
    day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    start_ts = day_start.timestamp()
    out: list[dict] = []
    if not trades_path.exists():
        return out
    _INCOME_SRC = (
        "income",
        "income_backfill",
        "income_confirmed",
        "income_corrected",
        "pending_dogon",
    )
    try:
        for line in trades_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            ev = str(row.get("event") or "")
            src = str(row.get("pnl_source") or "")
            is_exit = ev == "exit"
            is_income = src in _INCOME_SRC and row.get("pnl") is not None
            if not is_exit and not is_income:
                continue
            # skip pure entry/partial without close semantics
            if ev in ("entry", "partial_fill") and not is_exit:
                continue
            try:
                ts = float(row.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            if ts < start_ts:
                continue
            if ts > 1e12:
                ts = ts / 1000.0
                if ts < start_ts:
                    continue
            out.append(row)
    except Exception:
        return out
    return out


async def _maybe_send_digest(
    broker,
    client,
    *,
    now_ts: float | None = None,
    digest_path: Path = DIGEST_PATH,
    trades_path: Path = TRADES_PATH,
    send_tg=None,
) -> str | None:
    """LIVE TG digest at 09:00 / 21:00 MSK windows. Telemetry only; fail-open.

    Returns sent key on success, None if skipped/failed.
    """
    try:
        from astra_bot.decision.live_arm_wiring import append_journal
        from astra_bot.decision.live_positions import load_positions

        key = _digest_window_key(now_ts)
        if key is None:
            return None
        if _load_digest_last(digest_path) == key:
            return None

        try:
            eq = await client.get_balance_usdt()
            write_live_equity(float(eq) if eq is not None else None)
        except Exception:
            eq = Decimal("0")
        eq_f = float(eq)

        exits = _day_exits_msk(trades_path, now_ts)
        pnls: list[float] = []
        no_fill = 0
        for row in exits:
            if row.get("pnl") is None:
                no_fill += 1
                continue
            try:
                pnls.append(float(row["pnl"]))
            except (TypeError, ValueError):
                no_fill += 1
        day_sum = sum(pnls) if pnls else 0.0
        n_pos = sum(1 for p in pnls if p > 0)
        n_neg = sum(1 for p in pnls if p < 0)
        best = max(pnls) if pnls else None
        worst = min(pnls) if pnls else None

        open_n = 0
        risk_sum = 0.0
        try:
            plans = load_positions()
            open_n = len(plans)
            for plan in plans.values():
                try:
                    entry = float(plan.get("entry") or 0)
                    stop = float(plan.get("stop") or 0)
                    qty = float(plan.get("qty") or 0)
                    risk_sum += abs(entry - stop) * qty
                except (TypeError, ValueError):
                    continue
        except Exception:
            pass

        halt_reason = getattr(broker, "_halt_reason", None) or ""
        halt_txt = f"да: {halt_reason}" if halt_reason else "нет"

        dt = _msk_now(now_ts)
        when = "утро" if key.endswith(":am") else "вечер"
        day_lbl = dt.strftime("%d.%m")
        sign = "+" if day_sum >= 0 else "−"
        msg = (
            f"📊 LIVE-СВОД {day_lbl} ({when}): "
            f"счёт {eq_f:.2f}$; "
            f"день {sign}{abs(day_sum):.2f}$ ({n_pos}+/{n_neg}−)"
        )
        if best is not None:
            msg += f"; лучший +{best:.2f}$" if best >= 0 else f"; лучший −{abs(best):.2f}$"
        if worst is not None:
            msg += f", худший +{worst:.2f}$" if worst >= 0 else f", худший −{abs(worst):.2f}$"
        msg += f"; открыто {open_n} (риск {risk_sum:.0f}$); HALT: {halt_txt}"
        try:
            fund_day = None
            fday_path = Path("models/zeus_funding_day.json")
            try:
                if fday_path.exists():
                    raw_fd = json.loads(fday_path.read_text(encoding="utf-8"))
                    today = _msk_now(now_ts).strftime("%Y-%m-%d")
                    if str(raw_fd.get("date") or "") == today:
                        fund_day = float(raw_fd.get("sum") or 0)
            except Exception:
                fund_day = None
            if fund_day is None:
                fund_day = _day_funding_sum(trades_path, now_ts)
            if abs(fund_day) >= 0.005:
                fund_s = f"+{fund_day:.2f}$" if fund_day > 0 else f"-{abs(fund_day):.2f}$"
                msg += f"; funding {fund_s}"
            else:
                msg += "; funding 0.00$"
        except Exception:
            pass
        if no_fill:
            msg += f"; +{no_fill} без филлов"

        # F22-2: claim key BEFORE TG so a second tick in the same window cannot double-send
        if _load_digest_last(digest_path) == key:
            return None
        _save_digest_last(key, digest_path)
        tg = send_tg or _send_live_tg
        try:
            await tg(msg)
        except Exception:
            # keep claim (prefer miss over duplicate spam); log only
            logger.warning("live_digest TG failed after claim key=%s", key)
            raise
        try:
            append_journal(
                {
                    "event": "live_digest",
                    "key": key,
                    "day_pnl": round(day_sum, 4),
                    "n_pos": n_pos,
                    "n_neg": n_neg,
                    "open_n": open_n,
                    "risk_sum": round(risk_sum, 2),
                    "equity": eq_f,
                    "halt": halt_reason or None,
                    "ts": time.time(),
                }
            )
        except Exception:
            pass
        logger.info("live_digest sent key=%s day_pnl=%.2f", key, day_sum)
        return key
    except Exception as exp:
        logger.warning("live_digest FAILED: %s", type(exp).__name__)
        return None



async def one_tick(*, dry: bool = False) -> int:
    _guard()
    from astra_bot.adapters.bingx.client_live import LiveBingXClient
    from astra_bot.decision.live_broker import LiveBroker, LiveBrokerConfig
    from astra_bot.decision.live_commands import apply_pending

    client = LiveBingXClient({})
    await client.initialize()

    _max_lev = 10
    try:
        _max_lev = int((os.environ.get("ZEUS_LIVE_MAX_LEVERAGE") or "10").strip() or "10")
    except ValueError:
        _max_lev = 10
    _max_lev = max(1, min(_max_lev, 10))  # A7 ceiling 10
    broker = LiveBroker(
        client=client,
        config=LiveBrokerConfig(max_leverage=_max_lev),
    )

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

    cmd_lines = await apply_pending(broker, send_tg=_send_live_tg)
    for ln in cmd_lines:
        logger.info("%s", ln)

    if Path("models/zeus_live_debug.json").exists():
        from astra_bot.adapters.bingx.vst_diag import dump as _diag

        await _diag(client, logger)

    rec = await broker.reconcile()
    # F24c R4: never write HARD_CAP-clamped broker.equity() (100) into TG state —
    # wallet from get_balance_usdt only (sizing already uses uncapped path).
    try:
        _eq = await client.get_balance_usdt()
        write_live_equity(float(_eq) if _eq is not None else None)
    except Exception:
        pass
    logger.info(
        "reconcile equity=%s positions=%s orders=%s missing_stops=%s",
        rec.get("equity"),
        rec.get("positions"),
        rec.get("open_orders"),
        rec.get("missing_stops"),
    )

    # F13: plan.qty ↔ exchange qty (>0.5% → upsert + qty_reconcile)
    try:
        rq = await broker.reconcile_plan_qty()
        if rq.get("reconciled"):
            logger.info("reconcile_plan_qty: %s", rq)
    except Exception as _rq_exc:
        logger.warning("reconcile_plan_qty failed: %s", type(_rq_exc).__name__)

    # F13: income fill-tape (dedup by income id)
    try:
        from astra_bot.decision.live_income_report import process_income_fill_tape
        from astra_bot.decision.live_positions import load_positions as _lp_f13
        _syms = sorted({
            str(p.get("symbol") or k.split("|")[0])
            for k, p in (_lp_f13() or {}).items()
            if isinstance(p, dict)
        })
        if _syms:
            _ft = await process_income_fill_tape(client, _syms)
            if _ft.get("written"):
                logger.info("income_fill_tape: %s", _ft)
    except Exception as _ft_exc:
        logger.warning("income_fill_tape failed: %s", type(_ft_exc).__name__)

    try:
        try:
            try:
                _mig = migrate_initial_stop_v1()
                if _mig.get("migrated"):
                    logger.info("migrate_initial_stop_v1: %s", _mig)
            except Exception as _mexc:
                logger.warning("migrate_initial_stop_v1: %s", type(_mexc).__name__)
            _par = await apply_tp_parity_v1(broker, send_tg=_send_live_tg)
            if _par.get("replaced") or _par.get("failed"):
                logger.info("tp_parity_v1: %s", _par)
            _rep = await repair_early_tp_after_parity(broker, send_tg=_send_live_tg)
            if _rep.get("repaired") or _rep.get("failed") or _rep.get("past_level"):
                logger.info("early_tp_repair: %s", _rep)
            try:
                _heal = await heal_ratchet_and_runner_tp(broker, send_tg=_send_live_tg)
                if any(_heal.get(k) for k in ("ratchet", "runner_tp", "dead_flat")):
                    logger.info("heal_ratchet_runner: %s", _heal)
            except Exception as _he:
                logger.warning("heal_ratchet_runner failed: %s", type(_he).__name__)
            _sc = await scale_out_early_if_due(broker, send_tg=_send_live_tg)
            if _sc.get("scaled") or _sc.get("failed"):
                logger.info("early_scale_out: %s", _sc)
        except Exception as _p_exc:
            logger.warning("tp_parity_v1 failed: %s", type(_p_exc).__name__)
        _set_margin_warn_tg(_send_live_tg)
        rest = await broker.restore_missing_stops(send_tg=_send_live_tg)
        if rest.get("restored") or rest.get("closed_naked"):
            logger.info("restore_missing_stops: %s", rest)
    except Exception as exc:
        logger.warning("restore_missing_stops failed: %s", type(exc).__name__)

    # Owner 20.10 §4: no live STOP → place; fail → BE@0.8R or market-close THIS symbol
    try:
        _be_tr = 0.5
        try:
            import os as _os_be
            _be_tr = float((_os_be.environ.get("ZEUS_BE_TRIGGER_R") or "0.5").strip() or "0.5")
        except Exception:
            _be_tr = 0.5
        ens = await broker.ensure_protection(send_tg=_send_live_tg, trigger_be_r=_be_tr)
        if ens.get("placed") or ens.get("closed"):
            logger.info("ensure_protection: %s", ens)
    except Exception as exc:
        logger.warning("ensure_protection failed: %s", type(exc).__name__)

    # Telemetry: order_audit every tick — counts + price/qty/flags (P5 owner 19.10)
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
                detail = []
                n_stop = n_tp = 0
                for o in orders:
                    ot = str(getattr(o, "order_type", "") or "").upper()
                    is_stop = "STOP" in ot and "TAKE_PROFIT" not in ot
                    is_tp = "TAKE_PROFIT" in ot
                    if is_stop:
                        n_stop += 1
                    if is_tp:
                        n_tp += 1
                    if is_stop or is_tp:
                        detail.append({
                            "type": ot,
                            "price": str(
                                getattr(o, "stop_price", None)
                                or getattr(o, "price", None)
                                or ""
                            ),
                            "stopPrice": str(getattr(o, "stop_price", None) or ""),
                            "status": str(
                                getattr(o, "status_raw", None)
                                or getattr(o, "status", None)
                                or ""
                            ),
                            "workingType": str(
                                getattr(o, "working_type", None)
                                or getattr(o, "workingType", None)
                                or ""
                            ),
                            "qty": str(
                                getattr(o, "quantity", None)
                                or getattr(o, "origQty", None)
                                or ""
                            ),
                            "reduceOnly": (
                                getattr(o, "reduce_only", None)
                                if getattr(o, "reduce_only", None) is not None
                                else getattr(o, "reduceOnly", None)
                            ),
                            "closePosition": (
                                getattr(o, "close_position", None)
                                if getattr(o, "close_position", None) is not None
                                else getattr(o, "closePosition", None)
                            ),
                            "raw_keys": list(getattr(o, "_raw_order_keys", []) or [])[:24],
                        })
                append_journal(
                    {
                        "event": "order_audit",
                        "symbol": sym,
                        "side": plan.get("side"),
                        "n_stop": n_stop,
                        "n_tp": n_tp,
                        "be_moved": bool(plan.get("be_moved")),
                        "orders": detail,
                        "ts": time.time(),
                    }
                )
    except Exception as _oa_exc:
        logger.warning("order_audit FAILED: %s", type(_oa_exc).__name__)

    # F7.3a: dead TP leg auto-heal (replace-first, never naked)
    try:
        heal = await heal_dead_tp_legs(broker)
        if heal.get("healed") or heal.get("watched"):
            logger.info("heal_dead_tp_legs: %s", heal)
    except Exception as _heal_exc:
        logger.warning("heal_dead_tp_legs failed: %s: %s", type(_heal_exc).__name__, _heal_exc)

    # F9.2: protective orders without position row — journal only
    try:
        miss = await detect_position_missing_with_orders(broker)
        if miss.get("missing"):
            logger.info("position_missing_with_open_orders: %s", miss)
    except Exception as _miss_exc:
        logger.warning("detect_position_missing failed: %s", type(_miss_exc).__name__)

    # F23: exchange position without plan → ghost_position + TG warn
    try:
        ghosts = await detect_ghost_positions(broker, send_tg=_send_live_tg)
        if ghosts.get("ghosts"):
            logger.info("ghost_positions: %s", ghosts)
    except Exception as _ghost_exc:
        logger.warning("detect_ghost_positions failed: %s", type(_ghost_exc).__name__)

    # C: partial TP1 → BE — EVERY tick (owner rule), after restore so stop is present
    try:
        _F17_5M_CACHE.clear()
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

    try:
        n_pend = await _resolve_pending_exit_sums(client, send_tg=_send_live_tg)
        if n_pend:
            logger.info("pending exit sums sent: %d", n_pend)
    except Exception as _pexc:
        logger.warning("pending exit sums FAILED: %s", type(_pexc).__name__)

    try:
        fund_sum = await _accumulate_funding_day(client)
        logger.info("funding_day sum=%.4f", float(fund_sum or 0))
    except Exception as _fexc:
        logger.warning("funding_day FAILED: %s", type(_fexc).__name__)

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
            write_live_equity(float(eq) if eq is not None else None)
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

    # PR-B exit engine (shadow default) — after kill/risk, before arm
    try:
        from astra_bot.decision.live_exit_engine import engine_mode, run_exit_pass
        if engine_mode() != "off":
            from astra_bot.decision.live_positions import DEFAULT_PATH as _LIVE_POS
            _ex = await run_exit_pass(
                broker, send_tg=_send_live_tg, positions_path=_LIVE_POS
            )
            if _ex.get("actions"):
                logger.info("exit_engine: %s", _ex)
    except Exception as _ex_exc:
        logger.warning("exit_engine FAILED: %s", type(_ex_exc).__name__)

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

        try:
            entered, skipped = await run_arm_entries(
                broker, rows, max_entries=3, send_tg=_send_live_tg
            )
            logger.info("arm tick entries=%d skipped=%s", len(entered), skipped)
        except Exception as arm_exc:
            # Belt-and-suspenders: arm must never kill the tick (owner №25)
            logger.exception("arm tick FAILED (swallowed): %s", type(arm_exc).__name__)
            entered, skipped = [], {"other": 1}
            try:
                await _send_live_tg(
                    f"⚠️ arm tick error — {type(arm_exc).__name__}: {arm_exc}"
                )
            except Exception:
                pass
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
                    "identity_ok": (
                        len(rows)
                        == len(entered_syms) + sum(int(v or 0) for v in (skipped or {}).values())
                    ),
                    "ts": time.time(),
                }
            )
            _id_ok = len(rows) == len(entered_syms) + sum(int(v or 0) for v in (skipped or {}).values())
            if not _id_ok:
                logger.warning(
                    "arm_summary identity broken: cand=%s entered=%s skipped_sum=%s skipped=%s",
                    len(rows),
                    len(entered_syms),
                    sum(int(v or 0) for v in (skipped or {}).values()),
                    skipped,
                )
            if int((skipped or {}).get("other") or 0) > 0:
                logger.warning("arm_summary unattributed other=%s", skipped.get("other"))
        except Exception as _sum_exc:
            logger.warning("arm_summary FAILED: %s", type(_sum_exc).__name__)

    try:
        await _maybe_send_digest(broker, client, send_tg=_send_live_tg)
    except Exception as _dig_exc:
        logger.warning("live_digest tick FAILED: %s", type(_dig_exc).__name__)

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
