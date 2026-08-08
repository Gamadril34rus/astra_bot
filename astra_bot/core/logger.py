"""
ASTRA BOT — Логирование
"""

import logging
import sys
from pathlib import Path
from typing import Optional
from datetime import datetime


def setup_logging(
    level: int = logging.INFO,
    log_dir: str | Path | None = None,
    rotation: str = "midnight",
    retention_days: int = 30,
) -> None:
    """
    Настройка логирования системы.
    
    Args:
        level: Уровень логирования
        log_dir: Директория для логов
        rotation: Политика ротации
        retention_days: Срок хранения логов
    """
    # Формат сообщений
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Консольный обработчик
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    
    # Настройка корневого логгера
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    
    # Логгер для конкретных компонентов
    # Подавление шума от библиотек
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("ccxt").setLevel(logging.WARNING)
    
    # Файловый логгер (если указано)
    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        
        file_handler = logging.FileHandler(
            log_path / f"astra_{datetime.now().strftime('%Y%m%d')}.log"
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root_logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """Получить логгер для компонента"""
    return logging.getLogger(name)


# Преднастроенные логгеры для основных компонентов
loggers = {}

def get_component_logger(component: str) -> logging.Logger:
    """Получить логгер компонента с кэшированием"""
    if component not in loggers:
        loggers[component] = get_logger(f"astra.{component}")
    return loggers[component]
