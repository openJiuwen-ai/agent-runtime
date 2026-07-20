# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""SDK 日志清理器：用 foundation Handler 替换 SDK 默认 Handler。

设计说明
--------
- openjiuwen SDK 内部使用 Python logging（SafeRotatingFileHandler）记录日志
- SDK 默认只支持「按大小轮转 + 按数量保留」，不支持 gzip 压缩和按天/按空间清理
- foundation 的 CompressedRotatingFileHandler 支持轮转 + gzip 压缩
- 通过继承 SDK 的 DefaultLogger 重写 _setup_logger()，用 foundation Handler 替换 SDK Handler
- 替换后 SDK 日志获得：gzip 压缩 + 按天数清理 + 按总空间清理

清理逻辑与 a2a/VA 保持一致：
- 轮转时触发清理（不是定时任务）
- 按天数清理：删除超过 retention_days 的 .gz 归档文件
- 按总空间清理：总空间超限时从最旧 .gz 归档开始逐一删除
- 活跃文件（无 .gz 后缀）不参与删除
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

# 从 foundation 导入 Handler（支持 gzip 压缩）
from openjiuwen_runtime.foundation.log.handler import CompressedRotatingFileHandler

# 从 SDK 导入 DefaultLogger 和相关类（不修改 SDK，只继承）
from openjiuwen.core.common.logging.default.default_impl import (
    DefaultLogger,
    SafeRotatingFileHandler,
    ContextFilter,
)
from openjiuwen.core.common.logging.manager import LogManager


class CleanableCompressedRotatingFileHandler(CompressedRotatingFileHandler):
    """扩展 foundation 的 CompressedRotatingFileHandler，增加按天数和按空间清理。

    继承关系：
        CleanableCompressedRotatingFileHandler
            → CompressedRotatingFileHandler (foundation，提供 gzip 压缩)
                → RotatingFileHandler (Python logging)
    """

    def __init__(
            self,
            filename: str,
            mode: str = "a",
            max_bytes: int = 0,
            backup_count: int = 0,
            encoding: str = "utf-8",
            delay: bool = False,
            retention_days: int = 0,
            max_total_size: int = 0,
    ):
        super().__init__(
            filename=filename,
            mode=mode,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding=encoding,
            delay=delay,
        )
        self._retention_days = retention_days
        self._max_total_size = max_total_size

    def doRollover(self) -> None:
        # 1. 调用 foundation 的轮转 + gzip 压缩逻辑
        super().doRollover()

        # 2. 清理逻辑（与 a2a/VA 保持一致，只删 .gz）
        if self._retention_days > 0:
            self._cleanup_by_retention()
        if self._max_total_size > 0:
            self._cleanup_by_total_size()

    def _cleanup_by_retention(self) -> None:
        """按天数清理（只清理 .gz 归档文件，不删活跃文件）"""
        log_path = Path(self.baseFilename)
        log_dir = log_path.parent
        log_name = log_path.name
        cutoff_time = (datetime.now(tz=timezone.utc) - timedelta(days=self._retention_days)).timestamp()

        for f in log_dir.glob(f"{log_name}*.gz"):
            if f.is_file():
                try:
                    if f.stat().st_mtime < cutoff_time:
                        f.unlink()
                except OSError as e:
                    # 用 print 到 stderr，不用 loguru（避免在 Handler 中递归调用日志）
                    print(f"[sdk_log_cleanup] WARN 按天数清理删除失败: {f} -> {e}", file=sys.stderr)

    def _cleanup_by_total_size(self) -> None:
        """按总空间清理（活跃文件计入总量但不删除，只删 .gz 归档文件）"""
        log_path = Path(self.baseFilename)
        log_dir = log_path.parent
        log_name = log_path.name
        active_file = log_path

        active_size = active_file.stat().st_size if active_file.exists() else 0
        archive_files = []
        for f in log_dir.glob(f"{log_name}*.gz"):
            if f.is_file():
                try:
                    archive_files.append((f.stat().st_mtime, f))
                except OSError as e:
                    print(f"[sdk_log_cleanup] WARN 扫描归档文件 stat 失败: {f} -> {e}", file=sys.stderr)
                    continue

        archive_total = sum(f.stat().st_size for _, f in archive_files if f.exists())
        total_size = active_size + archive_total
        if total_size <= self._max_total_size:
            return

        archive_files.sort(key=lambda x: x[0])  # 最旧在前
        for mtime, f in archive_files:
            if total_size <= self._max_total_size:
                break
            try:
                file_size = f.stat().st_size
                f.unlink()
                total_size -= file_size
            except OSError as e:
                print(f"[sdk_log_cleanup] WARN 按空间清理删除失败: {f} -> {e}", file=sys.stderr)


