# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from unittest import TestCase
from unittest import mock

from openjiuwen_runtime.cli.main import _shutdown_manager


class _FakeManager:
    def __init__(self):
        self.shutdown = mock.AsyncMock()


class TestShutdownManagerIdempotency(TestCase):
    """_shutdown_manager 应以 manager 实例为粒度幂等，而非模块级一次性标记。"""

    def test_each_manager_shutdown_once(self):
        first = _FakeManager()
        second = _FakeManager()

        _shutdown_manager(first)
        _shutdown_manager(first)

        self.assertEqual(first.shutdown.await_count, 1)

        # 同一进程内再次调用 CLI 产生的 manager 仍应被关闭
        _shutdown_manager(second)
        self.assertEqual(second.shutdown.await_count, 1)
