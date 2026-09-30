# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

import asyncio
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase

from openjiuwen_runtime.management.deployments.subprocess.deployer import (
    LocalSubprocessDeployer,
)
from openjiuwen_runtime.management.models.enums import DeploymentStatus


class _FakePopen:
    """模拟同步 subprocess.Popen：returncode 仅在 poll() 后才会刷新。"""

    def __init__(self, pid: int = 4321):
        self.pid = pid
        self.returncode = None
        self._exited = False
        self.poll_calls = 0

    def exit_naturally(self) -> None:
        self._exited = True

    def poll(self):
        self.poll_calls += 1
        if self._exited:
            self.returncode = 0
        return self.returncode


class _StoppablePopen:
    """模拟同步 Popen，记录 terminate/kill，wait 支持一次超时。"""

    def __init__(self, pid: int = 5678, timeout_first: bool = True):
        self.pid = pid
        self.terminated = False
        self.killed = False
        self._timeout_first = timeout_first
        self._wait_calls = 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout=None) -> int:
        self._wait_calls += 1
        if self._timeout_first and self._wait_calls == 1:
            raise subprocess.TimeoutExpired(cmd="agent", timeout=timeout)
        return 0


class TestGetStatusRefreshesReturncode(TestCase):
    def test_natural_exit_detected_as_stopped(self):
        deployer = LocalSubprocessDeployer()
        process = _FakePopen()
        deployer._processes["dep-1"] = process

        self.assertEqual(
            asyncio.run(deployer.get_status("dep-1")),
            DeploymentStatus.RUNNING,
        )

        # 进程自然退出后 returncode 尚未被读取过，get_status 需通过 poll() 刷新
        process.exit_naturally()

        self.assertEqual(
            asyncio.run(deployer.get_status("dep-1")),
            DeploymentStatus.STOPPED,
        )
        self.assertGreaterEqual(process.poll_calls, 2)

    def test_running_process_stays_running(self):
        deployer = LocalSubprocessDeployer()
        process = _FakePopen()
        deployer._processes["dep-2"] = process

        self.assertEqual(
            asyncio.run(deployer.get_status("dep-2")),
            DeploymentStatus.RUNNING,
        )


class TestStopRegisteredProcess(TestCase):
    """登记同步 Popen 后 stop() 的优雅分支必须成功返回并完成清理。"""

    def _deployer_with(self, process) -> LocalSubprocessDeployer:
        deployer = LocalSubprocessDeployer()
        deployer._processes["dep-stop"] = process
        return deployer

    def test_graceful_stop_succeeds_and_cleans_up(self):
        process = _StoppablePopen(timeout_first=False)
        deployer = self._deployer_with(process)
        with tempfile.TemporaryDirectory() as tmp:
            venv_path = Path(tmp) / "venv"
            venv_path.mkdir()

            result = asyncio.run(deployer.stop("dep-stop", venv_path=str(venv_path)))

            self.assertTrue(result.success)
            self.assertTrue(process.terminated)
            self.assertFalse(process.killed)
            self.assertNotIn("dep-stop", deployer._processes)
            self.assertFalse(venv_path.exists())

    def test_stop_kills_when_terminate_times_out(self):
        process = _StoppablePopen(timeout_first=True)
        deployer = self._deployer_with(process)
        with tempfile.TemporaryDirectory() as tmp:
            venv_path = Path(tmp) / "venv"
            venv_path.mkdir()

            result = asyncio.run(deployer.stop("dep-stop", venv_path=str(venv_path)))

            self.assertTrue(result.success)
            self.assertTrue(process.terminated)
            self.assertTrue(process.killed)
            self.assertNotIn("dep-stop", deployer._processes)
            self.assertFalse(venv_path.exists())
