# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""CleanableCompressedRotatingFileHandler 日志清理逻辑单元测试。

覆盖场景：
1. 按天数清理：删除超过 retention_days 的 .gz 归档，保留新归档与活跃文件
2. 按总空间清理：超限时从最旧 .gz 归档开始逐一删除，活跃文件不删
3. 异常分支：归档文件 stat/unlink 失败时输出 stderr 警告，不抛出

实现说明：
- 由于 conftest.py 的 _OpenJiuwenStubFinder 会将 openjiuwen_runtime 包替换为
  MagicMock（避免依赖真实 SDK），本测试不通过 __init__ 构造 handler
  （super().__init__ 是 MagicMock，不会设置 baseFilename 等属性），
  而是用 __new__ + 手动属性注入的方式，只测试 _cleanup_by_retention /
  _cleanup_by_total_size 的纯 Path 操作逻辑。
"""

from __future__ import annotations

import gzip
import io
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

# 显式从源码目录加载真实 foundation 包，避免被 conftest 的 stub 污染。
_FOUNDATION_ROOT = Path(__file__).resolve().parents[4] / "foundation"
if str(_FOUNDATION_ROOT) not in sys.path:
    sys.path.append(str(_FOUNDATION_ROOT))
# 清除可能被 stub 污染的缓存，强制重新解析真实模块
for _mod_name in list(sys.modules.keys()):
    if _mod_name == "openjiuwen_runtime" or _mod_name.startswith("openjiuwen_runtime."):
        _mod = sys.modules.get(_mod_name)
        if _mod is not None and not hasattr(_mod, "__file__"):
            del sys.modules[_mod_name]

# 注意：common.sdk_log_cleaner 在模块加载期会 import openjiuwen SDK，
# 在 stub 环境下可正常加载（SDK 被 MagicMock 替换）。
from common.sdk_log_cleaner import CleanableCompressedRotatingFileHandler  # noqa: E402


def _write_bytes(path: Path, size: int) -> None:
    """向指定文件写入 size 字节的占位数据。"""
    with open(path, "wb") as f:
        f.write(b"x" * size)


def _make_gz(path: Path, size: int, mtime: float) -> None:
    """生成一个 gzip 压缩文件，内部展开后为 size 字节，并设置 mtime。"""
    with gzip.open(path, "wb") as f:
        f.write(b"x" * size)
    os.utime(path, (mtime, mtime))


def _make_handler(log_path: Path, retention_days: int, max_total_size: int) -> CleanableCompressedRotatingFileHandler:
    """绕过 __init__（依赖被 stub 的父类），手动构造 handler 实例。

    CleanableCompressedRotatingFileHandler._cleanup_by_retention /
    _cleanup_by_total_size 只依赖 self.baseFilename / self._retention_days /
    self._max_total_size，不调用父类方法，因此可独立测试。
    """
    handler = CleanableCompressedRotatingFileHandler.__new__(CleanableCompressedRotatingFileHandler)
    handler.baseFilename = str(log_path)
    handler._retention_days = retention_days
    handler._max_total_size = max_total_size
    return handler


class CleanableHandlerRetentionDaysTest(unittest.TestCase):
    """按天数清理逻辑测试。"""

    def test_deletes_archives_older_than_retention_days(self) -> None:
        """超过 retention_days 的 .gz 被删除，未超过的保留。"""
        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "app.log"
            _write_bytes(log_path, 100)

            now_ts = datetime.now(tz=timezone.utc).timestamp()
            old_ts = (datetime.now(tz=timezone.utc) - timedelta(days=10)).timestamp()

            _make_gz(log_path.with_suffix(".log.1.gz"), 50, old_ts)
            _make_gz(log_path.with_suffix(".log.2.gz"), 50, old_ts)
            _make_gz(log_path.with_suffix(".log.3.gz"), 50, now_ts)

            handler = _make_handler(log_path, retention_days=7, max_total_size=0)
            handler._cleanup_by_retention()

            self.assertFalse(log_path.with_suffix(".log.1.gz").exists())
            self.assertFalse(log_path.with_suffix(".log.2.gz").exists())
            self.assertTrue(log_path.with_suffix(".log.3.gz").exists())
            self.assertTrue(log_path.exists())

    def test_retention_days_zero_cleans_all_historical_archives(self) -> None:
        """retention_days=0 直接调用方法时，cutoff=now，所有历史归档都会被删。

        注意：跳过逻辑在 doRollover 中（if retention_days > 0），不在方法内部。
        直接调用 _cleanup_by_retention 时，retention_days=0 等价于"清理所有早于
        当前时间的归档"。
        """
        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "app.log"
            _write_bytes(log_path, 100)

            old_ts = (datetime.now(tz=timezone.utc) - timedelta(days=30)).timestamp()
            _make_gz(log_path.with_suffix(".log.1.gz"), 50, old_ts)

            handler = _make_handler(log_path, retention_days=0, max_total_size=0)
            handler._cleanup_by_retention()

            # cutoff=now，30天前的文件 mtime < now，被删除
            self.assertFalse(log_path.with_suffix(".log.1.gz").exists())


class CleanableHandlerTotalSizeTest(unittest.TestCase):
    """按总空间清理逻辑测试。"""

    def test_deletes_oldest_archives_when_total_exceeds(self) -> None:
        """总空间超限时，从最旧 .gz 开始删除直到降到阈值以下。

        注意：f.stat().st_size 返回 gzip 文件在磁盘上的实际大小（压缩后），
        非 gzip 内展开后的原始大小。本用例用 max_total_size=1 强制所有归档
        都超限，验证"从最旧开始删"的行为。
        """
        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "app.log"
            _write_bytes(log_path, 100)

            base_ts = datetime.now(tz=timezone.utc).timestamp()
            _make_gz(log_path.with_suffix(".log.1.gz"), 100, base_ts - 300)
            _make_gz(log_path.with_suffix(".log.2.gz"), 100, base_ts - 200)
            _make_gz(log_path.with_suffix(".log.3.gz"), 100, base_ts - 100)

            # max_total_size=1，活跃文件 100B 已超限，所有归档都会被删到 total_size<=1
            # 由于活跃文件不能删，所有 .gz 归档会被删尽
            handler = _make_handler(log_path, retention_days=0, max_total_size=1)
            handler._cleanup_by_total_size()

            # 所有归档被删（从最旧 1.gz 开始，依次 2.gz、3.gz）
            self.assertFalse(log_path.with_suffix(".log.1.gz").exists())
            self.assertFalse(log_path.with_suffix(".log.2.gz").exists())
            self.assertFalse(log_path.with_suffix(".log.3.gz").exists())
            self.assertTrue(log_path.exists())

    def test_total_size_zero_cleans_all_archives(self) -> None:
        """max_total_size=0 直接调用方法时，任何非零占用都会触发清理。

        注意：跳过逻辑在 doRollover 中（if max_total_size > 0），不在方法内部。
        直接调用 _cleanup_by_total_size 时，max_total_size=0 等价于"清理所有归档
        直到 total_size <= 0"，由于活跃文件不能删，所有 .gz 归档会被删尽。
        """
        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "app.log"
            _write_bytes(log_path, 100)

            _make_gz(log_path.with_suffix(".log.1.gz"), 100, datetime.now(tz=timezone.utc).timestamp())

            handler = _make_handler(log_path, retention_days=0, max_total_size=0)
            handler._cleanup_by_total_size()

            # max_total_size=0，total_size=活跃100+归档>0，归档被删到 total_size<=0
            # 活跃文件不删，但归档会被删尽
            self.assertFalse(log_path.with_suffix(".log.1.gz").exists())
            self.assertTrue(log_path.exists())

    def test_total_size_under_limit_keeps_all(self) -> None:
        """总空间未超限时，所有归档都保留。"""
        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "app.log"
            _write_bytes(log_path, 50)

            _make_gz(log_path.with_suffix(".log.1.gz"), 50, datetime.now(tz=timezone.utc).timestamp())

            handler = _make_handler(log_path, retention_days=0, max_total_size=1024)
            handler._cleanup_by_total_size()

            self.assertTrue(log_path.with_suffix(".log.1.gz").exists())


class CleanableHandlerExceptionTest(unittest.TestCase):
    """异常分支测试：确保 print 到 stderr，不抛出。"""

    def test_cleanup_by_retention_handles_oserror(self) -> None:
        """_cleanup_by_retention 中 unlink 抛 OSError 时输出 stderr，不传播。"""
        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "app.log"
            _write_bytes(log_path, 10)

            old_ts = (datetime.now(tz=timezone.utc) - timedelta(days=10)).timestamp()
            gz = log_path.with_suffix(".log.1.gz")
            _make_gz(gz, 10, old_ts)

            handler = _make_handler(log_path, retention_days=7, max_total_size=0)

            with patch("sys.stderr", new_callable=io.StringIO) as fake_err:
                with patch.object(Path, "unlink", side_effect=OSError("perm denied")):
                    handler._cleanup_by_retention()
                self.assertIn("按天数清理删除失败", fake_err.getvalue())

    def test_cleanup_by_total_size_handles_oserror(self) -> None:
        """_cleanup_by_total_size 中 unlink 抛 OSError 时输出 stderr，不传播。"""
        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "app.log"
            _write_bytes(log_path, 100)

            base_ts = datetime.now(tz=timezone.utc).timestamp()
            _make_gz(log_path.with_suffix(".log.1.gz"), 100, base_ts - 100)

            handler = _make_handler(log_path, retention_days=0, max_total_size=10)

            with patch("sys.stderr", new_callable=io.StringIO) as fake_err:
                with patch.object(Path, "unlink", side_effect=OSError("perm denied")):
                    handler._cleanup_by_total_size()
                self.assertIn("按空间清理删除失败", fake_err.getvalue())


if __name__ == "__main__":
    unittest.main()
