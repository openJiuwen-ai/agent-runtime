# coding: utf-8
"""服务规格：每个服务进程的启动命令 + 独立环境变量 + 就绪探针。

五个进程的 env 全部显式落字（对齐 K8s 部署模板 deploy/jiuwenswarm-ee 的
关键变量，DB/缓存全部本地化；userweb 是轻量版自带的用户面占位页）：

- identity: sqlite 库 + 播种 admin/user1（RS256 JWT 签发方）
- manager:  sqlite 库 + REDIS_URL 空（无 Redis，锁免选主单机心跳）
            + IDENTITY_PUBLIC_KEY_URL 指向本机 identity（JWT 验签）
- runtime:  AGENT_RUNTIME_MODE=local（fakeredis + 文件 SQLite + 子进程 Pod）
            + AGENT_RUNTIME_DEPLOY_MODE=subprocess（本机进程级拉起 agentserver）
- gateway:  JIUWENSWARM_EDITION=enterprise + sqlite 企业库 + standalone 内存
            瞬态后端（无 Redis）+ GATEWAY_AGENT_CLIENT_MODE=runtime（会话路由
            交 runtime 编排，不直连静态 agentserver）

未显式列出的 env 一律不注入（子进程继承 launcher 自身环境 + 本规格覆盖），
因此各服务从外部带来的杂散变量（如 AGENT_SERVER_URL）若影响拓扑，由
launcher 注入空串压制——见 _env 关键字。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .config import LiteConfig


def _py_paths(cfg: LiteConfig, parts: tuple[str, ...]) -> str:
    """服务子进程的确定性模块搜索路径（os.pathsep 连接，压过继承环境）。

    此前服务模块靠 ambient PYTHONPATH / venv editable pth 解析——换 shell
    或 pth 指向的检出被移动/删除就 ModuleNotFoundError。全部落字到
    外层仓根（``applications/``、``service/``、``foundation/`` 所在层）
    的相对布局后，服务代码解析只随仓走，与环境无关。
    """
    root = cfg.repo_root.parents[1]
    return os.pathsep.join(str(root / p) for p in parts)


@dataclass(frozen=True)
class ServiceSpec:
    """一个服务进程的完整启动描述。"""

    name: str                     # 进程名（pid 簿记/日志文件名）
    cmd: list[str]                # argv
    env: dict[str, str]           # 额外注入的环境变量（压过继承环境）
    port: int                     # 就绪探针端口
    ready_path: str = ""          # HTTP 就绪路径；空 = 仅 TCP 探活
    cwd: Path | None = None       # 工作目录（默认数据根）
    unset: tuple[str, ...] = ()   # 需要压制（置空串）的继承变量


def _spec(cfg: LiteConfig, name: str) -> ServiceSpec:
    py = cfg.python

    if name == "identity":
        return ServiceSpec(
            name="identity",
            cmd=[py, "-m", "identity_center.main"],
            env={
                "PYTHONPATH": _py_paths(cfg, (
                    "applications/manager/identity_center/src",
                    "applications/manager",
                    "service",
                    "foundation",
                )),
                "IDENTITY_REST_HOST": "127.0.0.1",
                "IDENTITY_REST_PORT": str(cfg.identity_port),
                "IDENTITY_DB_TYPE": "sqlite",
                "IDENTITY_SQLITE_PATH": str(cfg.db_file("identity")),
                "IDENTITY_SEED_ADMIN": "true",
                "IDENTITY_ADMIN_PASSWORD": cfg.admin_password,
                "IDENTITY_SEED_USER1": "true",
                "IDENTITY_USER1_PASSWORD": cfg.user1_password,
            },
            port=cfg.identity_port,
            ready_path="/v1/auth/public_key",   # 公钥端点无鉴权，可当探针
        )

    if name == "userweb":
        return ServiceSpec(
            name="userweb",
            cmd=[py, "-m", "jiuwenswarm_ee_lite.userweb"],
            env={
                # 本项目自身源码（避免依赖 venv editable pth 指到哪个检出）
                "PYTHONPATH": str(cfg.repo_root / "src"),
                "USERWEB_HOST": "127.0.0.1",
                "USERWEB_PORT": str(cfg.user_web_port),
            },
            port=cfg.user_web_port,
            ready_path="/",
        )

    if name == "manager":
        return ServiceSpec(
            name="manager",
            cmd=[py, "-m", "manager_server.main"],
            env={
                "PYTHONPATH": _py_paths(cfg, (
                    "applications/manager/manager_server/src",
                    "applications/manager",
                    "service",
                    "foundation",
                )),
                "MANAGER_REST_HOST": "127.0.0.1",
                "MANAGER_REST_PORT": str(cfg.manager_port),
                "MANAGER_DB_TYPE": "sqlite",
                "MANAGER_SQLITE_PATH": str(cfg.db_file("manager")),
                # JWT 验签：指向本机 identity（缺省即此 URL，显式落字防端口改配漂移）
                "IDENTITY_PUBLIC_KEY_URL":
                    f"{cfg.identity_url}/v1/auth/public_key",
                # REDIS_URL 缺省空 = 单机无锁心跳（真 Redis 留给集群版）
                "REDIS_URL": "",
            },
            port=cfg.manager_port,
            ready_path="/docs",
        )

    if name == "runtime":
        return ServiceSpec(
            name="runtime",
            cmd=[py, "-m", "agent_runtime.cli",
                 "--mode", "local", "--port", str(cfg.runtime_port)],
            env={
                "PYTHONPATH": _py_paths(cfg, (
                    "applications/agent_runtime/src",
                    "service",
                    "foundation",
                )),
                "AGENT_RUNTIME_MODE": "local",
                "AGENT_RUNTIME_SQLITE_PATH": str(cfg.db_file("agent_runtime")),
                # 本机进程级拉起 agentserver（轻量版核心，无 K8s 依赖）
                "AGENT_RUNTIME_DEPLOY_MODE": "subprocess",
                "AGENT_RUNTIME_SUBPROCESS_DATA_ROOT":
                    str(cfg.agentserver_pods_root),
                # pod 模板 env ${VAR} 的展开源：lite-service.json 里
                # GATEWAY_SQLITE_PATH 写 ${GATEWAY_SQLITE_PATH} 占位（种子文件
                # 保持可移植、不写死本机绝对路径），deploy 期由 SubprocessPodClient
                # 展开为本机绝对路径——路径真源在 launcher（随部署位置自动变化）。
                "GATEWAY_SQLITE_PATH": str(cfg.db_file("gateway")),
                "AGENT_RUNTIME_SUBPROCESS_PYTHON": py,
                "AGENT_RUNTIME_SUBPROCESS_MODULE": AGENTSERVER_MODULE,
                # 首启 agentserver 需初始化工作区，就绪等待放宽到 5 分钟
                "AGENT_RUNTIME_SUBPROCESS_READY_TIMEOUT": "300",
                "OPENJIUWEN_SERVICE_HOST": "127.0.0.1",
                "OPENJIUWEN_SERVICE_PORT": str(cfg.runtime_port),
            },
            port=cfg.runtime_port,
            ready_path="/healthz",
        )

    if name == "gateway":
        return ServiceSpec(
            name="gateway",
            cmd=[py, "-m", "gateway.app_gateway"],
            env={
                "WEB_HOST": "127.0.0.1",
                "WEB_PORT": str(cfg.gateway_web_port),
                "GATEWAY_HOST": "127.0.0.1",
                "GATEWAY_PORT": str(cfg.gateway_tui_port),
                "JIUWENSWARM_EDITION": "enterprise",
                # 企业版 ephemeral 降级内存后端（单机无 Redis；jiuwenclaw
                # gateway/storage_assembly/setup.py 的 env 门控分支）
                "GATEWAY_EPHEMERAL_BACKEND": "memory",
                "JIUWENSWARM_DATA_DIR": str(cfg.gateway_workspace),
                # 防工作区 .env override=True 抢走端口类 env（CLI 启动器模式）
                "JIUWENSWARM_CLI_PORTS": "1",
                "GATEWAY_DB_TYPE": "sqlite",
                "GATEWAY_SQLITE_PATH": str(cfg.db_file("gateway")),
                # cron 工厂硬性检查该 env 非空（sqlite 下 host 无语义，仅开关）
                "GATEWAY_DB_HOST": "127.0.0.1",
                # 会话路由交 runtime 编排（不直连静态 agentserver）
                "GATEWAY_AGENT_CLIENT_MODE": "runtime",
                "GATEWAY_RUNTIME_MANAGER_URL": cfg.runtime_url,
                "GATEWAY_MANAGER_HTTP_URL": cfg.manager_url,
                # 配置下发走 manager HTTP 推送；单机无需共享库轮询
                "GATEWAY_CONFIG_POLL_ENABLED": "false",
                # runtime↔gateway 链路 mTLS 本机关闭（默认 off，显式落字）
                "JIUWENSWARM_LINK_MTLS_MODE": "off",
            },
            # gateway 若从外部继承静态 agentserver 地址会绕开 runtime 路由——压制
            unset=("AGENT_SERVER_URL",),
            port=cfg.gateway_web_port,
            ready_path="/api/v1/health",
        )

    raise ValueError(f"unknown service: {name}")


SERVICE_ORDER = ("identity", "userweb", "manager", "runtime", "gateway")


# agentserver 子进程入口模块（runtime 拉起与 status 进程感知共用同一标记）
AGENTSERVER_MODULE = "jiuwenswarm.server.app_agentserver"


def build_specs(cfg: LiteConfig, services: list[str] | None = None) -> list[ServiceSpec]:
    """按依赖顺序构造启动规格（identity → manager → runtime → gateway）。"""
    names = list(services) if services else list(SERVICE_ORDER)
    return [_spec(cfg, n) for n in names]
