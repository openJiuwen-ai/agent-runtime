# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

import asyncio
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
