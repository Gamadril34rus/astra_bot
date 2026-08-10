#!/usr/bin/env python3
"""
Утренний отчёт о виртуальном счёте в Telegram.

Cron: 0 9 * * *  (каждое утро в 9:00)

Берёт последний self-play отчёт (если он есть) и шлёт его админам.
При наличии TELEGRAM_BOT_TOKEN и TELEGRAM_ADMIN_ID отправка происходит
автоматически.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astra_bot.core.logger import setup_logging
from astra_bot.ml.self_play import LearningReport, format_daily_report

LESSONS_PATH = PROJECT_ROOT / "models" / "lessons.jsonl"
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def build_report_from_lessons() -> LearningReport:
    """Собрать LearningReport из ранее сохранённого lessons.jsonl."""
    if not LESSONS_PATH.exists():
        return LearningReport(
            total_trades=0, wins=0, losses=0, win_rate=0.0,
            total_pnl=0.0, profit_factor=0.0, max_drawdown_pct=0.0,
            final_equity=2000.0, sharpe=0.0,
            lessons_path=LESSONS_PATH,
            started_learning=False,
            message="Сначала запустите самообучение: python scripts/self_play.py",
        )

    lessons = []
    with open(LESSONS_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                lessons.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not lessons:
        return LearningReport(
            total_trades=0, wins=0, losses=0, win_rate=0.0,
            total_pnl=0.0, profit_factor=0.0, max_drawdown_pct=0.0,
            final_equity=2000.0, sharpe=0.0,
            lessons_path=LESSONS_PATH,
            started_learning=False,
            message="Файл уроков пуст",
        )

    wins = sum(1 for row in lessons if row.get("outcome") == "win")
    losses = sum(1 for row in lessons if row.get("outcome") == "loss")
    total_pnl = sum(row.get("pnl", 0.0) for row in lessons)
    gp = sum(row["pnl"] for row in lessons if row.get("pnl", 0) > 0)
    gl = abs(sum(row["pnl"] for row in lessons if row.get("pnl", 0) < 0))
    pf = gp / gl if gl else float("inf")
    win_rate = wins / len(lessons) * 100

    # Доходность по сделкам → упрощённый Sharpe и max drawdown по эквити.
    rets = [row.get("pnl_pct", 0.0) for row in lessons]
    mean = sum(rets) / len(rets)
    std = (sum((r - mean) ** 2 for r in rets) / len(rets)) ** 0.5
    sharpe = mean / std if std else 0.0

    equity = 2000.0
    peak = equity
    max_dd = 0.0
    for row in lessons:
        equity += float(row.get("pnl", 0.0))
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100.0)

    by_symbol: dict[str, dict[str, float]] = {}
    for row in lessons:
        sym = row.get("symbol", "UNKNOWN")
        bucket = by_symbol.setdefault(sym, {"trades": 0, "wins": 0, "pnl": 0.0})
        bucket["trades"] += 1
        if row.get("outcome") == "win":
            bucket["wins"] += 1
        bucket["pnl"] += float(row.get("pnl", 0.0))
    for bucket in by_symbol.values():
        bucket["win_rate"] = (
            bucket["wins"] / bucket["trades"] * 100 if bucket["trades"] else 0.0
        )

    return LearningReport(
        total_trades=len(lessons),
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        total_pnl=total_pnl,
        profit_factor=pf,
        max_drawdown_pct=max_dd,
        final_equity=2000.0 + total_pnl,
        sharpe=sharpe,
        lessons_path=LESSONS_PATH,
        started_learning=len(lessons) >= 2000,
        message=f"Зафиксировано {len(lessons)} виртуальных сделок",
        by_symbol=by_symbol,
    )


def load_latest_model_metrics(model_path: Path = Path("models/current.pkl")) -> dict:
    """Достать AUC/accuracy из последней сохранённой модели."""
    if not model_path.exists():
        return {"version": "не обучена", "auc": 0.0, "accuracy": 0.0, "n": 0}
    try:
        from astra_bot.ml.model_trainer import MLModel

        m = MLModel.load(str(model_path))
        return {
            "version": m.config.model_type if m.config else "unknown",
            "auc": float(getattr(m.metrics, "roc_auc", 0.0) or 0.0),
            "accuracy": float(getattr(m.metrics, "accuracy", 0.0) or 0.0),
            "n": int(getattr(m.metrics, "n_samples", 0) or 0),
        }
    except Exception:
        return {"version": "не загрузилась", "auc": 0.0, "accuracy": 0.0, "n": 0}


async def send_to_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    admin_raw = os.environ.get("TELEGRAM_ADMIN_ID", "")
    if not token or not admin_raw:
        print("TELEGRAM_BOT_TOKEN/TELEGRAM_ADMIN_ID не заданы — отчёт выводится в stdout")
        return

    from telegram import Bot

    bot = Bot(token=token)
    for admin_id in [int(x) for x in admin_raw.split(",") if x.strip()]:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode="Markdown")
            print(f"Отправлено админу {admin_id}")
        except Exception as exc:
            print(f"Не удалось отправить {admin_id}: {exc}")


def main() -> None:
    setup_logging()
    now = datetime.now(tz=MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")
    report = build_report_from_lessons()
    body = format_daily_report(report)
    model = load_latest_model_metrics()
    if model["version"] not in {"не обучена", "не загрузилась"}:
        body += (
            f"\n\n🧠 *Модель:* {model['version']}\n"
            f"   AUC={model['auc']:.3f}, accuracy={model['accuracy']:.3f}"
        )
    else:
        body += "\n\n🧠 Модель пока не обучена — иду уроки."
    text = f"☀️ *Утренний отчёт* — {now} МСК\n\n{body}"
    print(text)
    asyncio.run(send_to_telegram(text))


if __name__ == "__main__":
    main()
