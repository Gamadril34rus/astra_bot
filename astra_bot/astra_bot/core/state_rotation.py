"""Rotation/compaction торговых state-файлов (TZ §28/§29).

Git — не бесконечная торговая БД: append-only JSONL ограничены по
размеру. При превышении лимита старые строки переезжают в
append-only архив ``<имя>.archive.jsonl`` (не удаляются — TZ: JSONL
только append-only), сам файл содержит последние N строк.

Вызывается в начале каждой live-сессии (scripts/run_bot.py) и может
запускаться отдельно (scripts/rotate_state.py).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Живые append-only файлы (live-схема), строки.
LIVE_JSONL_LIMITS: dict[str, int] = {
    "no_trade_observations.jsonl": 5_000,
    "decision_log.jsonl": 20_000,
    "live_lessons.jsonl": 10_000,
    "lessons.jsonl": 10_000,
    "paper_trades.jsonl": 10_000,
    "research_observations.jsonl": 20_000,
    "research/observations.jsonl": 10_000,
}

# Допустимый разброс на время сессии для size-gate (TZ §29).
SESSION_MARGIN_LINES = 500


def rotate_jsonl(path: Path, max_lines: int) -> int:
    """Обрезать ``path`` до ``max_lines`` последних строк; вырезанное —
    в append-only архив. Возвращает число перемещённых строк."""
    if not path.exists() or max_lines <= 0:
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) <= max_lines:
        return 0
    head, tail = lines[:-max_lines], lines[-max_lines:]
    archive = path.with_name(path.name[: -len(".jsonl")] + ".archive.jsonl")
    with archive.open("a", encoding="utf-8") as f:
        for line in head:
            if line.strip():
                f.write(line + "\n")
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(tail) + ("\n" if tail else ""), encoding="utf-8")
    tmp.replace(path)
    logger.info(
        "Rotation %s: -%d строк (осталось %d, архив %s)",
        path.name, len(head), len(tail), archive.name,
    )
    return len(head)


def rotate_all(root: Path = Path("models")) -> dict[str, int]:
    """Прокрутить все живые JSONL под ``root``. {relpath: перемещено}."""
    moved: dict[str, int] = {}
    for rel, limit in LIVE_JSONL_LIMITS.items():
        path = root / rel
        n = rotate_jsonl(path, limit)
        if n:
            moved[str(rel)] = n
    if moved:
        logger.info("State rotation: %s", moved)
    return moved


def size_gate(root: Path = Path("models")) -> list[str]:
    """Проверка размера (TZ §29): возвращает список нарушений.

    Лимит = rotation cap + SESSION_MARGIN_LINES (сессия может добавить
    немного строк после прокрутки)."""
    violations: list[str] = []
    for rel, limit in LIVE_JSONL_LIMITS.items():
        path = root / rel
        if not path.exists():
            continue
        n = sum(1 for line in path.open("r", encoding="utf-8") if line.strip())
        cap = limit + SESSION_MARGIN_LINES
        if n > cap:
            violations.append(f"{rel}: {n} строк > лимита {cap}")
    return violations


def jsonl_integrity(path: Path) -> list[int]:
    """Номера строк, не являющиеся валидным JSON (для smoke-проверки)."""
    bad: list[int] = []
    if not path.exists():
        return bad
    for i, line in enumerate(
        path.open("r", encoding="utf-8"), start=1
    ):
        line = line.strip()
        if not line:
            continue
        try:
            json.loads(line)
        except Exception:
            bad.append(i)
    return bad
