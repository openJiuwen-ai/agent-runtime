# coding: utf-8
"""轻量版全局配置：端口 / 目录 / 开关（环境变量驱动，全部有缺省）。

jiuwenswarm-ee-lite 单机进程版：四个服务进程（identity → manager →
runtime → gateway）+ runtime 按需拉起的 agentserver 子进程，全部共用本配置
推导出的数据目录与端口表。改这里即改全栈拓扑。

环境变量（均有缺省，可不配）：
- JIUWENSWARM_EE_LITE_HOME   数据根目录（默认 ``./data``，相对启动 cwd）
- LITE_IDENTITY_PORT         身份中心端口（默认 8770）
- LITE_MANAGER_PORT          管理面 REST 端口（默认 8765）
- LITE_RUNTIME_PORT          runtime 编排端口（默认 8091）
- LITE_GATEWAY_WEB_PORT      gateway Web/HTTP 端口（默认 19000）
- LITE_GATEWAY_TUI_PORT      gateway TUI/ACP 端口（默认 19001）
- LITE_USER_WEB_PORT         用户面占位页端口（默认 19002）
- LITE_MANAGER_FRONTEND_PORT 管理面前端端口（默认 19003）
- LITE_USER_FRONTEND_PORT    用户面前端端口（默认 19012）
- LITE_ADMIN_PASSWORD        播种管理员 admin 的密码（默认 admin123）
- LITE_USER1_PASSWORD        播种普通用户 user1 的密码（默认 user1pass）
- LITE_JIUWENCLAW_URL        用户面前端 jiuwenclaw 克隆地址（frontend build 自动
                             克隆用;默认 https://gitcode.com/openjiuwen/jiuwenclaw.git）
- LITE_JIUWENCLAW_BRANCH     jiuwenclaw 克隆分支（默认 develop）

以上变量也可写在项目根 ``.env``（python-dotenv 注入，真实环境变量优先），
样例见 ``.env.example``。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        # G.ERR.11: 函数内不用 SystemExit/sys.exit——改抛 ValueError,由 CLI
        # 入口统一捕获转成 ERROR 输出与退出码。
        raise ValueError(f"invalid {name}={raw!r}: 须为整数") from None


def _env_str(name: str, default: str) -> str:
    return (os.getenv(name) or "").strip() or default


@dataclass(frozen=True)
class LiteConfig:
    """轻量版拓扑配置（一次解析，四处引用）。"""

    home: Path                 # 数据根（DB/日志/工作区全在下面）
    identity_port: int
    manager_port: int
    runtime_port: int
    gateway_web_port: int
    gateway_tui_port: int
    user_web_port: int
    manager_frontend_port: int
    user_frontend_port: int
    admin_password: str
    user1_password: str
    jiuwenclaw_url: str       # 用户面前端 jiuwenclaw 克隆地址
    jiuwenclaw_branch: str    # jiuwenclaw 克隆分支

    @classmethod
    def from_env(cls) -> "LiteConfig":
        # 项目根 .env 先注入（python-dotenv 缺省不覆盖已存在的真实环境变量）,
        # 其后所有 LITE_* 读取对 .env 与真实 env 一视同仁。
        load_dotenv(Path.cwd() / ".env")
        home = Path(_env_str("JIUWENSWARM_EE_LITE_HOME", "./data")).resolve()
        return cls(
            home=home,
            identity_port=_env_int("LITE_IDENTITY_PORT", 8770),
            manager_port=_env_int("LITE_MANAGER_PORT", 8765),
            runtime_port=_env_int("LITE_RUNTIME_PORT", 8091),
            gateway_web_port=_env_int("LITE_GATEWAY_WEB_PORT", 19000),
            gateway_tui_port=_env_int("LITE_GATEWAY_TUI_PORT", 19001),
            user_web_port=_env_int("LITE_USER_WEB_PORT", 19002),
            manager_frontend_port=_env_int("LITE_MANAGER_FRONTEND_PORT", 19003),
            user_frontend_port=_env_int("LITE_USER_FRONTEND_PORT", 19012),
            admin_password=_env_str("LITE_ADMIN_PASSWORD", "admin"),
            user1_password=_env_str("LITE_USER1_PASSWORD", "user1"),
            jiuwenclaw_url=_env_str(
                "LITE_JIUWENCLAW_URL", "https://gitcode.com/openjiuwen/jiuwenclaw.git"),
            jiuwenclaw_branch=_env_str("LITE_JIUWENCLAW_BRANCH", "develop"),
        )

    # -------------------------------------------------------------- 目录布局
    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def gateway_workspace(self) -> Path:
        """gateway 进程自身工作区（JIUWENSWARM_DATA_DIR）。"""
        return self.home / "gateway_workspace"

    @property
    def agentserver_pods_root(self) -> Path:
        """runtime 拉起的 agentserver 子进程工作区根。"""
        return self.home / "agentserver_pods"

    @property
    def repo_root(self) -> Path:
        """本包所在仓（applications/jiuwenswarm-ee-lite）的根。

        优先按进程 CWD 探测（CLI/pytest 约定都在项目根执行）；CWD 不是
        本项目根时回退到包安装位置。这样即便 venv 是从别的检出复制来的
        （editable pth 仍指原检出），在副本目录里运行时解析的也是副本仓。
        可用 ``JIUWENSWARM_EE_LITE_PROJECT_ROOT`` 显式指定。
        """
        override = os.getenv("JIUWENSWARM_EE_LITE_PROJECT_ROOT")
        if override:
            return Path(override).resolve()
        cwd = Path.cwd()
        if (cwd / "pyproject.toml").is_file() and (
                cwd / "src" / "jiuwenswarm_ee_lite").is_dir():
            return cwd
        return Path(__file__).resolve().parents[2]

    @property
    def manager_frontend_src(self) -> Path:
        """管理面前端源码（外层仓 applications/manager/manager_web,原地构建）。

        本仓现为外层 agent-runtime 仓下的嵌套检出
        （``<外层仓>/deploy/jiuwenswarm-ee-lite``）,manager_web 在外层仓的
        ``applications/manager/manager_web``。
        """
        return (self.repo_root.parent.parent / "applications" / "manager"
                / "manager_web")

    @property
    def user_frontend_src(self) -> Path:
        """用户面前端源码（本仓 resources/jiuwenswarm 下的 jiuwenclaw 克隆内,
        原地构建;克隆缺失时由 frontend.build 自动执行 git clone）。

        布局: ``<repo_root>/resources/jiuwenswarm/<jiuwenclaw 克隆>``,frontend
        在其 ``jiuwenswarm/channels/web/frontend``;vite 加载期还要解析
        包内 ``jiuwenswarm/scripts`` 兄弟目录,克隆需包含该路径。
        """
        return (self.repo_root / "resources" / "jiuwenswarm" / "jiuwenswarm"
                / "channels" / "web" / "frontend")

    @property
    def user_web_workspace(self) -> Path:
        """用户面前端进程自身工作区（JIUWENSWARM_DATA_DIR，独立防互踩）。"""
        return self.home / "user_web_workspace"

    def db_file(self, service: str) -> Path:
        """各服务独立 SQLite 文件（identity/manager/agent_runtime/gateway）。"""
        return self.home / f"{service}.db"

    # -------------------------------------------------------------- 服务地址
    @property
    def identity_url(self) -> str:
        return f"http://127.0.0.1:{self.identity_port}"

    @property
    def manager_url(self) -> str:
        return f"http://127.0.0.1:{self.manager_port}"

    @property
    def runtime_url(self) -> str:
        return f"http://127.0.0.1:{self.runtime_port}"

    @property
    def gateway_url(self) -> str:
        return f"http://127.0.0.1:{self.gateway_web_port}"

    @property
    def user_web_url(self) -> str:
        return f"http://127.0.0.1:{self.user_web_port}"

    @property
    def manager_frontend_url(self) -> str:
        return f"http://127.0.0.1:{self.manager_frontend_port}"

    @property
    def user_frontend_url(self) -> str:
        return f"http://127.0.0.1:{self.user_frontend_port}"

    @property
    def python(self) -> str:
        """本 venv 的 python（所有服务进程共用同一解释器与依赖集）。"""
        override = os.getenv("JIUWENSWARM_EE_LITE_PYTHON")
        if override:
            return str(Path(override).resolve())
        import sys

        return sys.executable
