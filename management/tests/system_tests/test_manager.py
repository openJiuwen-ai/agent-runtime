# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""部署管理器测试"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from openjiuwen_runtime.foundation.db.table_def import ColumnDefinition, TableDefinition
from openjiuwen_runtime.foundation.log import get_logger
from openjiuwen_runtime.management import (
    DeployAgentParams,
    DeploymentManager,
    DeployMode,
)
from openjiuwen_runtime.management.deployments.base.models import DeployResult
from openjiuwen_runtime.management.models.enums import DeploymentStatus
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from openjiuwen_runtime.foundation.packaging import package_python_to_whl

logger = get_logger(__name__)


class FailingStrategy:
    """用于验证失败会向上抛出的最小策略实现。"""

    def __init__(self):
        self._records: dict[str, dict] = {}
        self._table_def = TableDefinition(
            table_name="failing_strategy",
            columns=[
                ColumnDefinition("id", "integer", primary_key=True, autoincrement=True),
                ColumnDefinition("deployment_id", "string", unique=True, nullable=False),
                ColumnDefinition("version", "string", nullable=False),
            ],
        )

    def get_table_definition(self) -> TableDefinition:
        return self._table_def

    async def create_record(self, db_handler, deployment_id: str, version: str, **kwargs):
        self._records[deployment_id] = {"deployment_id": deployment_id, "version": version, **kwargs}
        return self._records[deployment_id]

    async def get_record(self, db_handler, deployment_id: str):
        return self._records.get(deployment_id)

    async def delete_record(self, db_handler, deployment_id: str) -> bool:
        return self._records.pop(deployment_id, None) is not None

    async def deploy(self, deployment_id: str, db_handler) -> DeployResult:
        return DeployResult(
            success=False,
            deployment_id=deployment_id,
            message="simulated deploy failure",
        )

    async def stop(self, deployment_id: str, db_handler) -> DeployResult:
        return DeployResult(success=True, deployment_id=deployment_id, message="stopped")

    async def get_status(self, deployment_id: str, db_handler) -> DeploymentStatus:
        return DeploymentStatus.FAILED


class ManagerTest(unittest.IsolatedAsyncioTestCase):
    """部署管理器测试"""

    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="deployment_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.venv_base_path = os.path.join(self.temp_dir, "venvs")

        self.db_handler = SQLiteHandler(self.db_path)
        self.manager = DeploymentManager(
            db_handler=self.db_handler,
        )
        await self.manager.initialize()

    async def asyncTearDown(self):
        await self.manager.shutdown()

        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    @unittest.skip("skip")
    async def test_deploy_simple_agent(self):
        """测试部署 simple_agent"""
        project_root = Path(__file__).parent.parent
        simple_agent_dir = project_root / "resources" / "examples" / "simple_agent"

        self.assertTrue(simple_agent_dir.exists(), f"simple_agent directory not found: {simple_agent_dir}")

        with tempfile.TemporaryDirectory(prefix="whl_build_") as build_dir:
            whl_path = await package_python_to_whl(
                source_dir=str(simple_agent_dir),
                output_dir=build_dir,
                package_name="simple_agent",
            )

            self.assertTrue(os.path.exists(whl_path), f"WHL file not found: {whl_path}")

            deployment_info = await self.manager.deploy_agent(
                DeployAgentParams(
                    name="test_simple_agent",
                    version="1.0.0",
                    mode=DeployMode.SUBPROCESS,
                    extras={"package_name": "simple_agent", "whl_path": whl_path},
                )
            )

            self.assertIsNotNone(deployment_info)
            self.assertEqual(deployment_info.name, "test_simple_agent")
            self.assertIsNotNone(deployment_info.url)

            import aiohttp
            await asyncio.sleep(3)

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{deployment_info.url}/health", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    self.assertEqual(resp.status, 200)
                    data = await resp.json()
                    self.assertEqual(data.get("status"), "healthy")

            process_info = await self.manager.get_process_info(deployment_info.deployment_id)
            self.assertIsNotNone(process_info)
            self.assertIsNotNone(process_info.pid)

            success = await self.manager.stop_deployment(deployment_info.deployment_id)
            self.assertTrue(success)

            status = await self.manager.get_deployment_status(deployment_info.deployment_id)
            from openjiuwen_runtime.management.models.enums import DeploymentStatus
            self.assertEqual(status, DeploymentStatus.STOPPED)

    # @unittest.skip("skip")
    async def test_deploy_simple_agent_whl(self):
        """测试部署 simple_agent"""
        with tempfile.TemporaryDirectory(prefix="whl_build_") as build_dir:
            project_root = Path(__file__).parent.parent
            whl_path = str(
                project_root / "resources" / "examples" / "simple_agent" / "openjiuwen_agent-1.0.0-py3-none-any.whl")

            deployment_info = await self.manager.deploy_agent(
                DeployAgentParams(
                    name="test_simple_agent",
                    version="1.0.0",
                    mode=DeployMode.SUBPROCESS,
                    extras={"package_name": "simple_agent", "whl_path": whl_path},
                )
            )

            self.assertIsNotNone(deployment_info)
            self.assertEqual(deployment_info.name, "test_simple_agent")
            self.assertIsNotNone(deployment_info.url)

            import aiohttp
            await asyncio.sleep(3)

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{deployment_info.url}/health", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    self.assertEqual(resp.status, 200)
                    data = await resp.json()
                    self.assertEqual(data.get("status"), "healthy")

            process_info = await self.manager.get_process_info(deployment_info.deployment_id)
            self.assertIsNotNone(process_info)
            self.assertIsNotNone(process_info.pid)

            success = await self.manager.stop_deployment(deployment_info.deployment_id)
            self.assertTrue(success)

            status = await self.manager.get_deployment_status(deployment_info.deployment_id)
            from openjiuwen_runtime.management.models.enums import DeploymentStatus
            self.assertEqual(status, DeploymentStatus.STOPPED)

    async def test_deploy_agent_raises_when_strategy_reports_failure(self):
        deployment_id = "failing-deploy-test"
        failing_manager = DeploymentManager(
            db_handler=self.db_handler,
            strategies={DeployMode.SUBPROCESS: FailingStrategy()},
        )
        await self.db_handler.init_table(
            failing_manager._get_strategy(DeployMode.SUBPROCESS).get_table_definition()
        )

        with self.assertRaisesRegex(RuntimeError, "simulated deploy failure"):
            await failing_manager.deploy_agent(
                DeployAgentParams(
                    name="test_failed_agent",
                    version="1.0.0",
                    mode=DeployMode.SUBPROCESS,
                    extras={"deployment_id": deployment_id},
                )
            )

        deployment = await failing_manager.get_deployment(deployment_id)
        self.assertIsNotNone(deployment)
        self.assertEqual(deployment.deployment_status, DeploymentStatus.FAILED)


if __name__ == '__main__':
    a = ManagerTest()
    asyncio.run(a.asyncSetUp())
    result = asyncio.run(a.test_deploy_simple_agent_whl())
    asyncio.run(a.asyncTearDown())
    logger.info("%s", result)
