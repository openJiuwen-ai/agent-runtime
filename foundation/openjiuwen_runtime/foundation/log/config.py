# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

import logging
import logging.config
import os
from pathlib import Path
from typing import Any, Optional

import yaml

_config_loaded: bool = False

ENV_LOG_CONFIG = "OPENJIUWEN_RUNTIME_LOG_CONFIG"
ENV_LOG_FILE = "OPENJIUWEN_RUNTIME_LOG_FILE"
ENV_LOG_DIR = "OPENJIUWEN_RUNTIME_LOG_DIR"

_DEFAULT_LOG_DIR_FILENAME = "openjiuwen_runtime.log"
_STANDARD_LOG_FORMAT = (
    "%(asctime)s - %(levelname)s - %(name)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s"
)
_STANDARD_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
_FILE_HANDLER_MAX_BYTES = 10485760
_FILE_HANDLER_BACKUP_COUNT = 20
# OPENJIUWEN_RUNTIME_LOG_FILE 取下列值 = 显式关闭文件 handler（stdout-only 形态，
# K8s 等由平台采集 stdout 的部署用；容器内文件日志随 pod 生命周期丢失且无处导出）
_LOG_FILE_DISABLED_MARKERS = frozenset({"disabled", "off", "none", "false"})
_FILE_HANDLER_NAME = "file"


def _log_file_disabled(explicit: Optional[str] = None) -> bool:
    """判断是否显式关闭文件 handler（explicit 优先于环境变量）。"""
    raw = explicit if explicit is not None else os.getenv(ENV_LOG_FILE, "")
    return raw.strip().lower() in _LOG_FILE_DISABLED_MARKERS


def _drop_file_handler(config: dict[str, Any]) -> dict[str, Any]:
    """从 dictConfig 字典中移除 file handler（handlers / root.handlers / loggers 各处）。"""
    handlers = config.get("handlers")
    if not isinstance(handlers, dict) or _FILE_HANDLER_NAME not in handlers:
        return config

    patched = dict(config)

    def _filter_list(entries: Any) -> Any:
        if isinstance(entries, list):
            return [e for e in entries if e != _FILE_HANDLER_NAME]
        return entries

    patched_handlers = dict(handlers)
    patched_handlers.pop(_FILE_HANDLER_NAME)
    patched["handlers"] = patched_handlers

    root_cfg = config.get("root")
    if isinstance(root_cfg, dict):
        patched_root = dict(root_cfg)
        patched_root["handlers"] = _filter_list(root_cfg.get("handlers"))
        patched["root"] = patched_root

    loggers_cfg = config.get("loggers")
    if isinstance(loggers_cfg, dict):
        patched_loggers = {}
        for name, logger_cfg in loggers_cfg.items():
            if isinstance(logger_cfg, dict) and _FILE_HANDLER_NAME in (
                logger_cfg.get("handlers") or []
            ):
                item = dict(logger_cfg)
                item["handlers"] = _filter_list(logger_cfg.get("handlers"))
                patched_loggers[name] = item
            else:
                patched_loggers[name] = logger_cfg
        patched["loggers"] = patched_loggers

    return patched


def get_config_path() -> Path:
    """获取日志配置文件路径（每次调用重新读取 ``OPENJIUWEN_RUNTIME_LOG_CONFIG``）。"""
    override = os.getenv(ENV_LOG_CONFIG, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    current_dir = Path(__file__).parent
    return current_dir.parent / "config" / "logging.yaml"


def _resolve_log_file(raw: str) -> str:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return str(path.resolve())
    log_dir = os.getenv(ENV_LOG_DIR, "").strip()
    if log_dir:
        return str((Path(log_dir).expanduser().resolve() / path).resolve())
    return str((Path.cwd() / path).resolve())


def _pick_log_file_override(explicit: Optional[str] = None) -> Optional[str]:
    """解析 file handler 路径（每次调用重新读取 ``OPENJIUWEN_RUNTIME_LOG_*``）。"""
    if explicit and explicit.strip():
        return _resolve_log_file(explicit.strip())
    env_file = os.getenv(ENV_LOG_FILE, "").strip()
    if env_file:
        return _resolve_log_file(env_file)
    log_dir = os.getenv(ENV_LOG_DIR, "").strip()
    if log_dir:
        return str((Path(log_dir).expanduser().resolve() / _DEFAULT_LOG_DIR_FILENAME).resolve())
    return None


def _apply_log_file_override(config: dict[str, Any], log_file: Optional[str] = None) -> dict[str, Any]:
    resolved = _pick_log_file_override(log_file)
    if not resolved:
        return config

    handlers = config.get("handlers")
    if not isinstance(handlers, dict) or "file" not in handlers:
        return config

    patched = dict(config)
    patched_handlers = dict(handlers)
    file_handler = dict(patched_handlers["file"])
    file_handler["filename"] = resolved
    patched_handlers["file"] = file_handler
    patched["handlers"] = patched_handlers
    return patched


def _reset_root_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


def _attach_fallback_file_handler(resolved: str) -> None:
    from .handler import CompressedRotatingFileHandler

    log_path = Path(resolved)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(fmt=_STANDARD_LOG_FORMAT, datefmt=_STANDARD_LOG_DATEFMT)
    file_handler = CompressedRotatingFileHandler(
        resolved,
        maxBytes=_FILE_HANDLER_MAX_BYTES,
        backupCount=_FILE_HANDLER_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logging.getLogger().addHandler(file_handler)


def setup_logging(
    config_path: Optional[str] = None,
    *,
    log_file: Optional[str] = None,
) -> Optional[str]:
    """初始化或重新初始化 runtime SDK 日志配置。

    可重复调用；后一次调用会覆盖前一次配置。未显式传入的参数每次都会重新读取
    ``OPENJIUWEN_RUNTIME_LOG_CONFIG`` / ``OPENJIUWEN_RUNTIME_LOG_FILE`` /
    ``OPENJIUWEN_RUNTIME_LOG_DIR``。

    Args:
        config_path: 日志配置文件路径；默认 ``get_config_path()``。
        log_file: 覆盖 YAML 中 ``handlers.file.filename``；优先级高于环境变量。

    Returns:
        生效的文件日志绝对路径；未配置文件 handler 时返回 ``None``。
    """
    global _config_loaded

    if config_path is None:
        config_path = str(get_config_path())

    _reset_root_handlers()

    disabled = _log_file_disabled(log_file)
    resolved_log_file: Optional[str] = None
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
            if disabled:
                config = _drop_file_handler(config)
            else:
                config = _apply_log_file_override(config, log_file=log_file)
            logging.config.dictConfig(config)
        resolved_log_file = None if disabled else _pick_log_file_override(log_file)
    else:
        logging.basicConfig(
            level=logging.INFO,
            format=_STANDARD_LOG_FORMAT,
            datefmt=_STANDARD_LOG_DATEFMT,
        )
        if not disabled:
            resolved_log_file = _pick_log_file_override(log_file)
            if resolved_log_file:
                _attach_fallback_file_handler(resolved_log_file)

    _config_loaded = True
    return resolved_log_file


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """获取日志器；若尚未配置则按当前环境变量自动 ``setup_logging()``。"""
    if not _config_loaded:
        setup_logging()

    return logging.getLogger(name)
