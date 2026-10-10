#!/usr/bin/env python3
"""Ротация больших Zeus state-файлов (лимит GitHub: 100 MB на один blob).

Авария 09.10: models/zeus_trade_journal.jsonl дорос до 104 821 452 Б
(100.02 MB), и шаг Save Zeus state стал отклоняться GitHub — pre-receive
GH001 «large blob detected» (лог владельца из запуска #2376). Следствия:
state не сохранялся, сделки умирали вместе с раннером, сводки
дублировались, а шаг Save завершался success, проглотив ошибку.

Схема: если файл больше ``MAX_BYTES`` (20 МБ), в основном файле остаются
последние ``KEEP_LINES`` (20 000) строк, всё остальное уходит в
``models/archive/<имя>_<UTC-штамп до секунд>.jsonl.gz``. Один архив на
событие ротации; штамп в имени не даёт архивам перезаписывать друг друга.
Граница 20k может разрезать строки одной сделки между архивом и хвостом —
это допустимо, читатели работают хвостовыми окнами.

Запуск (в том числе в начале бумажного тика):
    python scripts/rotate_zeus_states.py [--dry-run]

Exit 0 даже если ни одного файла нет — это штатная ситуация.
"""

from __future__ import annotations

import argparse
import gzip
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("rotate_zeus_states")

# Живые Zeus state-файлы. models/zeus_signals.jsonl из ТЗ в репо нет —
# реальные имена найдены grep-ом по дереву master.
STATE_FILES = (
    "models/zeus_trade_journal.jsonl",  # 104.8 МБ — виновник GH001
    "models/zeus_paper_trades.jsonl",
    "models/zeus_wedge_1h_signals.jsonl",  # реальный «signals»
    "models/zeus_no_trade_observations.jsonl",
)

MAX_BYTES = 20 * 1024 * 1024
KEEP_LINES = 20_000
ARCHIVE_DIR = "models/archive"


def rotate_file(
    path: Path,
    *,
    max_bytes: int = MAX_BYTES,
    keep_lines: int = KEEP_LINES,
    archive_dir: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """Обрезать ``path`` до ``keep_lines`` последних строк, старые — в gz.

    Отсутствующий или не превышающий порог файл не трогаем. Возвращает
    отчёт по файлу — он же идёт в лог, и на нём стоят тесты.
    """
    res = {
        "path": str(path),
        "rotated": False,
        "archived_lines": 0,
        "archived_bytes": 0,
        "kept_lines": 0,
        "size_before": 0,
        "size_after": 0,
        "archive": "",
        "skipped": "",
    }
    if not path.exists():
        res["skipped"] = "missing"
        return res

    size_before = path.stat().st_size
    res["size_before"] = size_before
    if size_before <= max_bytes:
        res["skipped"] = "under_threshold"
        return res

    lines = path.read_bytes().splitlines(keepends=True)
    if len(lines) <= keep_lines:
        # Строк мало, а файл большой — резать по строкам нечего.
        res["skipped"] = "under_keep_lines"
        return res

    head, tail = lines[:-keep_lines], lines[-keep_lines:]
    res["archived_lines"] = len(head)
    res["kept_lines"] = len(tail)
    if dry_run:
        res["size_after"] = sum(len(x) for x in tail)
        res["skipped"] = "dry_run"
        return res

    archive_dir = Path(archive_dir) if archive_dir else PROJECT_ROOT / ARCHIVE_DIR
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = archive_dir / f"{path.name.removesuffix('.jsonl')}_{stamp}.jsonl.gz"
    with gzip.open(archive, "wb") as gz:
        gz.writelines(head)

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(b"".join(tail))
    tmp.replace(path)

    res["rotated"] = True
    res["archived_bytes"] = archive.stat().st_size
    res["size_after"] = path.stat().st_size
    res["archive"] = str(archive)
    logger.info(
        "rotate %s: %d -> %d Б; в архив %d строк (%d Б gz -> %s); в файле %d строк",
        path.name,
        size_before,
        res["size_after"],
        len(head),
        res["archived_bytes"],
        archive.name,
        len(tail),
    )
    return res


def rotate_all(
    files: tuple[str, ...] = STATE_FILES,
    *,
    root: Path | None = None,
    max_bytes: int = MAX_BYTES,
    keep_lines: int = KEEP_LINES,
    archive_dir: Path | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Прокрутить список state-файлов; отсутствующие пропускаются молча."""
    root = Path(root) if root else PROJECT_ROOT
    out: list[dict] = []
    for rel in files:
        res = rotate_file(
            root / rel,
            max_bytes=max_bytes,
            keep_lines=keep_lines,
            archive_dir=archive_dir if archive_dir else root / ARCHIVE_DIR,
            dry_run=dry_run,
        )
        if res["rotated"]:
            logger.info(
                "rotation done %s: archived=%d строк/%d Б, left=%d строк/%d Б",
                res["path"], res["archived_lines"], res["archived_bytes"],
                res["kept_lines"], res["size_after"],
            )
        else:
            logger.info("rotation skip %s: %s (%d Б)", res["path"], res["skipped"], res["size_before"])
        out.append(res)
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Ротация больших Zeus state-файлов (лимит GitHub 100 MB)")
    p.add_argument("--dry-run", action="store_true", help="только показать, что уйдёт в архив")
    p.add_argument("--max-bytes", type=int, default=MAX_BYTES)
    p.add_argument("--keep-lines", type=int, default=KEEP_LINES)
    args = p.parse_args()

    results = rotate_all(max_bytes=args.max_bytes, keep_lines=args.keep_lines, dry_run=args.dry_run)
    rotated = [r for r in results if r["rotated"]]
    logger.info(
        "rotate_zeus_states: rotated=%d из %d файлов (порог %d Б, хвост %d строк)",
        len(rotated), len(results), args.max_bytes, args.keep_lines,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