class CleanableDefaultLogger(DefaultLogger):
    """继承 SDK 的 DefaultLogger，在 _setup_logger() 中用 foundation Handler 替换 SDK Handler。

    继承关系：
        CleanableDefaultLogger
            → DefaultLogger (SDK)
    """

    def __init__(self, log_type: str, config: dict,
                 retention_days: int = 0, max_total_size: int = 0):
        self._retention_days = retention_days
        self._max_total_size = max_total_size
        super().__init__(log_type, config)

    def _setup_logger(self) -> None:
        """重写 _setup_logger：
        1. 调用 SDK 父类完成基础配置（console handler、level 等）
        2. 将 SafeRotatingFileHandler 替换为 foundation 的 CleanableCompressedRotatingFileHandler
        """
        # 先调用 SDK 父类完成基础配置
        super()._setup_logger()

        # 遍历 handlers，将 SDK 的 SafeRotatingFileHandler 替换为 foundation 的 Handler
        old_handlers = self._logger.handlers[:]
        for handler in old_handlers:
            if isinstance(handler, SafeRotatingFileHandler):
                # 移除 SDK 的 Handler
                self._logger.removeHandler(handler)
                handler.close()

                # 用 foundation 的 Handler 替换（获得压缩 + 清理能力）
                # backupCount/maxBytes 优先从环境变量读取（与 app.py 中的 configure_log_config 保持一致），
                # 避免旧 handler（SDK 默认值 20）覆盖用户配置
                env_backup_count = int(os.getenv("JIUWEN_LOG_BACKUP_COUNT", handler.backupCount))
                env_max_bytes = int(os.getenv("JIUWEN_LOG_MAX_BYTES", handler.maxBytes))
                cleanable_handler = CleanableCompressedRotatingFileHandler(
                    filename=handler.baseFilename,
                    max_bytes=env_max_bytes,
                    backup_count=env_backup_count,
                    encoding="utf-8",
                    retention_days=self._retention_days,
                    max_total_size=self._max_total_size,
                )
                cleanable_handler.addFilter(ContextFilter(self.log_type))
                cleanable_handler.setFormatter(handler.formatter)
                cleanable_handler.setLevel(handler.level)
                self._logger.addHandler(cleanable_handler)


# SDK 中所有 log_type 列表（含内置 4 个 + 懒加载的全部）
# 来源：openjiuwen/core/common/logging/__init__.py 中的 LazyLogger 定义
_ALL_SDK_LOG_TYPES = [
    # 内置（_Builtin_LOG_TYPES，initialize() 时创建）
    "common", "interface", "prompt_builder", "performance",
    # 懒加载（首次 get_logger() 时创建）
    "agent", "multi_agent", "workflow", "session", "controller", "runner",
    "sys_operation", "llm", "tool", "prompt", "store", "memory", "retrieval",
    "context_engine", "graph", "operator", "mcp", "team",
]


