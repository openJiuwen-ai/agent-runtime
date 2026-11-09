# coding: utf-8
"""RM 的进程级 Pod 实现（AGENT_RUNTIME_DEPLOY_MODE=subprocess）：本机子进程拉起 AgentServer。

轻量单机形态（jiuwenswarm-ee-lite）：无 K8s 依赖，pod_spec 容器规范形翻译为
子进程命令 + 环境变量。对上层（RM orchestrator/sweeper）完整实现 K8sPodClient
契约，语义逐字对齐 K8s：

- deploy(pod_spec)：声明端口逐个分配本机空闲口 → 模板 env 为底、进程覆盖为顶
  （端口 env 映射、独立工作区；模板 env 值支持 ``${VAR}`` 引用，展开自 runtime
  进程自身环境——部署期解析，pod_spec 快照里不必写死随部署位置变化的绝对路径）→
  Popen（日志落文件）→ TCP 探活等待就绪 →
  PodDeployInfo(pod_ip=127.0.0.1, published_port=实际 SSE 口)。进程已建但未就绪
  时不私吞：上抛 DeployFailed 携带 pod_id/namespace，交上层既有孤儿清理兜底
  delete（与 K8s「fail_after_create 留物理 Pod」语义一致）。
- delete()：进程树终止（Windows taskkill /F /T；POSIX 进程组 SIGTERM→SIGKILL），
  幂等（NotFound 安全）。
- get_pod/list_pods：进程存活 = Running/Ready（sweeper 巡检/watch/reconcile 用）。

拉起做法沿用 management.session.process_service_handler.ProcessServiceHandler 的
成熟实现（引导环境白名单、CREATE_NEW_PROCESS_GROUP/setsid、taskkill/killpg），
差异：端口由模板声明改为动态分配（单机多实例），工作区每 Pod 独立
（JIUWENSWARM_DATA_DIR/HOME 隔离，防 GatewayLock/配置文件互踩）。

环境变量（均有缺省，可不配）：
- AGENT_RUNTIME_SUBPROCESS_PYTHON            python 可执行文件（默认 sys.executable）
- AGENT_RUNTIME_SUBPROCESS_MODULE            启动模块（默认 jiuwenswarm.server.app_agentserver）
- AGENT_RUNTIME_SUBPROCESS_LAUNCHER_SCRIPT   启动脚本路径（设置则优先于 module）
- AGENT_RUNTIME_SUBPROCESS_PORT_ENV_MAP      端口名→环境变量映射 JSON
  （默认 {"sse": "AGENT_HTTP_PORT", "ws": "AGENT_SERVER_PORT"}：SSE/HTTP 门与
  WS 门各拿一个独立空闲口；映射目标无论模板是否声明都分配，防多进程撞默认口）
- AGENT_RUNTIME_SUBPROCESS_DATA_ROOT         每 Pod 工作区根目录（默认 ./agentserver_pods）
- AGENT_RUNTIME_SUBPROCESS_READY_TIMEOUT     TCP 就绪等待超时秒（默认 120）

模板 command/args 是容器镜像入口（本机无该镜像语义），不参与翻译；启动命令
固定由上面三个变量决定。镜像/挂载/资源/quota 等 K8s 专属字段忽略（记 DEBUG）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import re
import socket
import string
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..containers import (
    default_main_container,
    main_health_path,
    normalize_pod_spec,
)
from ..errors import DeployFailed
from .models import POD_LABEL_KEY, POD_LABEL_VALUE, PodDeployInfo, PodInfo

logger = logging.getLogger("agent_runtime.resource_manager.subprocess")

DEFAULT_READY_TIMEOUT = 120.0       # deploy 等 TCP 就绪超时（秒）
DEFAULT_READY_POLL_INTERVAL = 0.5   # 就绪轮询间隔（秒）
DEFAULT_MODULE = "jiuwenswarm.server.app_agentserver"
DEFAULT_PORT_ENV_MAP = {"sse": "AGENT_HTTP_PORT", "ws": "AGENT_SERVER_PORT"}

# 引导环境白名单（与 management ProcessServiceHandler 同源）：不继承网关业务环境，
# 只保留 Python 子进程启动所需系统变量。
_SUBPROCESS_BOOTSTRAP_KEYS = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "PYTHONHOME",
    "VIRTUAL_ENV",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
)


def _subprocess_bootstrap_env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key in _SUBPROCESS_BOOTSTRAP_KEYS and value
    }


def _random_suffix(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _pick_free_port() -> int:
    """向内核要一个空闲 TCP 口（绑定后立即释放；标准做法，竞态窗口可接受）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_port_env_map() -> dict[str, str]:
    raw = (os.getenv("AGENT_RUNTIME_SUBPROCESS_PORT_ENV_MAP") or "").strip()
    if not raw:
        return dict(DEFAULT_PORT_ENV_MAP)
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and k and v
        for k, v in parsed.items()
    ):
        raise ValueError(
            "AGENT_RUNTIME_SUBPROCESS_PORT_ENV_MAP must be a JSON object "
            f"of port-name -> env-var strings, got {raw!r}")
    return {k: v for k, v in parsed.items()}


