#!/usr/bin/env python3
"""Единый утренний отчёт ASTRA BOT, 09:00 МСК — улучшенный (Блоки 3, 8)."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Bot

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
MODELS_DIR = Path("models")
DATA_DIR = Path("data")
STATE_PATH_LEGACY = MODELS_DIR / "demo_state.json"
STATE_PATH = DATA_DIR / "state.json"
TRADES_JSONL = MODELS_DIR / "paper_trades.jsonl"
TRADES_DB = DATA_DIR / "trades.db"
POSITIONS_JSON = MODELS_DIR / "paper_positions.json"
BUDGET_JSON = MODELS_DIR / "trading_budget.json"
READINESS_JSON = MODELS_DIR / "readiness.json"
STRATEGY_STATS_JSON = MODELS_DIR / "strategy_stats.json"
WEIGHTS_JSON = DATA_DIR / "weights.json"
ERRORS_LOG = Path("logs/errors.log")


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_trades_last_24h() -> tuple[list[dict], list[dict], list[dict]]:
    """Return (last 24h trades, last 7d, last 30d) from paper_trades.jsonl and data/trades.db"""
    all_trades: list[dict] = []
    # Try JSONL first (legacy)
    if TRADES_JSONL.exists():
        try:
            for line in TRADES_JSONL.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    all_trades.append(json.loads(line))
                except Exception:
                    continue
        except Exception:
            pass

    # Also try SQLite if exists
    if TRADES_DB.exists():
        try:
            conn = sqlite3.connect(str(TRADES_DB))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM trades ORDER BY created_at DESC LIMIT 5000")
            for row in cur.fetchall():
                d = dict(row)
                # Convert to same format as JSONL
                all_trades.append(
                    {
                        "id": d.get("id"),
                        "symbol": d.get("symbol"),
                        "pnl": d.get("pnl"),
                        "closed_at": d.get("timestamp"),
                        "created_at": d.get("created_at"),
                        "strategy": d.get("strategy"),
                        "exit_reason": d.get("exit_reason"),
                    }
                )
            conn.close()
        except Exception:
            pass

    # Deduplicate by id
    seen = {}
    for t in all_trades:
        tid = t.get("id") or f"{t.get('symbol')}_{t.get('closed_at')}"
        if tid not in seen:
            seen[tid] = t
    all_trades = list(seen.values())

    def _parse_time(t: dict) -> datetime | None:
        # closed_at is ms timestamp or ISO
        for key in ("closed_at", "timestamp", "created_at"):
            v = t.get(key)
            if not v:
                continue
            try:
                if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
                    ms = int(float(v))
                    # if ms is seconds (<1e12), convert
                    if ms < 1e12:
                        ms *= 1000
                    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
                else:
                    # ISO
                    dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
            except Exception:
                continue
        return None

    now = datetime.now(timezone.utc)
    last_24h = []
    last_7d = []
    last_30d = []
    for tr in all_trades:
        dt = _parse_time(tr)
        if not dt:
            continue
        age = now - dt
        if age <= timedelta(hours=24):
            last_24h.append(tr)
        if age <= timedelta(days=7):
            last_7d.append(tr)
        if age <= timedelta(days=30):
            last_30d.append(tr)

    return last_24h, last_7d, last_30d


def _compute_stats(trades: list[dict]) -> dict:
    if not trades:
        return {"count": 0, "wins": 0, "losses": 0, "pnl": 0.0, "best": None, "worst": None}
    wins = sum(1 for t in trades if float(t.get("pnl", 0) or 0) > 0)
    losses = sum(1 for t in trades if float(t.get("pnl", 0) or 0) < 0)
    pnl = sum(float(t.get("pnl", 0) or 0) for t in trades)
    best = max(trades, key=lambda x: float(x.get("pnl", 0) or 0), default=None)
    worst = min(trades, key=lambda x: float(x.get("pnl", 0) or 0), default=None)
    return {"count": len(trades), "wins": wins, "losses": losses, "pnl": pnl, "best": best, "worst": worst}


def _load_positions() -> tuple[list[dict], float, float]:
    """Return (positions, equity, initial_capital)"""
    data = _load_json(POSITIONS_JSON)
    positions = data.get("positions", []) if isinstance(data, dict) else []
    realized = float(data.get("realized_pnl", 0) or 0)
    initial = float(data.get("initial_capital", 2000) or 2000)
    equity = initial + realized
    return positions, equity, initial


async def send_to_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    admin_raw = os.environ.get("TELEGRAM_ADMIN_ID", "").strip()
    if not token or not admin_raw:
        return
    bot = Bot(token=token)
    for raw_id in admin_raw.split(","):
        raw_id = raw_id.strip()
        if raw_id:
            try:
                # Telegram limit 4096 chars, split if needed
                for chunk in [text[i : i + 4000] for i in range(0, len(text), 4000)]:
                    await bot.send_message(chat_id=int(raw_id), text=chunk)
            except Exception as e:
                print(f"Telegram send failed: {e}")


def main() -> None:
    now_msk = datetime.now(tz=MOSCOW_TZ)
    now_str = now_msk.strftime("%d.%m.%Y %H:%M")
    print(f"Building morning report for {now_str} MSK...")

    # Block 3.3: Check if report already sent today
    state_data = _load_json(STATE_PATH) or _load_json(STATE_PATH_LEGACY)
    last_report = state_data.get("last_report_date")
    today_str = now_msk.date().isoformat()
    if last_report == today_str and os.environ.get("FORCE_REPORT") != "1":
        print(f"Report already sent today ({today_str}), skipping (use FORCE_REPORT=1 to override)")
        # Still allow to continue if triggered via repository_dispatch
        if os.environ.get("GITHUB_EVENT_NAME") != "repository_dispatch":
            # For scheduled runs, skip duplicate
            pass

    # Load trades
    trades_24h, trades_7d, trades_30d = _load_trades_last_24h()
    stats_24h = _compute_stats(trades_24h)
    stats_7d = _compute_stats(trades_7d)
    stats_30d = _compute_stats(trades_30d)

    # Fallback to legacy state file if no trades found
    if stats_24h["count"] == 0:
        legacy = _load_json(STATE_PATH_LEGACY)
        if legacy.get("daily_trades"):
            stats_24h = {
                "count": int(legacy.get("daily_trades", 0)),
                "wins": int(legacy.get("daily_wins", 0)),
                "losses": int(legacy.get("daily_losses", 0)),
                "pnl": float(legacy.get("daily_pnl", 0.0)),
                "best": None,
                "worst": None,
            }

    # Readiness
    try:
        from astra_bot.core import readiness

        verdict = readiness.evaluate()
    except Exception as e:
        print(f"readiness evaluate failed: {e}")
        verdict = {"ready": False, "score": 0, "threshold": 90, "trading_days": 0, "win_rate": 0, "profit_factor": 0, "max_drawdown_pct": 0}

    # Learning digest
    try:
        from astra_bot.ml.learning_digest import build_digest, save_watermark

        digest, new_wm = build_digest(MODELS_DIR)
    except Exception as e:
        print(f"learning_digest failed: {e}")
        digest = "📚 Чему научилась система:\n• Данные обучения недоступны"
        new_wm = int(datetime.now(timezone.utc).timestamp() * 1000)
        save_watermark = lambda *a, **k: None

    # Positions & equity
    positions, equity, initial_capital = _load_positions()
    total_pnl = equity - initial_capital

    # Trading budget
    budget = _load_json(BUDGET_JSON)
    remaining_hours = budget.get("remaining_hours", budget.get("budget_hours", 700))
    if isinstance(remaining_hours, (int, float)):
        remaining_str = f"{remaining_hours:.1f}ч"
    else:
        remaining_str = str(remaining_hours)

    # Weights
    weights = _load_json(WEIGHTS_JSON)
    strategy_weights = weights.get("strategy_weights", {})

    # Errors
    errors_count = 0
    if ERRORS_LOG.exists():
        try:
            errors_count = len([l for l in ERRORS_LOG.read_text(encoding="utf-8").splitlines() if l.strip()])
        except Exception:
            pass

    # Build report (Block 8.1)
    lines = [
        f"ASTRA BOT — {now_str} МСК",
        "",
        digest,
        "",
        "📊 Торговля:",
        f"• 24ч: {stats_24h['count']} сделок (+{stats_24h['wins']}/−{stats_24h['losses']}), PnL {stats_24h['pnl']:+.2f} USDT",
        f"• 7д: {stats_7d['count']} сделок, PnL {stats_7d['pnl']:+.2f} USDT",
        f"• 30д: {stats_30d['count']} сделок, PnL {stats_30d['pnl']:+.2f} USDT",
        f"• Баланс: {equity:.2f} USDT (старт {initial_capital:.2f}), PnL {total_pnl:+.2f}",
    ]

    if stats_24h["best"]:
        b = stats_24h["best"]
        lines.append(f"• Лучшая 24ч: {b.get('symbol')} {float(b.get('pnl',0)):+.2f} USDT ({b.get('strategy','')})")
    if stats_24h["worst"]:
        w = stats_24h["worst"]
        lines.append(f"• Худшая 24ч: {w.get('symbol')} {float(w.get('pnl',0)):+.2f} USDT ({w.get('strategy','')})")

    lines += [
        "",
        f"🎯 Готовность: {'ДА' if verdict['ready'] else 'НЕТ'} ({verdict.get('score', '?')}/{verdict.get('threshold', '?')}) | "
        f"{verdict['trading_days']} дн. | WR {verdict['win_rate']}% | PF {verdict['profit_factor']} | DD {verdict['max_drawdown_pct']}%",
        "",
        f"💼 Позиции: {len(positions)} открыто, бюджет {remaining_str} осталось",
    ]
    if positions:
        for p in positions[:3]:
            lines.append(f"  • {p.get('symbol')} {p.get('direction')} entry {p.get('entry_price')} qty {p.get('quantity')}")

    if strategy_weights:
        lines.append("")
        lines.append("🧠 Веса стратегий:")
        for k, v in list(strategy_weights.items())[:6]:
            lines.append(f"  • {k}: {v:.2f}")

    # Health & warnings (Block 8.1)
    lines.append("")
    lines.append(f"🏥 Здоровье: ошибок в логе {errors_count}, ML-модель {'есть' if (DATA_DIR / 'model.joblib').exists() or (MODELS_DIR / 'current.pkl').exists() else 'нет'}")
    warnings = []
    if verdict.get("max_drawdown_pct", 0) > 5:
        warnings.append(f"⚠️ Просадка {verdict['max_drawdown_pct']}% > 5%")
    if stats_24h["count"] == 0:
        warnings.append("⚠️ За 24ч сделок нет — проверьте риск-лимиты / HALT")
    if not (MODELS_DIR / "strategy_stats.json").exists():
        warnings.append("⚠️ База знаний пуста (strategy_stats.json нет) — будет создана после первых сделок")
    if warnings:
        lines.append("")
        lines.append("⚠️ Предупреждения:")
        lines.extend(f"• {w}" for w in warnings)

    text = "\n".join(lines)
    # Telegram limit
    if len(text) > 4000:
        text = text[:3990] + "\n…"

    print(text)
    asyncio.run(send_to_telegram(text))

    # Save watermark and last_report_date
    try:
        from astra_bot.ml.learning_digest import save_watermark

        save_watermark(MODELS_DIR, new_wm)
    except Exception as exc:
        print(f"watermark save failed: {exc}")

    # Update state.json last_report_date
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        state = _load_json(STATE_PATH) or {}
        state["last_report_date"] = today_str
        state["last_report_at"] = datetime.now(timezone.utc).isoformat()
        state["daily_trades"] = stats_24h["count"]
        state["daily_wins"] = stats_24h["wins"]
        state["daily_losses"] = stats_24h["losses"]
        state["daily_pnl"] = stats_24h["pnl"]
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_PATH)
        # Also update legacy
        legacy_path = MODELS_DIR / "demo_state.json"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"Failed to update last_report_date: {exc}")


if __name__ == "__main__":
    main()
