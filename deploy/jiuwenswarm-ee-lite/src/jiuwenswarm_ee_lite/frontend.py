# coding: utf-8
"""前端构建 + 进程拉起（管理面 manager-web / 用户面 user-web）。

**直接使用源码位置原地构建**——不做目录复制、不做缓存布局:

- 管理面 manager-web 源码: ``<外层仓>/applications/manager/manager_web``
- 用户面 user-web 源码:   先自动 git clone jiuwenclaw 到
  ``<本仓>/resources/jiuwenswarm/``（地址/分支由 LITE_JIUWENCLAW_URL /
  LITE_JIUWENCLAW_BRANCH 配置,或写项目根 ``.env``）,再用其中
  ``jiuwenswarm/channels/web/frontend`` 原地构建

命令语义::

    frontend build [--force]   确保 jiuwenclaw 克隆就位 + 各源码目录
                               npm install + npm run build
                               （dist 已存在跳过,--force 重建）
    frontend start             重新构建 + 拉起两个生产 Python 入口
                               （与 K8s 镜像同语义,单机免 nginx）:
        manager_web → ``python -m manager_server.manager_web``（FastAPI 静态+反代:
                      /api→manager /idp→identity /chat→用户面前端
                      /gateway-api,/file-api,/share-api,/ws→gateway）
        user_web    → ``python -m jiuwenswarm.channels.web.app_web``（标准库实现
                      静态+反代+WS 隧道:/api,/ws→gateway 统一 Web 口;
                      语义对齐 docker/web.nginx.conf.template）
    frontend stop / status     停止 / 状态
                               （进程也并入主 ``status`` 服务表；从未构建过的不出行）

端口: 管理面前端 19003 / 用户面前端 19012（LITE_*_FRONTEND_PORT 可改）。
进程簿记独立于核心五服务: ``<home>/frontend.pid.json``。

注意:
- 构建直接发生在源码目录（node_modules/dist 留在源码处,不再搬运）。
- jiuwenclaw 克隆注入 ``GIT_LFS_SKIP_SMUDGE=1``：远端缺视频素材的 LFS
  对象,正常 smudge 会让整个克隆失败（与 uv sync 同坑）;跳过后视频留
  指针文件,不影响前端构建。已存在时跳过克隆,不自动 pull。
- 用户面前端 ``npm run build`` 前注入 ``VITE_BASE_PATH=./`` 与
  ``VITE_API_PREFIX=``（``_USER_WEB_VITE_ENV``）——等价于
  ``VITE_BASE_PATH="./" VITE_API_PREFIX="" npm run build``,缺省构建
  产物用户面白屏,此组合实测修复。
- 用户面前端的 vite 配置在**加载期**解析包内 ``../../../scripts`` 兄弟
  目录（即 ``resources/jiuwenswarm/jiuwenswarm/scripts``）,克隆天然包含;
  缺失时构建前预检并给出清晰报错。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .config import LiteConfig
from .launcher import status as _stack_status
from .launcher import start as _stack_start
from .launcher import stop as _stack_stop
from .spec import ServiceSpec

FRONTEND_ORDER = ("manager_web", "user_web")
_NAME_ALIASES = {"manager_web": "manager_web", "user_web": "user_web",
                 "manager": "manager_web", "user": "user_web"}

_NPM_DEFAULT_REGISTRY = "https://registry.npmmirror.com"   # 对齐 Dockerfile.web 缺省

# 用户面前端构建期注入（等价于 `VITE_BASE_PATH="./" VITE_API_PREFIX="" npm run build`）:
# 缺省构建产物用户面白屏;资源按相对路径引用 + 空 API 前缀后正常。
_USER_WEB_VITE_ENV = {"VITE_BASE_PATH": "./", "VITE_API_PREFIX": ""}


class FrontendError(RuntimeError):
    pass


# ---------------------------------------------------------------- 源码定位
def _source_dir(cfg: LiteConfig, name: str) -> Path:
    """前端源码目录（直接原地使用,不复制）。"""
    return (cfg.manager_frontend_src if name == "manager_web"
            else cfg.user_frontend_src)


def _user_repo_root(cfg: LiteConfig) -> Path:
    """jiuwenclaw 克隆根（用户面前端源码所在仓）。"""
    return cfg.repo_root / "resources" / "jiuwenswarm"


def _git() -> str:
    git = shutil.which("git")
    if not git:
        raise FrontendError("未找到 git,请先安装 Git")
    return git


def _ensure_user_repo(cfg: LiteConfig) -> Path:
    """确保 jiuwenclaw 克隆就位于 ``<repo_root>/resources/jiuwenswarm``。

    克隆地址/分支取 ``cfg.jiuwenclaw_url`` / ``cfg.jiuwenclaw_branch``
    （env ``LITE_JIUWENCLAW_URL`` / ``LITE_JIUWENCLAW_BRANCH`` 或项目根
    ``.env`` 可改,默认 cyz95 fork 的 develop）。缺失时
    ``git clone --depth 1``;克隆子进程注入 ``GIT_LFS_SKIP_SMUDGE=1``——
    远端缺视频素材的 LFS 对象,正常 smudge 会让整个克隆失败（uv sync
    同坑）,跳过后视频留指针文件不影响前端。已存在时跳过,不自动 pull
    （更新: 删目录重跑,或自行 ``git pull``）。
    """
    repo = _user_repo_root(cfg)
    if (repo / ".git").is_dir():
        print(f"[user_web] jiuwenclaw 克隆已存在,跳过克隆: {repo}")
        return repo
    if repo.exists():
        raise FrontendError(
            f"[user_web] 克隆目标已存在但不是 git 仓库: {repo}\n"
            "  请手动清理该目录后重跑,或把 jiuwenclaw 完整克隆至此")
    repo.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    _run([_git(), "clone", "--depth", "1", "-b", cfg.jiuwenclaw_branch,
          cfg.jiuwenclaw_url, str(repo)], env=env)
    return repo


def _sibling_scripts_ok(src: Path) -> bool:
    """vite 配置在加载期解析 ``../../../scripts`` 兄弟目录,缺它必失败。"""
    return (src / ".." / ".." / ".." / "scripts").resolve().is_dir()


# ---------------------------------------------------------------- 子命令与构建
def _run(cmd: list[str], *, cwd: Path | None = None,
         env: dict[str, str] | None = None, capture: bool = False) -> None:
    """跑子命令;capture=True 时回收输出并在失败时把 stderr 尾部带进异常。"""
    shown = " ".join(str(c) for c in cmd)
    print(f"+ {shown}")
    try:
        proc = subprocess.run(
            [str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=env,
            capture_output=capture, text=True, encoding="utf-8",
            errors="replace")
    except FileNotFoundError as exc:
        raise FrontendError(f"命令不存在: {cmd[0]} ({exc})") from None
    if capture and proc.stdout and proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.returncode != 0:
        detail = ""
        if capture and proc.stderr and proc.stderr.strip():
            detail = "\n" + "\n".join(proc.stderr.strip().splitlines()[-12:])
        raise FrontendError(f"命令失败(exit={proc.returncode}): {shown}{detail}")


def _npm() -> str:
    npm = shutil.which("npm")
    if not npm:
        raise FrontendError("未找到 npm,请先安装 Node.js(≥18)")
    return npm


def _npm_env(name: str) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("NPM_CONFIG_REGISTRY", _NPM_DEFAULT_REGISTRY)
    if name == "user_web":
        env.update(_USER_WEB_VITE_ENV)
    return env


def _build_dir(src: Path, env: dict[str, str]) -> None:
    _run([_npm(), "install", "--no-audit", "--no-fund"], cwd=src, env=env)
    _run([_npm(), "run", "build"], cwd=src, env=env)


def build(cfg: LiteConfig, names: list[str] | None = None,
          force: bool = False) -> list[str]:
    """在源码目录原地 npm install + npm run build（dist 已存在跳过,--force 重建）。"""
    built: list[str] = []
    for name in _resolve_names(names):
        env = _npm_env(name)
        if name == "user_web":
            _ensure_user_repo(cfg)
        src = _source_dir(cfg, name)
        if not (src / "package.json").is_file():
            raise FrontendError(
                f"[{name}] 前端源码不存在: {src}\n"
                f"  管理面前端在外层仓 applications/manager/manager_web;"
                f"用户面前端应在 jiuwenclaw 克隆（{_user_repo_root(cfg)}）内")
        if name == "user_web" and not _sibling_scripts_ok(src):
            raise FrontendError(
                f"[{name}] vite 构建需要包内 scripts 兄弟目录: "
                f"{(src / '..' / '..' / '..' / 'scripts').resolve()} 缺失——"
                "克隆不完整,删除后重跑以重新 git clone")
        if (src / "dist").is_dir() and not force:
            print(f"[{name}] dist 已存在,跳过构建(--force 重建): {src / 'dist'}")
            built.append(name)
            continue
        _build_dir(src, env)
        print(f"[{name}] 构建完成: {src / 'dist'}")
        built.append(name)
    return built


# ---------------------------------------------------------------- serve specs
def build_frontend_specs(cfg: LiteConfig) -> list[ServiceSpec]:
    """两个前端进程的启动规格(dist 未构建则报错并提示)。"""
    py = cfg.python
    specs: list[ServiceSpec] = []
    dists = {
        "manager_web": _source_dir(cfg, "manager_web") / "dist",
        "user_web": _source_dir(cfg, "user_web") / "dist",
    }
    for name, dist in dists.items():
        if not dist.is_dir():
            raise FrontendError(
                f"{name} 未构建({dist} 缺失),先执行: "
                "jiuwenswarm-lite frontend build")

    specs.append(ServiceSpec(
        name="manager_web",
        cmd=[
            py, "-m", "manager_server.manager_web",
            "--host", "127.0.0.1",
            "--port", str(cfg.manager_frontend_port),
            "--dist", str(dists["manager_web"]),
            "--proxy-target", cfg.manager_url,
            "--idp-target", cfg.identity_url,
            "--user-web-target", cfg.user_frontend_url,
            "--gateway-http-target", cfg.gateway_url,
            "--gateway-ws-target", f"ws://127.0.0.1:{cfg.gateway_web_port}",
        ],
        env={},
        port=cfg.manager_frontend_port,
        ready_path="/",
        cwd=cfg.home,
    ))
    specs.append(ServiceSpec(
        name="user_web",
        cmd=[
            py, "-m", "jiuwenswarm.channels.web.app_web",
            "--host", "127.0.0.1",
            "--port", str(cfg.user_frontend_port),
            "--dist", str(dists["user_web"]),
            "--proxy-target", cfg.gateway_url,
        ],
        env={
            # 生产同款 env(web.template.yaml):代理目标=gateway 统一 Web 口,
            # 逐请求运行时配置注入用 edition/transport/auth-simulate。
            "GATEWAY_URL": cfg.gateway_url,
            "WEB_PORT": str(cfg.gateway_web_port),
            "JIUWENSWARM_EDITION": "enterprise",
            "WEB_TRANSPORT": "websocket",
            "LOGIN_AUTH_SIMULATE": "false",
            "JIUWENSWARM_DATA_DIR": str(cfg.user_web_workspace),
            "JIUWENSWARM_HOME": str(cfg.user_web_workspace),
        },
        port=cfg.user_frontend_port,
        ready_path="/",
        cwd=cfg.user_web_workspace,
    ))
    return specs


# ---------------------------------------------------------------- 启停(独立簿记)
def _resolve_names(names: list[str] | None) -> list[str]:
    if not names:
        return list(FRONTEND_ORDER)
    resolved: list[str] = []
    for raw in names:
        name = _NAME_ALIASES.get(raw.strip().lower())
        if name is None:
            raise FrontendError(
                f"未知前端服务 {raw!r}(可选: {', '.join(FRONTEND_ORDER)})")
        resolved.append(name)
    return resolved


def _pid_file(cfg: LiteConfig) -> Path:
    return cfg.home / "frontend.pid.json"


def _filter_specs(specs: list[ServiceSpec],
                  names: list[str] | None) -> list[ServiceSpec]:
    if not names:
        return specs
    keep = set(_resolve_names(names))
    matched = [s for s in specs if s.name in keep]
    if not matched:
        raise FrontendError(
            f"未匹配前端服务: {sorted(keep)}(可选: {', '.join(FRONTEND_ORDER)})")
    return matched


async def start(cfg: LiteConfig, names: list[str] | None = None) -> list[str]:
    """拉起前端进程(幂等;dist 缺失报错)。build 请显式先行或用 CLI。"""
    specs = _filter_specs(build_frontend_specs(cfg), names)
    cfg.user_web_workspace.mkdir(parents=True, exist_ok=True)
    return await _stack_start(
        cfg, [s.name for s in specs], specs=specs,
        pid_file=_pid_file(cfg))


async def stop(cfg: LiteConfig, names: list[str] | None = None) -> list[str]:
    return await _stack_stop(
        cfg, _resolve_names(names), pid_file=_pid_file(cfg))


def _specs_allowing_missing(cfg: LiteConfig) -> list[ServiceSpec]:
    """stop/status 用:不强求 dist 在位(没构建过的前端不进表)。"""
    specs: list[ServiceSpec] = []
    for name in FRONTEND_ORDER:
        try:
            specs.extend(_filter_specs(build_frontend_specs(cfg), [name]))
        except FrontendError:
            continue
    return specs


async def status(cfg: LiteConfig) -> list[dict[str, object]]:
    return await _stack_status(
        cfg, specs=_specs_allowing_missing(cfg), pid_file=_pid_file(cfg))
