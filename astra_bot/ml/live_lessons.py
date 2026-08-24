"""
Запись реальных закрытых paper-сделок в уроки для ML.

Каждая закрытая сделка живого paper-движка превращается в :class:`Lesson`
и дописывается в ``models/live_lessons.jsonl``. Это настоящие данные
(реальные свечи OKX demo, реальные сигналы стратегий), а не синтетика —
на них weekly-модель дообучается и со временем начинает допускать меньше
ошибок.

В отличие от self-play, здесь признаки считаются по факту входа из
сохранённых заметок позиции (``notes``), а рекомендация выводится из
причины выхода и исхода.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_LIVE_LESSONS = Path("models/live_lessons.jsonl")


def _factor(trade: dict[str, Any]) -> tuple[str, str]:
    """Подобрать влияющий фактор и рекомендацию по исходу/причине выхода."""
    reason = str(trade.get("exit_reason", "")).lower()
    pnl = float(trade.get("pnl", 0.0) or 0.0)
    pnl_pct = float(trade.get("pnl_pct", 0.0) or 0.0)

    if "tp" in reason:
        factor = "TAKE_PROFIT"
        rec = "HOLD_WINNERS"
    elif "stop" in reason:
        factor = "STOP_LOSS"
        # Большой стоп — слишком широкий/ранний вход.
        if abs(pnl_pct) > 1.5:
            rec = "WIDEN_STOP_LOSS"
        else:
            rec = "SKIP_FALSE_BREAKOUT"
    else:
        factor = "TIME_EXIT"
        rec = "EXIT_EARLY_LOW_MOMENTUM" if pnl < 0 else "HOLD_WINNERS"
    return factor, rec


def trade_to_lesson(trade: dict[str, Any]) -> dict[str, Any]:
    """Преобразовать запись ClosedTrade в словарь-урок."""
    pnl = float(trade.get("pnl", 0.0) or 0.0)
    if abs(pnl) < 1e-9:
        outcome = "breakeven"
    elif pnl > 0:
        outcome = "win"
    else:
        outcome = "loss"

    factor, rec = _factor(trade)
    notes = trade.get("notes") or {}
    features = {
        "confidence": float(notes.get("score", notes.get("confidence", 0.0)) or 0.0),
        "ml_probability": float(notes.get("ml_probability", 0.0) or 0.0),
        "edge_pct": float(notes.get("edge_pct", 0.0) or 0.0),
        "rr": float(notes.get("rr", 0.0) or 0.0),
        "live_pnl_pct": float(trade.get("pnl_pct", 0.0) or 0.0),
    }

    now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    return {
        "trade_id": trade.get("id", ""),
        "symbol": trade.get("symbol", ""),
        "direction": trade.get("direction", ""),
        "entry_time": int(trade.get("opened_at", 0) or 0),
        "exit_time": int(trade.get("closed_at", now_ms) or now_ms),
        "entry_price": float(trade.get("entry_price", 0.0) or 0.0),
        "exit_price": float(trade.get("exit_price", 0.0) or 0.0),
        "qty": float(trade.get("quantity", 0.0) or 0.0),
        "pnl": pnl,
        "pnl_pct": float(trade.get("pnl_pct", 0.0) or 0.0),
        "outcome": outcome,
        "strategy": trade.get("strategy", "live"),
        "confidence": features["confidence"],
        "features": features,
        "market_regime": notes.get("regime", "UNKNOWN"),
        "news_impulse": bool(notes.get("news_impulse", False)),
        "influencing_factor": factor,
        "counterfactual": (
            "следовало войти раньше" if outcome == "win" and "tp" in str(trade.get("exit_reason", ""))
            else "следовало подождать подтверждения"
        ),
        "takeaway": (
            f"{trade.get('symbol','')} {trade.get('direction','')}: {outcome} "
            f"{pnl:+.2f} ({trade.get('exit_reason','')})"
        ),
        "recommendation": rec,
    }


def append_lessons(
    trades: Iterable[dict[str, Any]],
    path: Path = DEFAULT_LIVE_LESSONS,
) -> int:
    """Дописать уроки по закрытым сделкам в JSONL. Возвращает число добавленных."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "a", encoding="utf-8") as f:
        for trade in trades:
            try:
                lesson = trade_to_lesson(trade)
                f.write(json.dumps(lesson, ensure_ascii=False) + "\n")
                count += 1
            except Exception as exc:
                logger.warning("Не записал урок по сделке: %s", exc)
    if count:
        logger.info("Записано %d live-уроков в %s", count, path)
    return count


def merge_into_main_lessons(
    live_path: Path = DEFAULT_LIVE_LESSONS,
    main_path: Path = Path("models/lessons.jsonl"),
) -> int:
    """Слить live-уроки в общий lessons.jsonl для weekly-обучения.

    Дубликаты по trade_id пропускаются. Возвращает число добавленных строк.
    """
    live_path = Path(live_path)
    main_path = Path(main_path)
    if not live_path.exists():
        return 0
    existing_ids: set[str] = set()
    if main_path.exists():
        with open(main_path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    if row.get("trade_id"):
                        existing_ids.add(str(row["trade_id"]))
                except json.JSONDecodeError:
                    continue

    added = 0
    with open(live_path, encoding="utf-8") as src, open(main_path, "a", encoding="utf-8") as dst:
        for line in src:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            tid = str(row.get("trade_id", ""))
            if tid and tid in existing_ids:
                continue
            dst.write(json.dumps(row, ensure_ascii=False) + "\n")
            if tid:
                existing_ids.add(tid)
            added += 1
    if added:
        logger.info("Смержено %d live-уроков в %s", added, main_path)
    return added
