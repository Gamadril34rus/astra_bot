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
    trigger_r: float = 0.8,
) -> dict:
    """P2: BE when mark reaches +trigger_r MFE (not TP1 fill). Net BE + only-tighten.

    Owner 19.10 / arena B2-B3: net_breakeven_price when available; never widen stop.
    One-shot via plan.be_moved + journal event=price_breakeven.
    """
    from astra_bot.decision.live_arm_wiring import append_journal
    from astra_bot.decision.live_positions import load_positions, upsert_plan
    from astra_bot.decision.live_stop_telemetry import record_stop_change
    from astra_bot.decision.live_margin_budget import only_tighter_stop

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
            mark = (
                getattr(pos, "mark_price", None)
                or getattr(pos, "markPrice", None)
                or getattr(pos, "price", None)
            )
            if mark is None:
                skipped.append(sym)
                continue
            mark_px = Decimal(str(mark))
            r_abs = abs(entry_px - stop_px)
            if r_abs <= 0 or exch_qty <= 0:
                continue
            side = str(plan.get("side") or "long").lower()
            if side == "long":
                mfe_r = float((mark_px - entry_px) / r_abs)
            else:
                mfe_r = float((entry_px - mark_px) / r_abs)
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
                "sync_partial_breakeven: %s failed: %s",
                sym, type(exc).__name__,
            )
            skipped.append(sym)

    return {"moved": moved, "skipped": skipped}



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
        from datetime import datetime, timezone, timedelta
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
    """Exit rows from trades.jsonl with ts >= 00:00 current MSK day."""
    dt = _msk_now(now_ts)
    day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    start_ts = day_start.timestamp()
    out: list[dict] = []
    if not trades_path.exists():
        return out
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

        tg = send_tg or _send_live_tg
        await tg(msg)
        _save_digest_last(key, digest_path)
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
                            "qty": str(
                                getattr(o, "quantity", None)
                                or getattr(o, "origQty", None)
                                or ""
                            ),
                            "reduceOnly": bool(
                                getattr(o, "reduce_only", None)
                                if getattr(o, "reduce_only", None) is not None
                                else getattr(o, "reduceOnly", False)
                            ),
                            "closePosition": bool(
                                getattr(o, "close_position", None)
                                if getattr(o, "close_position", None) is not None
                                else getattr(o, "closePosition", False)
                            ),
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
            _ex = await run_exit_pass(broker, send_tg=_send_live_tg)
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
