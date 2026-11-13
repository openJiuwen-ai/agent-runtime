# coding: utf-8
"""e2e 公共夹具：全栈就绪 + 各服务客户端 + 播种账号令牌。

栈复用 ``jiuwenswarm-lite`` 的幂等启动（已在跑的服务跳过），测试会话结束
不拆栈（便于本地反复调试；显式 ``jiuwenswarm-lite stop`` 清场）。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import click
import httpx
import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    # G.PSL.03: 不用 insert(0,...) 抢占路径首位(避免遮蔽同名模块);追加兜底——
    # 包已随 uv 安装时优先命中安装副本,未安装的检出场景也能兜底导入。
    sys.path.append(str(SRC))

from jiuwenswarm_ee_lite.config import LiteConfig  # noqa: E402
from jiuwenswarm_ee_lite.launcher import start as stack_start  # noqa: E402
from jiuwenswarm_ee_lite.spec import POD_COMPAT_DIR  # noqa: E402

from .helpers.identity_client import IdentityClient  # noqa: E402

GATEWAY_CHANNEL_CONFIG_GRACE_S = 300.0  # 首启"可选通道配置"阻塞窗口（实测 ~3min）

_STALE_INSTANCE_PREFIXES = ("e2e-", "chat-")   # 本套 e2e 建实例的名称前缀


def _purge_stale_test_instances(cfg: LiteConfig) -> None:
    """清掉上次异常中断的测试会话残留的实例（尽力而为，失败不阻塞）。

    e2e 实例都登记同一个 gateway_host，manager 有唯一性校验——上次会话
    teardown 没跑完（Ctrl+C/进程被杀）就会让后续所有建实例的 fixture
    在 setup 阶段 400（gateway_host already in use）。按名称前缀识别
    本套 e2e 的实例，栈就绪后先清场。
    """
    try:
        identity = IdentityClient(cfg.identity_url)
        token = identity.login("admin", cfg.admin_password)["access_token"]
        with httpx.Client(
                base_url=cfg.manager_url, timeout=30.0,
                headers={"Authorization": f"Bearer {token}"}) as api:
            resp = api.get("/api/v1/instances/",
                           params={"page": 1, "page_size": 200})
            resp.raise_for_status()
            data = resp.json().get("data")
            rows = data.get("items") if isinstance(data, dict) else (data or [])
            for row in rows or []:
                name = str((row or {}).get("jiuwenclaw_name") or "")
                jid = str((row or {}).get("jiuwenclaw_id") or "")
                if jid and name.startswith(_STALE_INSTANCE_PREFIXES):
                    api.delete(f"/api/v1/instances/{jid}")
                    click.echo(f"[conftest] 清理残留测试实例: {name} ({jid})")
    except Exception as exc:   # noqa: BLE001 - 清场失败不挡本次测试
        click.echo(f"[conftest] 残留实例清理跳过: {exc}")


@pytest.fixture(scope="session")
def cfg() -> LiteConfig:
    return LiteConfig.from_env()


@pytest.fixture(scope="session")
def stack(cfg: LiteConfig) -> LiteConfig:
    """全栈就绪（幂等）；gateway 就绪后再等通道配置窗口过去。"""
    import asyncio

    asyncio.run(stack_start(cfg))
    deadline = time.monotonic() + GATEWAY_CHANNEL_CONFIG_GRACE_S
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{cfg.gateway_url}/api/health", timeout=5).status_code < 500:
                _purge_stale_test_instances(cfg)
                return cfg
        except httpx.HTTPError:
            pass
        time.sleep(2.0)
    raise RuntimeError(f"gateway not serving within {GATEWAY_CHANNEL_CONFIG_GRACE_S}s")


@pytest.fixture(scope="session")
def identity(stack: LiteConfig) -> IdentityClient:
    return IdentityClient(stack.identity_url)


@pytest.fixture(scope="session")
def admin_token(cfg: LiteConfig, identity: IdentityClient) -> str:
    """播种管理员 admin 的 access token（密码同 launcher 注入：LITE_ADMIN_PASSWORD）。"""
    return identity.login("admin", cfg.admin_password)["access_token"]


@pytest.fixture(scope="session")
def user1_token(identity: IdentityClient) -> str:
    """播种普通用户 user1 的 access token。"""
    return identity.login("user1", "user1pass")["access_token"]


@pytest.fixture(scope="session")
def manager_api(cfg: LiteConfig, admin_token: str) -> httpx.Client:
    """manager 管理面客户端（admin 身份）。"""
    return httpx.Client(
        base_url=cfg.manager_url,
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=30.0,
    )


# ---------------------------------------------------- 聊天链路夹具(test_02/03 共用)
@pytest.fixture(scope="session")
def mock_llm():
    from .helpers.mock_llm import MockLLM

    llm = MockLLM()
    llm.start()
    yield llm
    llm.stop()


@pytest.fixture()
def chat_setup(manager_api, cfg, mock_llm):
    """实例 + 容器模板(模型凭证 env)+ 服务模板/资源(runtime 部署)+ agent 模板/资源(gateway 准入)。"""
    import uuid

    body = {
        "jiuwenclaw_name": f"chat-{uuid.uuid4().hex[:8]}",
        "gateway_host": cfg.gateway_url,
        "runtime_host": cfg.runtime_url,
        "user_web_host": cfg.user_web_url,
        "gateway_web_http_host": cfg.gateway_url,
    }
    r = manager_api.post("/api/v1/instances/", json=body)
    if r.status_code != 200:   # G.TES.01: 不用 assert(优化编译下会被剥除)
        raise AssertionError(f"create instance: HTTP {r.status_code}: {r.text}")
    jid = r.json()["data"]["jiuwenclaw_id"]

    suffix = uuid.uuid4().hex[:8]
    ct = manager_api.post("/api/v1/container-templates", json={
        "template_name": f"chat-container-{suffix}",
        "container_id": f"chat-agent-{suffix}",
        "name": "agent",
        "image": "local/agentserver:e2e",
        "ports": [{"name": "sse", "containerPort": 18092}],
        # 模型凭证走管理面下发（agentserver 子进程的 OpenAI 兼容配置）
        "env": [
            {"name": "JIUWENSWARM_EDITION", "value": "enterprise"},
            {"name": "JIUWENSWARM_CONFIG_SOURCE", "value": "enterprise"},
            # pod 兼容垫片（sitecustomize：checkpointer 企业模式回退本地 sqlite,
            # 新版 workswarm 硬性要求 MySQL/PG 共享库,轻量形态无外部 DB）
            {"name": "PYTHONPATH", "value": str(POD_COMPAT_DIR)},
            {"name": "API_BASE", "value": mock_llm.base_url},
            {"name": "API_KEY", "value": "sk-e2e-mock"},
            {"name": "MODEL_NAME", "value": "mock-model"},
            {"name": "MODEL_PROVIDER", "value": "OpenAI"},
        ],
    })
    if ct.status_code != 200:
        raise AssertionError(f"create container-template: HTTP {ct.status_code}: {ct.text}")
    container_id = ct.json()["data"]["container_id"]

    st = manager_api.post("/api/v1/service-config-templates", json={
        "template_name": f"chat-service-{suffix}",
        "main_container_id": container_id,
        "min_idle_pods": 0,
    })
    if st.status_code != 200:
        raise AssertionError(f"create service-template: HTTP {st.status_code}: {st.text}")
    template_id = st.json()["data"]["template_id"]

    sr = manager_api.post(f"/api/v1/instances/{jid}/service-resources", json={
        "resource_name": f"chat-resource-{suffix}",
        "ref_template_id": template_id,
        "match_exprs": ["user_id == 'user1'"],
    })
    if sr.status_code != 200:
        raise AssertionError(f"create service-resource: HTTP {sr.status_code}: {sr.text}")
    resource_id = sr.json()["data"]["items"][0]["resource_id"]

    # Agent 模板 + 实例 Agent 资源：gateway 侧聊天准入（bot_id=resource_id,
    # match_expr 校验 user_id）——manager 先推 gateway 成功再落库。
    at = manager_api.post("/api/v1/agent-templates/", json={
        "template_name": f"chat-agent-tpl-{suffix}",
        "template_ref": {},
        "enabled": True,
    })
    if at.status_code != 200:
        raise AssertionError(f"create agent-template: HTTP {at.status_code}: {at.text}")
    agent_template_id = at.json()["data"]["template_id"]
    ar = manager_api.post(f"/api/v1/instances/{jid}/agent-resources", json={
        "resource_name": f"chat-bot-{suffix}",
        "ref_template_id": agent_template_id,
        "match_exprs": ["user_id == 'user1'"],
    })
    if ar.status_code != 200:
        raise AssertionError(f"create agent-resource: HTTP {ar.status_code}: {ar.text}")
    bot_id = ar.json()["data"]["items"][0]["resource_id"]

    yield {"jid": jid, "resource_id": resource_id, "bot_id": bot_id}

    manager_api.delete(
        f"/api/v1/instances/{jid}/agent-resources/{bot_id}")
    manager_api.delete(f"/api/v1/agent-templates/{agent_template_id}")
    manager_api.delete(f"/api/v1/instances/{jid}/service-resources/{resource_id}")
    manager_api.delete(f"/api/v1/service-config-templates/{template_id}")
    manager_api.delete(f"/api/v1/container-templates/{container_id}")
    manager_api.delete(f"/api/v1/instances/{jid}")
