"""Настройка логирования для Delta-Neutral контроллера."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
) -> None:
    """Настроить логирование контроллера.

    Args:
        level: Уровень логирования (DEBUG, INFO, WARNING, ERROR).
        log_file: Путь к файлу лога (опционально).
    """
    log_format = "%(asctime)s | %(levelname)-7s | %(name)-16s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    handlers: list[logging.Handler] = []

    # Консольный хендлер
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(log_format, datefmt=date_format))
    handlers.append(console)

    # Файловый хендлер (если указан)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))
        handlers.append(file_handler)

    # Настраиваем корневой логгер для dn.*
    root_logger = logging.getLogger("dn")
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    root_logger.handlers.clear()
    for handler in handlers:
        root_logger.addHandler(handler)

    # Подавляем слишком подробные логи от SDK
    logging.getLogger("nado_grid").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
