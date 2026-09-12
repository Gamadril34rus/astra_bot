"""
ASTRA BOT — Логирование

Текстовый формат по умолчанию. JSON + request_id — ``LOG_FORMAT=json``
или ``setup_logging(json_logs=True)`` (TZ P2.3).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from .request_context import get_request_id


class JsonFormatter(logging.Formatter):
    """Одна JSON-строка на событие: ts, level, logger, msg, request_id."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": get_request_id(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rid = get_request_id()
        if rid and rid != "-":
            record.msg = f"[{rid}] {record.msg}"
        return super().format(record)


def _coerce_level(level: int | str) -> int:
    if isinstance(level, int):
        return level
    name = str(level).upper()
    return getattr(logging, name, logging.INFO)


def setup_logging(
    level: int | str = logging.INFO,
    log_dir: str | Path | None = None,
    rotation: str = "midnight",
    retention_days: int = 30,
    json_logs: bool | None = None,
) -> None:
    """
    Настройка логирования системы.

    Args:
        level: Уровень логирования (int или имя, например ``INFO``)
        log_dir: Директория для логов
        rotation: Политика ротации (зарезервировано)
        retention_days: Срок хранения логов (зарезервировано)
        json_logs: JSON-строки. None → ``LOG_FORMAT=json``
    """
    del rotation, retention_days  # reserved; file rotation is daily by name
    level_int = _coerce_level(level)
    if json_logs is None:
        json_logs = os.environ.get("LOG_FORMAT", "").strip().lower() == "json"

    formatter: logging.Formatter
    if json_logs:
        formatter = JsonFormatter()
    else:
        formatter = TextFormatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level_int)

    root_logger = logging.getLogger()
    root_logger.setLevel(level_int)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("ccxt").setLevel(logging.WARNING)

    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            log_path / f"astra_{datetime.now().strftime('%Y%m%d')}.log"
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level_int)
        root_logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """Получить логгер для компонента"""
    return logging.getLogger(name)


loggers: dict[str, logging.Logger] = {}


def get_component_logger(component: str) -> logging.Logger:
    """Получить логгер компонента с кэшированием"""
    if component not in loggers:
        loggers[component] = get_logger(f"astra.{component}")
    return loggers[component]
