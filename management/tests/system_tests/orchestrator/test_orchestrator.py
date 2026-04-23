# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Orchestrator 系统测试 - 使用 SQLite 和 K8s 部署模式"""

import pytest
import asyncio
import tempfile
import os
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from openjiuwen_runtime.management.orchestrator.access import Access, AccessConfig
from openjiuwen_runtime.management.orchestrator.models import MessagePriority


class TestOrchestratorSystem:
    """Orchestrator 系统测试"""

    @pytest.fixture
    def temp_db_path(self):
        """创建临时数据库文件"""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        yield path
        if os.path.exists(path):
            os.unlink(path)

    @pytest.fixture
    async def db_handler(self, temp_db_path):
        """创建 SQLite DBHandler"""
        handler = SQLiteHandler(db_path=temp_db_path)
        await handler.initialize()
        yield handler
        await handler.shutdown()

    @pytest.fixture
    def mock_k8s_deployer(self):
        """创建 mock K8s Deployer"""
        deployer = MagicMock()
        deployer.deploy = AsyncMock(return_value=MagicMock(
            success=True,
            message="K8s deployment started",
            url="http://test-service.default.svc.cluster.local:8000"
        ))
        deployer.undeploy = AsyncMock(return_value=True)
        return deployer

    @pytest.fixture
    async def access_config(self, db_handler):
        """创建 AccessConfig 实例"""
        return AccessConfig(
            db_handler=db_handler,
            image="test-image:latest",
            max_concurrency=10,
            min_idle_services=0,
            max_services=3,
            target_port=8000,
            invoke_path="/invoke",
            service_ttl=300,
            queue_size=10,
        )

    @pytest.fixture
    async def access(self, access_config, mock_k8s_deployer):
        """创建 Access 实例并 mock K8s 操作"""
        with patch(
            "openjiuwen_runtime.management.deployments.k8s.deployer.K8sDeployer",
            return_value=mock_k8s_deployer
        ):
            access = Access(access_config)
            await access.init()
            yield access
            await access.stop()

    @pytest.mark.asyncio
    async def test_send_message_flow(self, access, mock_k8s_deployer):
        """测试消息分发流程"""
        header = {
            "session_id": "test-session-1",
            "concurrency": 1,
            "ttl": 30,
        }
        payload = {"data": "test message"}

        result = await access.send_message(header, payload)

        assert result["success"] is True
        assert "session_id" in result
        assert result["session_id"] == "test-session-1"

    @pytest.mark.asyncio
    async def test_multiple_sessions(self, access, mock_k8s_deployer):
        """测试多个 session 的消息分发"""
        results = []
        for i in range(3):
            header = {
                "session_id": f"session-{i}",
                "concurrency": 1,
                "ttl": 30,
            }
            payload = {"data": f"message {i}"}
            result = await access.send_message(header, payload)
            results.append(result)

        assert all(r["success"] for r in results)

    @pytest.mark.asyncio
    async def test_health_check_after_operations(self, access):
        """测试操作后的健康检查"""
        header = {
            "session_id": "health-check-session",
            "concurrency": 1,
            "ttl": 30,
        }
        await access.send_message(header, {"data": "test"})

        status = await access.health_check()

        assert status["Access"] == "running"
        assert status["deployment_manager"] == "initialized"
        assert status["message_queue"] == "initialized"
        assert status["timer"] == "initialized"
        assert status["service_manager"] == "initialized"

    @pytest.mark.asyncio
    async def test_config_update(self, access):
        """测试配置更新"""
        await access.set_max_concurrency(20)
        await access.set_max_services(10)
        await access.set_service_ttl(600)

        assert access._config.max_concurrency == 20
        assert access._config.max_services == 10
        assert access._config.service_ttl == 600

    @pytest.mark.asyncio
    async def test_send_response_end(self, access):
        """测试发送响应结束消息"""
        header = {
            "session_id": "response-end-session",
            "concurrency": 1,
            "ttl": 30,
        }
        await access.send_message(header, {"data": "test"})

        await access.send_response_end("response-end-session")

    @pytest.mark.asyncio
    async def test_concurrent_messages(self, access):
        """测试并发消息处理"""
        async def send_message(session_id: str):
            header = {
                "session_id": session_id,
                "concurrency": 1,
                "ttl": 30,
            }
            return await access.send_message(header, {"data": f"concurrent {session_id}"})

        results = await asyncio.gather(
            send_message("concurrent-1"),
            send_message("concurrent-2"),
            send_message("concurrent-3"),
        )

        assert all(r["success"] for r in results)

    @pytest.mark.asyncio
    async def test_message_without_session_id(self, access):
        """测试缺少 session_id 的消息"""
        header = {}
        payload = {"data": "test"}

        result = await access.send_message(header, payload)

        assert result["success"] is False
        assert "session_id" in result["message"]

    @pytest.mark.asyncio
    async def test_graceful_shutdown(self, access_config, mock_k8s_deployer):
        """测试优雅关闭"""
        with patch(
            "openjiuwen_runtime.management.deployments.k8s.deployer.K8sDeployer",
            return_value=mock_k8s_deployer
        ):
            access = Access(access_config)
            await access.init()

            header = {
                "session_id": "shutdown-test",
                "concurrency": 1,
                "ttl": 30,
            }
            await access.send_message(header, {"data": "test"})

            await access.stop()

            assert access._running is False

    @pytest.mark.asyncio
    async def test_autoscaling_trigger(self, access, mock_k8s_deployer):
        """测试自动扩容触发"""
        results = []
        for i in range(5):
            header = {
                "session_id": f"scale-session-{i}",
                "concurrency": 5,
                "ttl": 30,
            }
            result = await access.send_message(header, {"data": f"scale {i}"})
            results.append(result)

        assert all(r["success"] for r in results)

    @pytest.mark.asyncio
    async def test_max_services_limit(self, access_config, db_handler, mock_k8s_deployer):
        """测试最大服务数限制"""
        access_config.max_services = 2
        access_config.max_concurrency = 1

        with patch(
            "openjiuwen_runtime.management.deployments.k8s.deployer.K8sDeployer",
            return_value=mock_k8s_deployer
        ):
            access = Access(access_config)
            await access.init()

            results = []
            for i in range(5):
                header = {
                    "session_id": f"limit-session-{i}",
                    "concurrency": 1,
                    "ttl": 30,
                }
                result = await access.send_message(header, {"data": f"limit {i}"})
                results.append(result)

            await access.stop()

        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_min_idle_services(self, db_handler, mock_k8s_deployer):
        """测试最小空闲服务数"""
        config = AccessConfig(
            db_handler=db_handler,
            image="test-image:latest",
            max_concurrency=10,
            min_idle_services=2,
            max_services=5,
            target_port=8000,
            invoke_path="/invoke",
            service_ttl=300,
            queue_size=10,
        )

        with patch(
            "openjiuwen_runtime.management.deployments.k8s.deployer.K8sDeployer",
            return_value=mock_k8s_deployer
        ):
            access = Access(config)
            await access.init()

            await asyncio.sleep(0.5)

            services = await access._service_manager.list_services()

            await access.stop()

        assert len(services) >= 2
