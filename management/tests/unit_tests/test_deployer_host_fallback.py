# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""部署器 endpoint host 回退测试。

settings.IP 是可选的“对外可达地址”配置；未设置时必须回退到部署上下文
（ctx.host / deployer.default_host），否则 Docker 部署会把 None 当主机名
扫描端口（永远找不到）而 Subprocess 部署会返回 ``http://None:<port>/``。
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen_runtime.management.deployments.base.models import CommonParams, DeployContext
from openjiuwen_runtime.management.deployments.docker import deployer as docker_module
from openjiuwen_runtime.management.deployments.docker.deployer import DockerDeployer
from openjiuwen_runtime.management.deployments.docker.models import DockerParams
from openjiuwen_runtime.management.deployments.subprocess import deployer as subprocess_module
from openjiuwen_runtime.management.deployments.subprocess.deployer import LocalSubprocessDeployer
from openjiuwen_runtime.management.deployments.subprocess.models import SubprocessParams


def _docker_context(tmp_path: Path) -> DeployContext:
    return DeployContext(
        common=CommonParams(deployment_id="dep-docker", host=None),
        params=DockerParams(whl_path=str(tmp_path / "agent-0.1.0.whl"), package_name="agent"),
    )


class TestDockerDeployerEndpointHost(unittest.TestCase):
    def test_uses_context_host_when_ip_is_unset(self):
        deployer = DockerDeployer(default_host="localhost")
        deployer.deploy_lowcode_container = AsyncMock(return_value="container-id")
        real_find = docker_module.find_available_port

        def _small_scan(host):
            return real_find(host=host, start_port=8000, end_port=8010)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(docker_module, "find_available_port", _small_scan), patch.object(
                docker_module, "settings", SimpleNamespace(IP=None)
            ):
                result = asyncio.run(deployer.deploy(_docker_context(Path(tmp))))

        self.assertTrue(result.success, result.message)
        self.assertNotIn("No available port found", result.message)
        self.assertTrue(result.url.startswith("http://localhost:"), result.url)
        deployer.deploy_lowcode_container.assert_awaited_once()

    def test_keeps_ip_when_configured(self):
        deployer = DockerDeployer(default_host="localhost")
        deployer.deploy_lowcode_container = AsyncMock(return_value="container-id")
        seen = {}

        def _fake_scan(host):
            seen["host"] = host
            return 8123

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(docker_module, "find_available_port", _fake_scan), patch.object(
                docker_module, "settings", SimpleNamespace(IP="10.20.30.40")
            ):
                result = asyncio.run(deployer.deploy(_docker_context(Path(tmp))))

        self.assertTrue(result.success, result.message)
        self.assertEqual(seen["host"], "10.20.30.40")
        self.assertEqual(result.url, "http://10.20.30.40:8123")


class _FakePopen:
    pid = 4321

    def poll(self):
        return None


class TestSubprocessDeployerEndpointHost(unittest.TestCase):
    def test_url_uses_context_host_when_ip_is_unset(self):
        deployer = LocalSubprocessDeployer(default_host="127.0.0.1")
        deployer.venv_manager = MagicMock()
        deployer.venv_manager.pip_install.return_value = True

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            venv_path = tmp_path / "dep-subprocess" / ".venv"
            whl_path = tmp_path / "agent-0.1.0.whl"
            whl_path.write_bytes(b"fake")
            deployer.venv_manager.create_venv.return_value = venv_path
            deployer.venv_manager.get_python_executable.return_value = Path("/usr/bin/python3")

            ctx = DeployContext(
                common=CommonParams(deployment_id="dep-subprocess", host=None),
                params=SubprocessParams(whl_path=str(whl_path), package_name="agent"),
            )
            with patch.object(
                subprocess_module,
                "settings",
                SimpleNamespace(IP=None, MODE="product", dist_path=tmp_path),
            ), patch.object(subprocess_module.subprocess, "Popen", return_value=_FakePopen()), patch.object(
                subprocess_module.asyncio, "sleep", AsyncMock()
            ):
                result = asyncio.run(deployer.deploy(ctx))

        self.assertTrue(result.success, result.message)
        self.assertNotIn("None", result.url)
        self.assertTrue(result.url.startswith("http://127.0.0.1:"), result.url)