# 模板 env 值的 ${VAR} 引用（语义对齐 K8s $(VAR)：仅 env 值，不递归，
# 不展开 command/args）。pod_spec 是管理面下发的静态快照，单机部署里
# "随部署位置变化"的值（如与 gateway 共享的 sqlite 路径）不应在配置里
# 写死绝对路径——launcher 把绝对值注入 runtime 进程 env，模板里写
# ${VAR} 占位，deploy 期在此展开。展开源 = runtime 进程自身环境
# （launcher 注入的 spec env 也在其中）；解析不到保留原样并记 WARNING，
# 让问题在 pod 日志/env 排查里可见而不是静默拿到字面量路径。
_ENV_REF_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env_refs(value: str) -> str:
    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        resolved = os.environ.get(name)
        if resolved is None:
            logger.warning(
                "pod template env ${%s} not resolvable in runtime process "
                "env; kept literal", name)
            return match.group(0)
        return resolved

    return _ENV_REF_PATTERN.sub(_sub, value)


class _Proc:
    """一个已拉起 AgentServer 进程的本地簿记。"""

    def __init__(
        self,
        *,
        popen: subprocess.Popen[Any],
        namespace: str,
        sse_port: int,
        health_path: str,
        workspace: Path,
        log_file: Path,
    ) -> None:
        self.popen = popen
        self.namespace = namespace
        self.sse_port = sse_port
        self.health_path = health_path
        self.workspace = workspace
        self.log_file = log_file
        self.created_at = time.time()

    @property
    def alive(self) -> bool:
        return self.popen.poll() is None

    def terminate(self) -> None:
        proc = self.popen
        if proc.poll() is not None:
            return
        if sys.platform == "win32":
            system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
            taskkill = str(Path(system_root) / "System32" / "taskkill.exe")
            subprocess.run(
                [taskkill, "/F", "/T", "/PID", str(proc.pid)],
                shell=False,
                capture_output=True,
                text=True,
            )
        else:
            import signal as _signal

            try:
                os.killpg(os.getpgid(proc.pid), _signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), _signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()


