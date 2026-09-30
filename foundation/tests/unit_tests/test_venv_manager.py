# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest import mock

from openjiuwen_runtime.foundation.venv_manager import VirtualEnvironmentManager

_UV_RESOLVER = "openjiuwen_runtime.foundation.venv_manager._resolve_uv_executable"
_SUBPROCESS_RUN = "openjiuwen_runtime.foundation.venv_manager.subprocess.run"


class TestCreateVenvFailureCleanup(TestCase):
    """create_venv 失败时必须清理半成品 venv，避免下次调用误判为已创建。"""

    def test_timeout_removes_partial_venv(self):
        manager = VirtualEnvironmentManager()
        with tempfile.TemporaryDirectory() as tmp:
            venv_path = Path(tmp) / "deploy-timeout" / ".venv"

            def fake_run(cmd, *args, **kwargs):
                # 模拟 uv 已创建部分目录后超时
                venv_path.mkdir(parents=True, exist_ok=True)
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=300)

            with mock.patch.object(manager, "get_venv_path", return_value=venv_path), \
                    mock.patch(_UV_RESOLVER, return_value="uv"), \
                    mock.patch(_SUBPROCESS_RUN, side_effect=fake_run):
                with self.assertRaises(RuntimeError):
                    manager.create_venv("deploy-timeout")

            self.assertFalse(venv_path.exists())

    def test_missing_python_executable_removes_partial_venv(self):
        manager = VirtualEnvironmentManager()
        with tempfile.TemporaryDirectory() as tmp:
            venv_path = Path(tmp) / "deploy-partial" / ".venv"

            def fake_run(cmd, *args, **kwargs):
                # 目录已建立但缺少解释器，随后应整体清理
                venv_path.mkdir(parents=True, exist_ok=True)
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with mock.patch.object(manager, "get_venv_path", return_value=venv_path), \
                    mock.patch(_UV_RESOLVER, return_value="uv"), \
                    mock.patch(_SUBPROCESS_RUN, side_effect=fake_run):
                with self.assertRaises(RuntimeError):
                    manager.create_venv("deploy-partial")

            self.assertFalse(venv_path.exists())
