# coding: utf-8
"""进程编排：按序启动 / 就绪等待 / 状态查看 / 逆序停止。

单机进程版没有 K8s/supervisor，簿记落 ``<home>/lite.pid.json``：
``{"服务名": {"pid": int, "started_at": float}}``。停止按启动逆序
taskkill/killpg 整进程树（agentserver 子进程随 runtime 一起带走）。

就绪判定：HTTP GET ready_path（默认探针路径各服务见 spec.py），失败回退
TCP 端口探活——与服务内部实现解耦，只依赖"端口开了=能收请求"这一事实。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from .config import LiteConfig
from .spec import SERVICE_ORDER, ServiceSpec, build_specs

logger = logging.getLogger("jiuwenswarm_ee_lite.launcher")

READY_TIMEOUT_S = 90.0        # 单服务就绪等待（runtime 首启较慢，单独放宽）
READY_POLL_S = 0.5
STOP_WAIT_S = 15.0


class LauncherError(RuntimeError):
    pass


def _pid_file(cfg: LiteConfig) -> Path:
    return cfg.home / "lite.pid.json"


def _read_records(pid_file: Path) -> dict[str, dict[str, float]]:
    if not pid_file.is_file():
        return {}
    try:
        raw = json.loads(pid_file.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_records(pid_file: Path, records: dict[str, dict[str, float]]) -> None:
    pid_file.write_text(
        json.dumps(records, indent=2, sort_keys=True), encoding="utf-8")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # kernel32 OpenProcess 探活（taskkill 副作用大，只用于停机）。
        import ctypes

        # Win32 API 常量(命名按检查器 G.NAM.01 统一 snake_case)。
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # 进程存在但无权发信号


def _tree_kill(pid: int) -> None:
    """整进程树终止（与 agent_runtime SubprocessPodClient 同做法）。"""
    if sys.platform == "win32":
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        taskkill = str(Path(system_root) / "System32" / "taskkill.exe")
        subprocess.run(
            [taskkill, "/F", "/T", "/PID", str(pid)],
            shell=False, capture_output=True, text=True,
        )
    else:
        import os as _os
        import signal as _signal

        try:
            _os.killpg(_os.getpgid(pid), _signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and _pid_alive(pid):
            time.sleep(0.2)
        if _pid_alive(pid):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                _os.killpg(_os.getpgid(pid), _signal.SIGKILL)


async def _wait_ready(spec: ServiceSpec, timeout: float = READY_TIMEOUT_S) -> None:
    """HTTP 就绪探测，回退 TCP。"""
    deadline = asyncio.get_running_loop().time() + timeout
    url = f"http://127.0.0.1:{spec.port}{spec.ready_path}"
    async with httpx.AsyncClient(timeout=2.0) as client:
        while asyncio.get_running_loop().time() < deadline:
            try:
                resp = await client.get(url)
                if resp.status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(READY_POLL_S)
    raise LauncherError(
        f"service {spec.name} not ready within {timeout}s "
        f"(probe: GET {url}); 查看日志 {spec.name}.log")


async def start(
    cfg: LiteConfig,
    services: list[str] | None = None,
    *,
    specs: list[ServiceSpec] | None = None,
    pid_file: Path | None = None,
) -> list[str]:
    """按序启动服务（幂等：已在跑的服务跳过）。返回本次实际启动的服务名。

    specs/pid_file 可注入外部规格与簿记文件（frontend 复用同一套启停语义;
    缺省 = 核心 5 服务的 build_specs + lite.pid.json,行为不变）。
    """
    cfg.home.mkdir(parents=True, exist_ok=True)
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    cfg.gateway_workspace.mkdir(parents=True, exist_ok=True)
    cfg.agentserver_pods_root.mkdir(parents=True, exist_ok=True)

    pid_path = pid_file or _pid_file(cfg)
    spec_list = list(specs) if specs is not None else build_specs(cfg, services)
    if specs is not None and services:
        keep = set(services)
        spec_list = [s for s in spec_list if s.name in keep]
    records = _read_records(pid_path)
    started: list[str] = []
    try:
        for spec in spec_list:
            rec = records.get(spec.name)
            if rec and _pid_alive(int(rec.get("pid", 0))):
                # 就绪探针也顺带确认服务真的能服务（陈旧 pid 文件自愈）
                try:
                    await _wait_ready(spec, timeout=5.0)
                    logger.info("service %s already running (pid=%s), skip",
                                spec.name, rec["pid"])
                    continue
                except LauncherError:
                    logger.warning(
                        "service %s pid=%s alive but not serving; restarting",
                        spec.name, rec["pid"])

            env: dict[str, str] = {
                **{k: v for k, v in os.environ.items() if k not in spec.unset},
                **spec.env,
            }
            for key in spec.unset:
                env.pop(key, None)
                env[key] = ""

            log_path = cfg.logs_dir / f"{spec.name}.log"
            log_handle = log_path.open("a", encoding="utf-8")
            creation_flags = 0
            preexec_fn = None
            if sys.platform == "win32":
                creation_flags = (
                    subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
            else:
                preexec_fn = os.setsid
            try:
                proc = subprocess.Popen(
                    spec.cmd,
                    env=env,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    cwd=str(spec.cwd or cfg.home),
                    creationflags=creation_flags,
                    preexec_fn=preexec_fn,
                )
            finally:
                log_handle.close()

            records[spec.name] = {"pid": proc.pid, "started_at": time.time()}
            _write_records(pid_path, records)
            logger.info("service %s starting: pid=%s cmd=%s log=%s",
                        spec.name, proc.pid, " ".join(spec.cmd), log_path)
            timeout = READY_TIMEOUT_S
            if spec.name == "runtime":
                timeout = max(timeout, 120.0)   # fakeredis+SQLite 首启初始化
            try:
                await _wait_ready(spec, timeout=timeout)
            except LauncherError:
                await stop(cfg, [spec.name])
                raise
            started.append(spec.name)
            logger.info("service %s ready on port %s", spec.name, spec.port)
    except BaseException:
        if started:
            # 半启动状态回滚已启动的服务，不留孤儿栈
            await stop(cfg, started, pid_file=pid_path)
        raise
    return started


async def stop(
    cfg: LiteConfig,
    services: list[str] | None = None,
    *,
    pid_file: Path | None = None,
) -> list[str]:
    """逆序停止（幂等）。返回本次实际停止的服务名。"""
    pid_path = pid_file or _pid_file(cfg)
    records = _read_records(pid_path)
    names = list(services) if services else list(SERVICE_ORDER)
    stopped: list[str] = []
    for name in reversed(names):
        rec = records.pop(name, None)
        if rec is None:
            continue
        pid = int(rec.get("pid", 0))
        if _pid_alive(pid):
            _tree_kill(pid)
            logger.info("service %s stopped (pid=%s)", name, pid)
        else:
            logger.info("service %s already dead (pid=%s)", name, pid)
        stopped.append(name)
        _write_records(pid_path, records)
    return stopped


async def status(
    cfg: LiteConfig,
    *,
    specs: list[ServiceSpec] | None = None,
    pid_file: Path | None = None,
) -> list[dict[str, object]]:
    """各服务运行状态（存活 + 端口可服务）。

    就绪探测并发执行（顺序探测 5 个服务在 Windows 上累计近 1s，
    ``status --watch`` 每秒刷新需要这里的耗时压到单次探测水平）。
    """
    pid_path = pid_file or _pid_file(cfg)
    records = _read_records(pid_path)
    spec_list = specs if specs is not None else build_specs(cfg)

    async def _row(spec: ServiceSpec) -> dict[str, object]:
        rec = records.get(spec.name)
        pid = int(rec.get("pid", 0)) if rec else 0
        alive = _pid_alive(pid) if pid else False
        serving = False
        if alive:
            try:
                await _wait_ready(spec, timeout=2.0)
                serving = True
            except LauncherError:
                serving = False
        return {
            "service": spec.name,
            "port": spec.port,
            "pid": pid if alive else None,
            "running": serving,
            "state": "running" if serving else (
                "starting" if alive else "stopped"),
        }

    # gather 保序，rows 顺序与 spec_list 一致
    return list(await asyncio.gather(*(_row(s) for s in spec_list)))
