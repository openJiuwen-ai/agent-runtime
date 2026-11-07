# coding: utf-8
"""agentserver 子进程感知（``status`` 第二张表的数据源）。

感知原则（精确标记,而非进程名粗暴筛选）:
每个 agentserver 子进程由 SubprocessPodClient 拉起,env 必然携带
``JIUWENSWARM_DATA_DIR=<pods_root>/pods/<pod_id>/workspace``
（pod_id 唯一,路径圈定在本仓 data 目录内）。扫描规则:

1. cmdline 必须是 ``-m <AGENTSERVER_MODULE>`` 形态（进程名筛选会误报
   任何命令行里带关键字的无关进程,故只认 ``-m`` 后的模块 token）;
2. env 的 JIUWENSWARM_DATA_DIR 前缀命中本仓 pods 根 → 归属到具体
   pod_id,并读出 SSE/WS 端口（零误报）;
3. env 不可读（如跨权限残留）但 cmdline 命中 → 仍计入,pod_id 记 ``?``
   （兜底可见性,便于发现孤儿进程）;
4. 同 pod 出现两行属 Windows venv 的正常形态,不是两个 agentserver:
   ``.venv\\Scripts\\python.exe`` 是启动器壳（venvlauncher,它拉起真正的
   解释器后原地等待）,真实解释器的 ``exe()`` 是基础 Python 路径而
   cmdline[0] 仍是 venv 路径——以此区分,role 列标 ``shim``/``main``
   （干活的永远是 main 行;两行 env/cmdline 相同,杀任一行随树全灭）。

性能:扫描前先按进程名（python*/py）预筛——agentserver 必由本 venv
python 拉起,预筛只为跳过无关进程的 cmdline/env 读取,不参与判定。

注意:agentserver 进程是瞬态的——runtime 按聊天路由按需拉起,空闲约
60s 被 sweeper 回收（``min_idle_pods=0`` 时必回收）,status 显示 0 是常态。
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

from .config import LiteConfig
from .spec import AGENTSERVER_MODULE


class PodsScanError(RuntimeError):
    """进程感知不可用（如缺 psutil）。"""


def _cmdline_matches(cmdline: list[str] | None, module: str) -> bool:
    """cmdline 形如 [python, -m, module, --port, N] → True。"""
    if not cmdline:
        return False
    with contextlib.suppress(ValueError):
        i = list(cmdline).index("-m")
        return i + 1 < len(cmdline) and cmdline[i + 1] == module
    return False


def scan_agentserver_processes(cfg: LiteConfig) -> list[dict[str, Any]]:
    """扫描本机存活的 agentserver 子进程。

    返回行: {pid, pod_id, sse_port, ws_port, created};按 (pod_id, pid) 排序。

    性能:先用进程名预筛（agentserver 必由 venv python 拉起，名字必为
    python*/py），只对命中的少数进程读 cmdline/env——Windows 上全量
    cmdline 快照秒级，预筛后毫秒级（``--watch`` 每秒刷新的前提）。
    判定语义不变:仍以 ``-m <AGENTSERVER_MODULE>`` + env 双匹配为准。
    """
    try:
        import psutil
    except ImportError as exc:  # pragma: no cover - 依赖声明兜底
        raise PodsScanError(f"缺 psutil: {exc}") from None

    pods_prefix = str((cfg.agentserver_pods_root / "pods").resolve()) + os.sep
    rows: list[dict[str, Any]] = []
    for proc in psutil.process_iter(attrs=["pid", "name", "create_time"]):
        try:
            name = str(proc.info.get("name") or "").lower()
            if not (name.startswith("python") or name == "py.exe"):
                continue   # agentserver 只可能由 python 解释器拉起
            cmdline = proc.cmdline()
            if not _cmdline_matches(cmdline, AGENTSERVER_MODULE):
                continue
            pod_id, sse_port, ws_port = "?", None, None
            with contextlib.suppress(Exception):
                env = proc.environ() or {}
                data_dir = env.get("JIUWENSWARM_DATA_DIR") or ""
                if data_dir.startswith(pods_prefix):
                    # <pods_root>/pods/<pod_id>/workspace → pod_id
                    pod_id = Path(data_dir).parent.name
                sse_port = int(env["AGENT_HTTP_PORT"]) if env.get("AGENT_HTTP_PORT") else None
                ws_port = int(env["AGENT_SERVER_PORT"]) if env.get("AGENT_SERVER_PORT") else None
            if ws_port is None:
                # env 不可读时从 cmdline --port 兜底
                with contextlib.suppress(ValueError, IndexError):
                    ws_port = int(cmdline[cmdline.index("--port") + 1])  # type: ignore[union-attr]
            rows.append({
                "pid": proc.info["pid"],
                "pod_id": pod_id,
                "role": _process_role(proc, cmdline),
                "sse_port": sse_port,
                "ws_port": ws_port,
                "created": proc.info.get("create_time"),
            })
        except Exception:  # noqa: BLE001 - 单进程异常不阻断整表（竞态消亡等）
            continue
    # main（真实解释器）排在 shim（启动器壳）前,同 pod 两行一眼分清主次
    rows.sort(key=lambda r: (str(r["pod_id"]), r["role"] != "main", int(r["pid"])))
    return rows


def _process_role(proc: psutil.Process, cmdline: list[str]) -> str:
    """区分 venv 启动器壳与真实解释器（见模块 docstring 第 4 条）。

    Windows 下 ``.venv\\Scripts\\python.exe`` 是 venvlauncher:它拉起真正的
    解释器（exe 是基础 Python 路径）后原地等待。壳的 ``exe()`` 与
    cmdline[0] 相同;真实解释器两者不同。读不到 exe() 时退化记 ``?``。
    """
    with contextlib.suppress(Exception):
        exe = str(proc.exe() or "")
        cmd0 = str(cmdline[0] or "") if cmdline else ""
        if exe and cmd0:
            return "shim" if exe.lower() == cmd0.lower() else "main"
    return "?"
