"""部署子进程执行器：代跑 deploy.sh，采集结构化进度与日志，支持取消/超时。

进度行契约见 deploy/openshift/README.md「程序化调用」：
``@@DEPLOY_JSON@@{"event":"phase|component|summary",...}``（值均为字符串）。
子进程 ``start_new_session`` 独立进程组，取消/超时经 ``killpg`` 整组终止；
deploy.sh 幂等，中断后重发任务即恢复。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import tempfile
import time
from pathlib import Path
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.deployment.toolkit import resolve_tool_dir
from manager_server.infrastructure.config import settings
from manager_server.infrastructure.logger import get_logger
from manager_server.infrastructure.utils import utc_now
from manager_server.models.deployment_models import (
    DEPLOYMENT_LOG_TABLE_DEF,
    DEPLOYMENT_TASK_TABLE_DEF,
)

_log = get_logger(__name__)
_TASK_TABLE = DEPLOYMENT_TASK_TABLE_DEF.table_name
_LOG_TABLE = DEPLOYMENT_LOG_TABLE_DEF.table_name

_JSON_PREFIX = "@@DEPLOY_JSON@@"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# 单行日志截断长度（与表列长度一致）
_LINE_MAX_CHARS = 2048
# 周期职责间隔（秒）：取消检查 / 心跳
_CANCEL_CHECK_INTERVAL = 3.0
_HEARTBEAT_INTERVAL = 10.0
# 日志落库节流：批行数或间隔先到先刷
_LOG_FLUSH_INTERVAL = 2.0
_LOG_FLUSH_BATCH = 200
# 取消宽限：SIGTERM 后未退出升级 SIGKILL
_KILL_GRACE_SECONDS = 30.0
# 泵循环空闲唤醒间隔（秒）
_PUMP_TICK = 1.0
# 进度落库最小间隔（秒）
_PROGRESS_PERSIST_INTERVAL = 2.0
# 任务预算下限（秒，防误配过小直接超时）
_MIN_TASK_BUDGET_SECONDS = 60


class DeployRunner:
    """执行单个部署任务（调用前任务行已由 service 置 running/started_at）。"""

    def __init__(
        self,
        handler: DBHandler,
        task: dict,
        env_content: str,
        *,
        wait_timeout_secs: int | None = None,
        action: str = "up",
    ) -> None:
        self._handler = handler
        self._deployment_id = task["deployment_id"]
        self._namespace = task["namespace"]
        self._env_content = env_content
        self._wait_timeout_secs = wait_timeout_secs
        self._action = "down" if action == "down" else "up"

        self._work_dir = Path(tempfile.mkdtemp(prefix=f"deploy-{self._deployment_id}-"))
        self._phase = "validate"
        self._phase_states: dict[str, str] = {}
        self._components: dict[str, dict[str, Any]] = {}
        self._current_component: str | None = None
        self._fail_message: str | None = None

        # 日志缓冲（超限即停写，尾部补截断标记）
        self._log_buffer: list[tuple[int, str]] = []
        self._log_seq = 0
        self._log_lines_written = 0
        self._log_bytes_written = 0
        self._log_truncated = False
        self._log_tail: list[str] = []

        self._last_persist = 0.0
        self._last_heartbeat = 0.0
        self._last_cancel_check = 0.0
        self._last_log_flush = 0.0
        self._dirty = False

        self._cancel_seen = False
        self._timed_out = False
        self._summary: dict[str, Any] | None = None

    # ---------- 对外结果 ----------

    @property
    def progress_snapshot(self) -> dict:
        return {
            "phases": dict(self._phase_states),
            "components": {k: dict(v) for k, v in self._components.items()},
            "current": self._current_component,
        }

    # ---------- 主流程 ----------

    async def run(self) -> dict:
        """跑完子进程并落终态，返回 ``{status, exit_code, error_summary}``。"""
        tool_dir = resolve_tool_dir()
        conf_dir = self._work_dir / "conf"
        tmp_dir = self._work_dir / "tmp"
        conf_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        env_file = self._work_dir / ".env.custom"
        env_file.write_text(self._env_content, encoding="utf-8")

        sub_env = dict(os.environ)
        sub_env.update(
            {
                "DEPLOY_ENV_FILE": str(env_file),
                "DEPLOY_CONF_DIR": str(conf_dir),
                "DEPLOY_TMP_DIR": str(tmp_dir),
                "DEPLOY_JSON_PROGRESS": "1",
            }
        )
        kubeconfig = (settings.deploy_kubeconfig or "").strip()
        if kubeconfig:
            sub_env["KUBECONFIG"] = kubeconfig
        if self._wait_timeout_secs:
            sub_env["WAIT_TIMEOUT_SECS"] = str(int(self._wait_timeout_secs))

        deadline = time.monotonic() + max(
            _MIN_TASK_BUDGET_SECONDS, int(settings.deploy_task_timeout_seconds)
        )
        exit_code: int | None = None
        try:
            if not (tool_dir / "deploy.sh").is_file():
                raise FileNotFoundError(f"{tool_dir}/deploy.sh")
            if self._action == "down":
                # down 不渲染、直接消费 conf/ 现有清单：先补渲染（down 目标集）再卸载，
                # 两步共用同一 work_dir（env/conf 隔离）
                exit_code = await self._exec(
                    tool_dir, sub_env, ["-n", self._namespace, "down", "--render-only"], deadline
                )
                if exit_code == 0:
                    exit_code = await self._exec(
                        tool_dir, sub_env, ["-n", self._namespace, "down"], deadline
                    )
            else:
                exit_code = await self._exec(
                    tool_dir, sub_env, ["-n", self._namespace, "up"], deadline
                )
        except FileNotFoundError as exc:
            exit_code = -1
            self._fail_message = f"部署工具不可用: {exc}"
        except Exception as exc:  # noqa: BLE001
            exit_code = -1
            self._fail_message = f"部署执行异常: {exc}"
            _log.warning(
                "deployment_runner_error",
                deployment_id=self._deployment_id,
                error=str(exc),
            )
        finally:
            await self._flush_logs(force=True)
            shutil.rmtree(self._work_dir, ignore_errors=True)

        status, error_summary = self._final_status(exit_code)
        self._dirty = False
        final_progress = self.progress_snapshot
        final_progress["log_truncated"] = self._log_truncated
        await self._persist(
            {
                "status": status,
                "exit_code": exit_code,
                "error_summary": error_summary,
                "phase": "finished" if status == "success" else self._phase,
                "progress": final_progress,
                "finished_at": utc_now(),
                "heartbeat_at": utc_now(),
            }
        )
        _log.info(
            "deployment_finished",
            deployment_id=self._deployment_id,
            status=status,
            exit_code=exit_code,
        )
        return {"status": status, "exit_code": exit_code, "error_summary": error_summary}

    async def _exec(
        self, tool_dir: Path, sub_env: dict, args: list[str], deadline: float
    ) -> int:
        """起一个 deploy.sh 子进程并泵完输出，返回退出码。"""
        proc = await asyncio.create_subprocess_exec(
            "bash",
            "deploy.sh",
            *args,
            cwd=str(tool_dir),
            env=sub_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        return await self._pump(proc, deadline)

    # ---------- 输出泵与周期职责 ----------

    async def _pump(self, proc: asyncio.subprocess.Process, deadline: float) -> int:
        """逐行泵子进程输出；空转间隙执行取消检查/心跳/日志刷盘/超时判定。"""
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def _reader() -> None:
            try:
                while True:
                    line = await proc.stdout.readline()
                    await queue.put(line)
                    if not line:
                        return
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "deployment_reader_error",
                    deployment_id=self._deployment_id,
                    error=str(exc),
                )
                await queue.put(b"")

        reader = asyncio.create_task(_reader())
        try:
            while True:
                try:
                    line = await asyncio.wait_for(queue.get(), timeout=_PUMP_TICK)
                except asyncio.TimeoutError:
                    line = None
                if line is not None:
                    if not line:
                        break
                    self._consume_line(line.decode("utf-8", errors="replace"))
                now = time.monotonic()
                if now - self._last_cancel_check >= _CANCEL_CHECK_INTERVAL:
                    self._last_cancel_check = now
                    await self._check_cancel(proc)
                if now > deadline and proc.returncode is None and not self._timed_out:
                    self._timed_out = True
                    if not self._fail_message:
                        self._fail_message = (
                            f"任务超时（>{settings.deploy_task_timeout_seconds}s）被终止"
                        )
                    await self._terminate(proc)
                if self._dirty and now - self._last_persist >= _PROGRESS_PERSIST_INTERVAL:
                    await self._persist({})
                if now - self._last_heartbeat >= _HEARTBEAT_INTERVAL:
                    self._last_heartbeat = now
                    await self._persist({"heartbeat_at": utc_now()})
                await self._flush_logs(force=False)
            return await proc.wait()
        finally:
            reader.cancel()
            try:
                await reader
            except asyncio.CancelledError:
                pass

    def _consume_line(self, raw: str) -> None:
        text = raw.rstrip("\r\n")
        if text.startswith(_JSON_PREFIX):
            try:
                payload = json.loads(text[len(_JSON_PREFIX) :])
            except json.JSONDecodeError:
                self._append_log(text)
                return
            self._consume_event(payload)
            return
        self._append_log(text)

    def _consume_event(self, event: dict) -> None:
        kind = str(event.get("event", ""))
        state = str(event.get("state", ""))
        if kind == "phase":
            phase = str(event.get("phase", ""))
            if phase:
                self._phase = phase
                if state:
                    self._phase_states[phase] = state
            if state == "fail" and event.get("message"):
                self._fail_message = str(event["message"])
        elif kind == "component":
            name = str(event.get("component", ""))
            if name:
                self._components[name] = {
                    "state": state,
                    "index": int(event.get("index", 0) or 0),
                    "total": int(event.get("total", 0) or 0),
                    **({"message": str(event["message"])} if event.get("message") else {}),
                }
                if state == "start":
                    self._current_component = name
                elif state == "ok":
                    self._current_component = None
            if state == "fail" and event.get("message"):
                self._fail_message = str(event["message"])
        elif kind == "summary":
            self._summary = event
        self._dirty = True

    # ---------- 取消 / 超时终止 ----------

    async def _check_cancel(self, proc: asyncio.subprocess.Process) -> None:
        if self._cancel_seen or proc.returncode is not None:
            return
        try:
            row = await self._handler.get(
                _TASK_TABLE, {"deployment_id": self._deployment_id}
            )
        except Exception:  # noqa: BLE001
            return
        if row is not None and bool(getattr(row, "cancel_requested", False)):
            self._cancel_seen = True
            self._fail_message = None
            _log.info("deployment_cancelling", deployment_id=self._deployment_id)
            await self._terminate(proc)

    async def _terminate(self, proc: asyncio.subprocess.Process) -> None:
        """SIGTERM 进程组，宽限后 SIGKILL。"""
        if proc.returncode is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_SECONDS)
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    # ---------- 日志缓冲 ----------

    def _append_log(self, text: str) -> None:
        # 截断后仍保留尾部（error_summary 兜底要用）
        self._log_tail.append(text)
        if len(self._log_tail) > 5:
            self._log_tail.pop(0)
        if self._log_truncated:
            return
        clean = _ANSI_RE.sub("", text)[:_LINE_MAX_CHARS]
        buffered_bytes = self._log_bytes_written + sum(len(c) for _, c in self._log_buffer)
        if (
            self._log_lines_written + len(self._log_buffer) >= settings.deploy_log_max_lines
            or buffered_bytes + len(clean) >= settings.deploy_log_max_bytes
        ):
            self._log_truncated = True
            self._log_seq += 1
            self._log_buffer.append(
                (
                    self._log_seq,
                    f"... 日志超出落库上限（{settings.deploy_log_max_lines} 行 / "
                    f"{settings.deploy_log_max_bytes} 字节），已截断 ...",
                )
            )
            return
        self._log_seq += 1
        self._log_buffer.append((self._log_seq, clean))

    async def _flush_logs(self, *, force: bool) -> None:
        if not self._log_buffer:
            return
        if (
            not force
            and len(self._log_buffer) < _LOG_FLUSH_BATCH
            and time.monotonic() - self._last_log_flush < _LOG_FLUSH_INTERVAL
        ):
            return
        self._last_log_flush = time.monotonic()
        batch, self._log_buffer = self._log_buffer, []
        now = utc_now()
        try:
            for seq, content in batch:
                await self._handler.create(
                    _LOG_TABLE,
                    {
                        "deployment_id": self._deployment_id,
                        "seq": seq,
                        "content": content,
                        "created_at": now,
                    },
                )
            self._log_lines_written += len(batch)
            self._log_bytes_written += sum(len(content) for _, content in batch)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "deployment_log_flush_failed",
                deployment_id=self._deployment_id,
                error=str(exc),
            )

    # ---------- 任务行落库 ----------

    async def _persist(self, patch: dict) -> None:
        self._last_persist = time.monotonic()
        data = dict(patch)
        data["updated_at"] = utc_now()
        if "progress" not in data and self._dirty:
            self._dirty = False
            data["progress"] = self.progress_snapshot
            data["phase"] = self._phase
        try:
            await self._handler.update(
                _TASK_TABLE, {"deployment_id": self._deployment_id}, data
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "deployment_persist_failed",
                deployment_id=self._deployment_id,
                error=str(exc),
            )

    # ---------- 终态判定 ----------

    def _final_status(self, exit_code: int | None) -> tuple[str, str | None]:
        if self._cancel_seen:
            return "cancelled", None
        if exit_code == 0 and not self._timed_out:
            return "success", None
        if self._fail_message:
            return "failed", self._fail_message
        # 无结构化 fail 事件（bash 层失败：依赖缺失/语法错误/exit 127 等）→ 日志尾部兜底
        tail = _ANSI_RE.sub("", " | ".join(self._log_tail)).strip()
        return "failed", (tail[:500] or f"deploy.sh 退出码 {exit_code}")
