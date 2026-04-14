from __future__ import annotations

import logging
from pathlib import Path

from src.app.config_loader import LoggingConfig


def setup_logging(project_root: Path, logging_cfg: LoggingConfig) -> None:
    """初始化日志输出，默认同时输出到控制台和文件。"""
    level_name = (logging_cfg.level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    log_file = project_root / logging_cfg.file
    log_file.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    root.addHandler(console_handler)
    root.addHandler(file_handler)
