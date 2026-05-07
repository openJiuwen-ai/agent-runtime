# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from __future__ import annotations

import logging
import os
from pathlib import Path


def setup_error_file_logging() -> Path:
    """为本应用单独落盘错误日志（ERROR+），避免被大量 INFO 淹没。

    默认路径：<app_root>/logs/error.log
    可通过环境变量 IR_ERROR_LOG_PATH 覆盖。
    """

    # Service root: .../applications/ir_execution_service
    app_root = Path(__file__).resolve().parent.parent.parent
    default_path = app_root / "logs" / "error.log"
    raw = (os.environ.get("IR_ERROR_LOG_PATH") or "").strip()
    log_path = Path(raw).expanduser().resolve() if raw else default_path.resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(str(log_path), encoding="utf-8")
    handler.setLevel(logging.ERROR)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    def _already_added(logger_obj: logging.Logger) -> bool:
        for h in logger_obj.handlers:
            if isinstance(h, logging.FileHandler):
                h_base = getattr(h, "baseFilename", None)
                hand_base = getattr(handler, "baseFilename", None)
                if h_base == hand_base:
                    return True
        return False

    # 尽量覆盖：根 logger、uvicorn、以及 openjiuwen 的 logger 层级
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access", "openjiuwen"):
        lg = logging.getLogger(name)
        if not _already_added(lg):
            lg.addHandler(handler)
        if lg.level > logging.ERROR:
            lg.setLevel(logging.ERROR)

    return log_path