class SubprocessPodClient:
    """K8sPodClient 的进程级实现（deploy_mode=subprocess）。"""

    def __init__(
        self,
        *,
        default_namespace: str = "default",
        host: str = "127.0.0.1",
        ready_timeout: float | None = None,
        ready_poll_interval: float = DEFAULT_READY_POLL_INTERVAL,
    ) -> None:
        self.default_namespace = default_namespace
        self.host = host
        self.ready_timeout = (
            float(ready_timeout) if ready_timeout is not None
            else float(os.getenv("AGENT_RUNTIME_SUBPROCESS_READY_TIMEOUT")
                       or DEFAULT_READY_TIMEOUT)
        )
        self.ready_poll_interval = float(ready_poll_interval)
        self.data_root = Path(
            os.getenv("AGENT_RUNTIME_SUBPROCESS_DATA_ROOT")
            or "./agentserver_pods").resolve()
        self._pods: dict[tuple[str, str], _Proc] = {}   # (namespace, pod_id) → book

    # -------------------------------------------------------------- 生命周期

    async def start(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)

    async def close(self) -> None:
        for key in list(self._pods):
            await self.delete(key[1], key[0])

    # -------------------------------------------------------------- K8sPodClient 契约

    async def deploy(self, pod_spec: dict[str, Any]) -> PodDeployInfo:
        norm = normalize_pod_spec(dict(pod_spec))
        main = norm.get("main_container")
        if not isinstance(main, dict) or not main.get("image"):
            main = default_main_container()
        namespace = pod_spec.get("namespace") or self.default_namespace
        pod_id = f"agentserver-{_random_suffix()}"
        workspace = self.data_root / "pods" / pod_id / "workspace"
        log_file = self.data_root / "pods" / pod_id / "agent.log"
        workspace.mkdir(parents=True, exist_ok=True)
        log_file.parent.mkdir(parents=True, exist_ok=True)

        env, sse_port = self.build_env(main, workspace=workspace)
        cmd = self._build_command(env)

        creation_flags = 0
        preexec_fn = None
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            preexec_fn = os.setsid

        log_handle = log_file.open("a", encoding="utf-8")
        try:
            popen = subprocess.Popen(
                cmd,
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
                preexec_fn=preexec_fn,
                cwd=str(workspace),
            )
        finally:
            log_handle.close()

        book = _Proc(
            popen=popen,
            namespace=namespace,
            sse_port=sse_port,
            health_path=main_health_path(main),
            workspace=workspace,
            log_file=log_file,
        )
        self._pods[(namespace, pod_id)] = book
        logger.info(
            "process deploy: pod=%s pid=%s sse_port=%s ws_port=%s module=%s "
            "workspace=%s log=%s",
            pod_id, popen.pid, book.sse_port,
            env.get("AGENT_SERVER_PORT", "-"),
            os.getenv("AGENT_RUNTIME_SUBPROCESS_MODULE") or DEFAULT_MODULE,
            workspace, log_file,
        )
        try:
            await self._wait_until_ready(book)
        except BaseException as exc:
            # 与 K8s「create 成功但未 Ready」同语义：进程留在簿记，异常携带
            # pod_id/namespace 交上层孤儿清理兜底 delete（不在此私吞终止）。
            failure = DeployFailed(
                f"agentserver subprocess not ready within {self.ready_timeout}s: "
                f"pod={pod_id} error={exc}")
            failure.pod_id = pod_id
            failure.namespace = namespace
            logger.warning(
                "process deploy not ready: pod=%s pid=%s error=%s",
                pod_id, popen.pid, exc)
            raise failure from exc
        logger.info(
            "process deploy ready: pod=%s pid=%s sse_port=%s",
            pod_id, popen.pid, book.sse_port)
        return PodDeployInfo(
            pod_id=pod_id,
            namespace=namespace,
            pod_ip=self.host,
            published_port=book.sse_port,
        )

    async def delete(self, pod_id: str, namespace: str) -> str:
        book = self._pods.pop((namespace, pod_id), None)
        if book is None:
            return pod_id     # NotFound 安全（幂等）
        book.terminate()
        logger.info(
            "process delete 完成: pod=%s pid=%s", pod_id, book.popen.pid)
        return pod_id

    async def get_pod(self, pod_id: str, namespace: str) -> PodInfo | None:
        book = self._pods.get((namespace, pod_id))
        if book is None:
            return None
        return self._pod_info(pod_id, namespace, book)

    async def list_pods(self, namespace: str, label_selector: str) -> list[PodInfo]:
        return [
            self._pod_info(pod_id, ns, book)
            for (ns, pod_id), book in self._pods.items()
            if ns == namespace and _matches_selector(label_selector)
        ]

    async def probe_pod_health(
        self,
        *,
        pod_id: str,
        namespace: str,
        pod_ip: str,
        sse_port: int,
        health_path: str = "/health",
        timeout: float = 2.0,
    ) -> bool:
        """进程形态不探测 SSE 半死：直接返回健康。

        进程死活由 sweeper 的 dead-pod 扫描经 ``get_pod`` phase 判定兜底
        （``_Proc`` 在册、poll() 感知退出）；K8s 的"Running 但 SSE hang"
        场景对子进程不适用，跳过 HTTP 探测。
        """
        return True

    # -------------------------------------------------------------- 内部

    def _pod_info(self, pod_id: str, namespace: str, book: _Proc) -> PodInfo:
        alive = book.alive
        return PodInfo(
            pod_id=pod_id,
            namespace=namespace,
            phase="Running" if alive else "Failed",
            ready=alive,
            pod_ip=self.host,
            labels={POD_LABEL_KEY: POD_LABEL_VALUE, "app": pod_id},
            reason="" if alive else f"exited code={book.popen.poll()}",
        )

    def build_env(
        self, main: dict[str, Any], *, workspace: Path,
    ) -> tuple[dict[str, str], int]:
        """模板 env 为底 + 进程覆盖为顶。返回 (进程 env, SSE 实际端口)。"""
        env = _subprocess_bootstrap_env()
        template_env = main.get("env") or {}
        for key, value in template_env.items():
            if isinstance(value, str):
                env[key] = _expand_env_refs(value)
        unknown_env_from = main.get("env_from")
        if unknown_env_from:
            # K8s Secret/ConfigMap envFrom 无本地语义：记 WARNING 不阻断。
            logger.warning(
                "env_from not supported in subprocess deploy mode, ignored: %s",
                unknown_env_from)

        # 端口分配：每个映射目标独立空闲口（无论模板是否声明同名端口，
        # 都要避免多进程撞各自默认口）。
        port_env_map = _read_port_env_map()
        declared = {
            str(p.get("name")): int(p.get("container_port"))
            for p in main.get("ports") or [] if p.get("name")
        }
        sse_port = 0
        for port_name, env_var in port_env_map.items():
            port = _pick_free_port()
            env[env_var] = str(port)
            if port_name == "sse":
                sse_port = port
            elif port_name in declared and not sse_port:
                sse_port = port
        if not sse_port:
            # 自定义映射未覆盖 sse：兜底一个 AGENT_HTTP_PORT。
            sse_port = _pick_free_port()
            env.setdefault("AGENT_HTTP_PORT", str(sse_port))

        # 进程级覆盖（必须压过模板 env）：
        env["AGENT_SERVER_HOST"] = self.host
        # 每 Pod 独立工作区（同机多实例隔离，防 GatewayLock/配置互踩）。
        env["JIUWENSWARM_DATA_DIR"] = str(workspace)
        env.setdefault("JIUWENSWARM_HOME", str(workspace))
        env.setdefault("HOME", str(workspace))
        if sys.platform == "win32":
            # Windows 部分三方库经 USERPROFILE 解析 home（对齐 ProcessServiceHandler）。
            env.setdefault("USERPROFILE", str(workspace))
        # SSE/HTTP 门是 gateway 直连契约（published_port），模板未显式开启时兜底打开；
        # 模板显式 false 则告警（gateway 将无法直连该 Pod）。
        env.setdefault("AGENT_HTTP_ENABLED", "true")
        if str(template_env.get("AGENT_HTTP_ENABLED", "")).lower() in (
                "false", "0", "off", "no"):
            logger.warning(
                "template env disables AGENT_HTTP_ENABLED; gateway SSE direct "
                "connect will fail for pods of this template")
        return env, sse_port

    @staticmethod
    def _build_command(env: dict[str, str]) -> list[str]:
        python = (os.getenv("AGENT_RUNTIME_SUBPROCESS_PYTHON")
                  or sys.executable).strip()
        launcher_script = (
            os.getenv("AGENT_RUNTIME_SUBPROCESS_LAUNCHER_SCRIPT") or "").strip()
        if launcher_script:
            return [python, launcher_script, "--port",
                    env.get("AGENT_SERVER_PORT", "0")]
        module = (os.getenv("AGENT_RUNTIME_SUBPROCESS_MODULE")
                  or DEFAULT_MODULE).strip()
        return [python, "-m", module, "--port",
                env.get("AGENT_SERVER_PORT", "0")]

    async def _wait_until_ready(self, book: _Proc) -> None:
        """TCP 探活（对齐 ProcessServiceHandler）：端口可连即就绪，不做 WS 握手
        （enforce 鉴权下无 token 握手会被拒，TCP 与 K8s readiness 探针同语义）。"""
        deadline = asyncio.get_running_loop().time() + self.ready_timeout
        last_error: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            if not book.alive:
                raise RuntimeError(
                    f"agentserver process exited early with code "
                    f"{book.popen.returncode} (log: {book.log_file})")
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, book.sse_port),
                    timeout=2.0,
                )
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                await asyncio.sleep(self.ready_poll_interval)
        raise TimeoutError(
            f"agentserver not ready within {self.ready_timeout}s "
            f"host={self.host} port={book.sse_port} last_error={last_error}")


def _matches_selector(selector: str) -> bool:
    # 本客户端只创建自家 label 的 Pod；非本组件 selector（如 sidecar 清理目标）
    # 一律空集，语义与 K8s label 过滤一致。
    return selector in ("", POD_LABEL_KEY + "=" + POD_LABEL_VALUE)