def setup_sdk_log_cleaner() -> None:
    """在 SDK 初始化完成后，替换 SDK 的 logger 为带清理逻辑的版本。

    关键步骤：
    1. 预触发所有懒加载 logger（遍历全部 log_type 调用 get_logger）
    2. 遍历所有已创建的 logger，用 register_logger 替换为 CleanableDefaultLogger
    3. 压缩残留的未压缩归档（.1/.2/... → .1.gz/.2.gz/...）

    注意：必须在 await initialize() 之后调用。

    背景：configure_log_config() 在 initialize() 之前执行，此时 SDK 用原生
    SafeRotatingFileHandler（不压缩）。如果此时活跃文件已超 maxBytes，轮转会
    生成 .1/.2/...（未压缩）。本函数第 3 步把这些残留文件压缩成 .gz，否则
    它们不会被清理逻辑扫描到（清理只扫 .gz），会永久残留。
    """
    retention_days = int(os.getenv("JIUWEN_LOG_RETENTION_DAYS", 7))
    max_total_size = int(os.getenv("JIUWEN_LOG_MAX_TOTAL_SIZE", 524288000))

    # 第 1 步：预触发所有懒加载 logger，确保它们被创建并加入 _loggers dict
    for log_type in _ALL_SDK_LOG_TYPES:
        try:
            LogManager.get_logger(log_type)
        except Exception as e:
            # 某些 logger 可能因配置缺失创建失败，跳过但不静默
            print(f"[sdk_log_cleanup] WARN 预触发 logger 失败 log_type={log_type}: {e}", file=sys.stderr)

    # 第 2 步：遍历所有已创建的 logger，逐个替换
    all_loggers = LogManager.get_all_loggers()
    for log_type, original_logger in all_loggers.items():
        config = original_logger.get_config()

        cleanable_logger = CleanableDefaultLogger(
            log_type=log_type,
            config=config,
            retention_days=retention_days,
            max_total_size=max_total_size,
        )

        LogManager.register_logger(log_type, cleanable_logger)

    # 第 3 步：压缩残留的未压缩归档（configure_log_config 到本函数之间产生的 .1/.2/...）
    _compress_legacy_archives(all_loggers)


def _compress_legacy_archives(all_loggers: dict) -> None:
    """把 logger 目录下残留的 .1/.2/...（未压缩）归档压缩成 .gz。

    只处理 .1/.2/... 这种纯数字后缀的文件，不处理 .1.gz（已压缩）。
    压缩时如果目标 .gz 已存在，直接删除未压缩版本（避免覆盖）。
    """
    import gzip
    import shutil

    seen_keys = set()  # (log_dir, log_name) 组合去重，避免同目录不同文件名被误跳过
    for log_type, logger in all_loggers.items():
        # 从 logger 的 handler 拿 baseFilename
        py_logger = getattr(logger, "_logger", None) or getattr(logger, "py_logger", None)
        if py_logger is None:
            continue
        for handler in py_logger.handlers:
            base_filename = getattr(handler, "baseFilename", None)
            if not base_filename:
                continue
            log_dir = str(Path(base_filename).parent)
            log_name = Path(base_filename).name
            key = (log_dir, log_name)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            # 扫描 .1/.2/... 这种纯数字后缀（不带 .gz）
            for legacy in Path(log_dir).glob(f"{log_name}.[0-9]*"):
                if legacy.suffix == ".gz":
                    continue  # 已压缩
                # 检查是否是纯数字后缀（如 jiuwen.log.1）
                suffix = legacy.name[len(log_name) + 1:]
                if not suffix.isdigit():
                    continue
                gz_path = legacy.with_suffix(legacy.suffix + ".gz")
                if gz_path.exists():
                    # 目标 .gz 已存在，直接删除未压缩版本
                    legacy.unlink()
                    continue
                try:
                    with open(legacy, "rb") as src, gzip.open(gz_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    legacy.unlink()
                except Exception as e:
                    # 压缩失败保留原文件，避免数据丢失
                    print(f"[sdk_log_cleanup] WARN 压缩残留归档失败 {legacy}: {e}", file=sys.stderr)
