#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from __future__ import annotations

import argparse
import asyncio
import glob
import logging
import os
import sys
import time
import uuid
import json
from pathlib import Path
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Iterable
from urllib.parse import quote_plus

import httpx

# 本脚本位于 a2a_service/tools/simulate_router/，向上 2 级才是 a2a_service 根目录
A2A_SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(A2A_SERVICE_ROOT) not in sys.path:
    sys.path.append(str(A2A_SERVICE_ROOT))

from common.redis_client import RedisClient
from api.dispatch import _check_rate_limit
from redis import Redis


DEFAULT_SESSION_LIMIT = 1
DEFAULT_SESSION_WINDOW = 10
DEFAULT_GLOBAL_LIMIT = 3
DEFAULT_GLOBAL_WINDOW = 30
DEFAULT_CASES = ["core"]
DEFAULT_PROJECT_ID = "demo"
DEFAULT_TIMEOUT = 120.0


# 该脚本属于命令行测试入口，需要将运行进度直接打印到控制台。
# 这里通过 logging 模块输出（满足 G.LOG.02），并使用极简格式，避免对消息内容产生干扰。
_LOGGER = logging.getLogger("a2a_service.flow_control_runner")
if not _LOGGER.handlers:
    _handler = logging.StreamHandler(stream=sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _LOGGER.addHandler(_handler)
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False


def _echo(message: str) -> None:
    _LOGGER.info("%s", message)


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    detail: str


class _ServiceLogTail:
    def __init__(self, *, log_pattern: str, prefix: str = "[SERVICE]", poll_interval_ms: int = 500) -> None:
        self.log_pattern = log_pattern
        self.prefix = prefix
        self.poll_interval_ms = max(int(poll_interval_ms), 100)
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._current_path: str | None = None
        self._known_line_count = 0
        self._initialized = False
        self._script_started_at = time.time()

    def start(self) -> None:
        if not self.log_pattern:
            _echo("[INFO] tail_service_log=skip (.env 未配置 LOG_FILE)")
            return
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())
        _echo("[INFO] tail_service_log=enabled (python)")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await self._task
        finally:
            self._task = None
        _echo("[INFO] tail_service_log=stopped")

    async def _run(self) -> None:
        self._write_tail_message(f"watching pattern: {self.log_pattern}")
        while not self._stop_event.is_set():
            latest = self._get_latest_log_file(self.log_pattern)
            if latest is None:
                if not self._initialized:
                    self._write_tail_message("waiting for log file...")
                    self._initialized = True
                await self._wait_once()
                continue

            if latest != self._current_path:
                self._current_path = latest
                lines = self._get_file_lines(latest)
                if not self._initialized:
                    self._known_line_count = len(lines)
                    self._write_tail_message(f"attached to latest log: {latest} (from end)")
                    self._initialized = True
                else:
                    stat = Path(latest).stat()
                    is_fresh_log = stat.st_ctime >= self._script_started_at - 2
                    if is_fresh_log:
                        self._known_line_count = 0
                        self._write_tail_message(f"switched to newer log: {latest} (from beginning)")
                    else:
                        self._known_line_count = len(lines)
                        self._write_tail_message(f"switched to newer log: {latest} (from end)")

            if self._current_path is None:
                await self._wait_once()
                continue

            lines = self._get_file_lines(self._current_path)
            if len(lines) < self._known_line_count:
                self._known_line_count = 0
                self._write_tail_message(
                    f"detected log truncation, restarting from beginning: {self._current_path}"
                )

            if len(lines) > self._known_line_count:
                for line in lines[self._known_line_count:]:
                    self._write_tail_message(line)
                self._known_line_count = len(lines)

            await self._wait_once()

    async def _wait_once(self) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval_ms / 1000.0)
        except asyncio.TimeoutError:
            return

    def _write_tail_message(self, message: str) -> None:
        _echo(f"{self.prefix} {message}")

    @staticmethod
    def _get_latest_log_file(pattern: str) -> str | None:
        matches = [Path(item) for item in glob.glob(pattern) if Path(item).is_file()]
        if not matches:
            return None
        matches.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        return str(matches[0])

    @staticmethod
    def _get_file_lines(path: str) -> list[str]:
        try:
            return Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return []


class FlowControlTester:
    def __init__(
        self,
        *,
        base_url: str,
        project_id: str,
        agent_id: str,
        redis_url: str,
        session_limit: int,
        session_window: int,
        global_limit: int,
        global_window: int,
        request_timeout: float,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.project_id = project_id
        self.agent_id = agent_id
        self.redis_url = redis_url
        self.redis = Redis.from_url(redis_url, decode_responses=True, protocol=2)
        self.session_limit = session_limit
        self.session_window = session_window
        self.global_limit = global_limit
        self.global_window = global_window
        self.request_timeout = request_timeout
        self._tracked_conversations: set[str] = set()

    def _rate_limit_settings(
        self,
        *,
        session_limit: int | None = None,
        session_window: int | None = None,
        global_limit: int | None = None,
        global_window: int | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            redis_host="configured",
            rate_limit_max_requests=session_limit if session_limit is not None else self.session_limit,
            rate_limit_window_seconds=session_window if session_window is not None else self.session_window,
            global_rate_limit_max_requests=global_limit if global_limit is not None else self.global_limit,
            global_rate_limit_window_seconds=global_window if global_window is not None else self.global_window,
        )

    async def _direct_check(
        self,
        redis_client: RedisClient,
        conversation_id: str,
        *,
        session_limit: int | None = None,
        session_window: int | None = None,
        global_limit: int | None = None,
        global_window: int | None = None,
    ) -> tuple[bool, Any, Any]:
        return await _check_rate_limit(
            redis=redis_client,
            settings=self._rate_limit_settings(
                session_limit=session_limit,
                session_window=session_window,
                global_limit=global_limit,
                global_window=global_window,
            ),
            agent_id=self.agent_id,
            conversation_id=conversation_id,
        )

    async def _with_async_redis(self):
        redis_client = RedisClient()
        await redis_client.connect(self.redis_url)
        return redis_client

    def endpoint(self, conversation_id: str) -> str:
        return (
            f"{self.base_url}/v1/{self.project_id}/agents/{self.agent_id}"
            f"/conversations/{conversation_id}"
        )

    def payload(self, conversation_id: str, *, stream: bool) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "input": {"query": f"flow-control-test:{conversation_id}"},
            "conversation_id": conversation_id,
            "stream": stream,
            "custom_data": {"inputs": {"query": f"flow-control-test:{conversation_id}"}},
        }

    def session_key(self, conversation_id: str) -> str:
        return f"a2a_service:rate_limit:{self.agent_id}:session:{conversation_id}"

    def global_key(self) -> str:
        return f"a2a_service:rate_limit:{self.agent_id}:global"

    @staticmethod
    def task_mapping_key(conversation_id: str) -> str:
        return f"session:{conversation_id}:a2a_task_id"

    def track(self, *conversation_ids: str) -> None:
        for conv in conversation_ids:
            self._tracked_conversations.add(conv)

    def cleanup(self, *conversation_ids: str) -> None:
        targets = set(conversation_ids) if conversation_ids else set(self._tracked_conversations)
        keys = [self.global_key()]
        for conv in targets:
            keys.append(self.session_key(conv))
            keys.append(self.task_mapping_key(conv))
        if keys:
            self.redis.delete(*keys)

    async def post(self, conversation_id: str, *, stream: bool) -> tuple[int, Any]:
        self.track(conversation_id)
        async with httpx.AsyncClient(timeout=self.request_timeout, trust_env=False) as client:
            response = await client.post(
                self.endpoint(conversation_id),
                json=self.payload(conversation_id, stream=stream),
                headers={"Content-Type": "application/json"},
            )
            if stream:
                return response.status_code, await response.aread()
            try:
                return response.status_code, response.json()
            except json.JSONDecodeError:
                return response.status_code, response.text

    async def concurrent_post(self, conversation_id: str, *, count: int, stream: bool = False) -> list[tuple[int, Any]]:
        tasks = [self.post(conversation_id, stream=stream) for _ in range(count)]
        return await asyncio.gather(*tasks)

    def member_count(self, key: str) -> int:
        return int(self.redis.zcard(key))

    def members(self, key: str) -> list[str]:
        return [str(member) for member in self.redis.zrange(key, 0, -1)]

    async def case_f02(self) -> CaseResult:
        conv = f"f02-{uuid.uuid4().hex[:8]}"
        self.cleanup(conv)
        first_status, _ = await self.post(conv, stream=False)
        second_status, second_body = await self.post(conv, stream=False)
        passed = (
            first_status == 200
            and second_status == 429
            and isinstance(second_body, dict)
            and second_body.get("error_code") == "100001"
        )
        return CaseResult("F02", passed, f"first={first_status}, second={second_status}")

    async def case_f03(self) -> CaseResult:
        conv1 = f"f03a-{uuid.uuid4().hex[:8]}"
        conv2 = f"f03b-{uuid.uuid4().hex[:8]}"
        self.cleanup(conv1, conv2)
        s1, _ = await self.post(conv1, stream=False)
        s2, _ = await self.post(conv2, stream=False)
        passed = s1 == 200 and s2 == 200
        return CaseResult("F03", passed, f"conv1={s1}, conv2={s2}")

    async def case_f04(self) -> CaseResult:
        convs = [f"f04-{i}-{uuid.uuid4().hex[:6]}" for i in range(min(self.global_limit, 3))]
        self.cleanup(*convs)
        statuses = []
        for conv in convs:
            status_code, _ = await self.post(conv, stream=False)
            statuses.append(status_code)
        passed = all(code == 200 for code in statuses)
        return CaseResult("F04", passed, f"statuses={statuses}")

    async def case_s01(self) -> CaseResult:
        convs = [f"s01-fill-{i}-{uuid.uuid4().hex[:6]}" for i in range(self.global_limit)]
        new_conv = f"s01-new-{uuid.uuid4().hex[:8]}"
        self.cleanup(*convs, new_conv)
        redis_client = await self._with_async_redis()
        try:
            fill_results = []
            for conv in convs:
                allowed, _, _ = await self._direct_check(redis_client, conv)
                fill_results.append(allowed)
            blocked_allowed, blocked_msg, blocked_code = await self._direct_check(redis_client, new_conv)
        finally:
            await redis_client.disconnect()
        passed = all(fill_results) and not blocked_allowed and blocked_code == "100001"
        return CaseResult(
            "S01",
            passed,
            (
                f"fill={fill_results}, new={blocked_allowed}, "
                f"code={blocked_code}, msg={blocked_msg}"
            ),
        )

    async def case_s02(self) -> CaseResult:
        existing_conv = f"s02-old-{uuid.uuid4().hex[:8]}"
        fill_convs = [f"s02-fill-{i}-{uuid.uuid4().hex[:6]}" for i in range(max(self.global_limit - 1, 0))]
        self.cleanup(existing_conv, *fill_convs)
        redis_client = await self._with_async_redis()
        try:
            relaxed_session_limit = max(self.session_limit + 1, 2)
            first_allowed, _, _ = await self._direct_check(
                redis_client,
                existing_conv,
                session_limit=relaxed_session_limit,
            )
            fill_results = []
            for conv in fill_convs:
                allowed, _, _ = await self._direct_check(
                    redis_client,
                    conv,
                    session_limit=relaxed_session_limit,
                )
                fill_results.append(allowed)
            existing_again_allowed, _, _ = await self._direct_check(
                redis_client,
                existing_conv,
                session_limit=relaxed_session_limit,
            )
        finally:
            await redis_client.disconnect()
        passed = first_allowed and all(fill_results) and existing_again_allowed
        return CaseResult(
            "S02",
            passed,
            (
                f"first={first_allowed}, fill={fill_results}, "
                f"existing_again={existing_again_allowed}, "
                f"session_limit={relaxed_session_limit}"
            ),
        )

    async def case_r01(self) -> CaseResult:
        conv = f"r01-{uuid.uuid4().hex[:8]}"
        self.cleanup(conv)
        await self.post(conv, stream=False)
        status_code, body = await self.post(conv, stream=False)
        required = {"success", "error", "error_code", "conversation_id", "agent_id"}
        passed = (
            status_code == 429
            and isinstance(body, dict)
            and required.issubset(body.keys())
            and body.get("success") is False
        )
        body_keys = sorted(body.keys()) if isinstance(body, dict) else type(body).__name__
        return CaseResult(
            "R01",
            passed,
            f"status={status_code}, keys={body_keys}",
        )

    async def case_r02(self) -> CaseResult:
        conv = f"r02-{uuid.uuid4().hex[:8]}"
        self.cleanup(conv)
        await self.post(conv, stream=False)
        status_code, body = await self.post(conv, stream=True)
        text = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else str(body)
        passed = status_code == 429 and "error_code" in text
        return CaseResult("R02", passed, f"status={status_code}, body={text[:120]!r}")

    async def case_r03(self) -> CaseResult:
        conv = f"r03-{uuid.uuid4().hex[:8]}"
        self.cleanup(conv)
        await self.post(conv, stream=False)
        await self.post(conv, stream=False)
        task_key_exists = self.redis.exists(self.task_mapping_key(conv))
        passed = int(task_key_exists) == 1
        return CaseResult(
            "R03",
            passed,
            "限流拒绝未新增额外 task 映射；该会话首个成功请求会保留已有映射",
        )

    async def case_d01(self) -> CaseResult:
        conv = f"d01-{uuid.uuid4().hex[:8]}"
        self.cleanup(conv)
        await self.post(conv, stream=False)
        key = self.session_key(conv)
        members = self.members(key)
        passed = self.redis.exists(key) == 1 and len(members) > 0 and all(":" not in member for member in members)
        return CaseResult("D01", passed, f"key={key}, members={members[:3]}")

    async def case_d02(self) -> CaseResult:
        conv = f"d02-{uuid.uuid4().hex[:8]}"
        self.cleanup(conv)
        await self.post(conv, stream=False)
        key = self.global_key()
        members = self.members(key)
        passed = self.redis.exists(key) == 1 and any(member.startswith(f"{conv}:") for member in members)
        return CaseResult("D02", passed, f"key={key}, sample={members[:3]}")

    async def case_d03(self) -> CaseResult:
        test_session_window = 2
        test_global_window = 2
        wait_seconds = max(test_session_window, test_global_window) + 0.2
        conv_old = f"d03-old-{uuid.uuid4().hex[:8]}"
        fill_convs = [f"d03-fill-{i}-{uuid.uuid4().hex[:6]}" for i in range(max(self.global_limit, 0))]
        conv_retry = conv_old
        self.cleanup(conv_old, *fill_convs)

        redis_client = await self._with_async_redis()
        try:
            old_allowed, _, _ = await self._direct_check(
                redis_client,
                conv_old,
                session_window=test_session_window,
                global_window=test_global_window,
            )
            await asyncio.sleep(wait_seconds)

            fill_results: list[bool] = []
            for conv in fill_convs:
                allowed, _, _ = await self._direct_check(
                    redis_client,
                    conv,
                    session_window=test_session_window,
                    global_window=test_global_window,
                )
                fill_results.append(allowed)

            old_members_cleared = not any(
                member.startswith(f"{conv_old}:") for member in self.members(self.global_key())
            )

            retry_allowed, blocked_msg, blocked_code = await self._direct_check(
                redis_client,
                conv_retry,
                session_window=test_session_window,
                global_window=test_global_window,
            )
        finally:
            await redis_client.disconnect()

        passed = (
            old_allowed
            and old_members_cleared
            and all(fill_results)
            and not retry_allowed
            and blocked_code == "100001"
        )
        return CaseResult(
            "D03",
            passed,
            (
                f"wait={wait_seconds:.1f}s, global_cleared={old_members_cleared}, "
                f"retry_allowed={retry_allowed}, code={blocked_code}, "
                f"msg={blocked_msg}"
            ),
        )

    async def case_p01(self) -> CaseResult:
        conv = f"p01-{uuid.uuid4().hex[:8]}"
        self.cleanup(conv)
        redis_client = await self._with_async_redis()
        try:
            results = await asyncio.gather(*[self._direct_check(redis_client, conv) for _ in range(20)])
        finally:
            await redis_client.disconnect()
        allowed = sum(1 for allowed, _, _ in results if allowed)
        blocked = len(results) - allowed
        passed = allowed == self.session_limit and blocked == 20 - self.session_limit
        return CaseResult("P01", passed, f"allowed={allowed}, blocked={blocked}")

    async def case_p02(self) -> CaseResult:
        convs = [f"p02-{i}-{uuid.uuid4().hex[:6]}" for i in range(max(self.global_limit * 3, 10))]
        self.cleanup(*convs)
        results = await asyncio.gather(*[self.post(conv, stream=False) for conv in convs])
        allowed = sum(1 for status_code, _ in results if status_code == 200)
        blocked = sum(1 for status_code, _ in results if status_code == 429)
        passed = allowed <= self.global_limit and blocked >= len(convs) - self.global_limit
        return CaseResult("P02", passed, f"allowed={allowed}, blocked={blocked}, total={len(convs)}")

    async def case_p03(self) -> CaseResult:
        relaxed_session_limit = 2
        old_convs = [f"p03-old-{i}-{uuid.uuid4().hex[:6]}" for i in range(min(self.global_limit, 3))]
        new_convs = [f"p03-new-{i}-{uuid.uuid4().hex[:6]}" for i in range(10)]
        self.cleanup(*old_convs, *new_convs)
        redis_client = await self._with_async_redis()
        try:
            for conv in old_convs:
                await self._direct_check(
                    redis_client,
                    conv,
                    session_limit=relaxed_session_limit,
                )
            old_checks = [
                self._direct_check(
                    redis_client,
                    conv,
                    session_limit=relaxed_session_limit,
                )
                for conv in old_convs
            ]
            new_checks = [
                self._direct_check(
                    redis_client,
                    conv,
                    session_limit=relaxed_session_limit,
                )
                for conv in new_convs
            ]
            old_results = await asyncio.gather(*old_checks)
            new_results = await asyncio.gather(*new_checks)
        finally:
            await redis_client.disconnect()
        old_allowed = sum(1 for allowed, _, _ in old_results if allowed)
        new_allowed = sum(1 for allowed, _, _ in new_results if allowed)
        passed = old_allowed == len(old_convs) and new_allowed == 0
        return CaseResult(
            "P03",
            passed,
            f"old_allowed={old_allowed}, new_allowed={new_allowed}, session_limit={relaxed_session_limit}",
        )

    async def case_p04(self) -> CaseResult:
        convs = [f"p04-{i}-{uuid.uuid4().hex[:6]}" for i in range(50)]
        self.cleanup(*convs)
        redis_client = await self._with_async_redis()
        try:
            results = await asyncio.gather(*[self._direct_check(redis_client, conv) for conv in convs])
        finally:
            await redis_client.disconnect()
        allowed = sum(1 for allowed, _, _ in results if allowed)
        passed = allowed <= self.global_limit
        return CaseResult("P04", passed, f"allowed={allowed}, expected<={self.global_limit}")


CASE_ORDER = [
    "F02",
    "F03",
    "F04",
    "S01",
    "S02",
    "R01",
    "R02",
    "R03",
    "D01",
    "D02",
    "D03",
    "P01",
    "P02",
    "P03",
    "P04",
]


def expand_cases(raw_cases: Iterable[str]) -> list[str]:
    presets = {
        "core": ["F02", "F03", "F04", "S01", "S02", "R01", "R02", "D01", "D02", "P01", "P04"],
        "pressure": ["P01", "P02", "P03", "P04"],
        "all": CASE_ORDER,
    }
    expanded: list[str] = []
    for item in raw_cases:
        key = item.upper()
        if item in presets:
            expanded.extend(presets[item])
        elif key in CASE_ORDER:
            expanded.append(key)
        else:
            raise ValueError(f"未知用例或预设: {item}")
    seen: set[str] = set()
    ordered: list[str] = []
    for case_id in expanded:
        if case_id not in seen:
            ordered.append(case_id)
            seen.add(case_id)
    return ordered


def _load_env(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        raise FileNotFoundError(f"未找到 .env 文件: {env_path}")

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _build_redis_url(env: dict[str, str]) -> str:
    host = env.get("REDIS_HOST", "").strip()
    if not host:
        raise ValueError(".env 中未配置 REDIS_HOST，无法自动构造 redis_url")

    port = env.get("REDIS_PORT", "6379").strip() or "6379"
    db = env.get("REDIS_DB", "0").strip() or "0"
    password = env.get("REDIS_PASSWORD", "")
    if password:
        return f"redis://:{quote_plus(password)}@{host}:{port}/{db}"
    return f"redis://{host}:{port}/{db}"


def _build_base_url(env: dict[str, str]) -> str:
    host = env.get("FASTAPI_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if host == "0.0.0.0":
        host = "127.0.0.1"
    port = env.get("FASTAPI_PORT", "8090").strip() or "8090"
    return f"http://{host}:{port}"


def _mask_redis_url(redis_url: str) -> str:
    if "@" not in redis_url or ":" not in redis_url:
        return redis_url
    prefix, suffix = redis_url.split("@", 1)
    if ":" not in prefix:
        return redis_url
    scheme, _secret = prefix.split(":", 1)
    return f"{scheme}:***@{suffix}"


def _build_service_log_pattern(env_path: Path, env: dict[str, str]) -> str:
    raw_log_file = env.get("LOG_FILE", "").strip()
    if not raw_log_file:
        return ""

    log_file = Path(raw_log_file)
    if not log_file.is_absolute():
        log_file = env_path.parent / log_file

    stem = log_file.stem
    suffix = log_file.suffix or ".log"
    return str(log_file.with_name(f"{stem}_*{suffix}"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-click runner for flow control test cases.")
    parser.add_argument("--env-file", default=str(Path(__file__).resolve().parents[2] / ".env"))
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--agent-id", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--tail-service-log",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Whether to auto-tail the newest a2a_service_*.log file during test execution.",
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        default=DEFAULT_CASES,
        help="Case IDs or presets: core / pressure / all",
    )
    return parser.parse_args()


async def _run_cases(*, tester: FlowControlTester, case_ids: Iterable[str]) -> int:
    failures = 0
    case_list = list(case_ids)
    total = len(case_list)
    _echo(f"[INFO] running cases: {', '.join(case_list)}")
    _echo("[INFO] note=S01/S02/P01/P04 为直接限流校验，不会命中 8090 服务端路由日志")
    for index, case_id in enumerate(case_list, start=1):
        _echo(f"[START] ({index}/{total}) {case_id}")
        started_at = time.perf_counter()
        case_method = getattr(tester, f"case_{case_id.lower()}")
        try:
            result = await case_method()
        finally:
            tester.cleanup()
        marker = "PASS" if result.passed else "FAIL"
        elapsed = time.perf_counter() - started_at
        _echo(f"[{marker}] ({index}/{total}) {result.case_id} [{elapsed:.2f}s]: {result.detail}")
        if not result.passed:
            failures += 1
    _echo(f"[SUMMARY] total={total}, failures={failures}")
    return 1 if failures else 0


async def main() -> int:
    args = _parse_args()
    env_path = Path(args.env_file).resolve()
    env = _load_env(env_path)

    redis_url = _build_redis_url(env)
    base_url = args.base_url or _build_base_url(env)
    service_log_pattern = _build_service_log_pattern(env_path, env)
    project_id = args.project_id
    agent_id = args.agent_id or env.get("DPA_AGENT_ID", "edp_agent")
    case_ids = expand_cases(args.cases)

    _echo(f"[INFO] env_file={env_path}")
    _echo(f"[INFO] base_url={base_url}")
    _echo(f"[INFO] agent_id={agent_id}")
    _echo(f"[INFO] redis_url={_mask_redis_url(redis_url)}")
    if service_log_pattern:
        _echo(f"[INFO] service_log_pattern={service_log_pattern}")
    _echo(f"[INFO] tail_service_log={'on' if args.tail_service_log else 'off'}")
    _echo(
        "[INFO] fixed_limits="
        f"session={DEFAULT_SESSION_LIMIT}/{DEFAULT_SESSION_WINDOW}s, "
        f"global={DEFAULT_GLOBAL_LIMIT}/{DEFAULT_GLOBAL_WINDOW}s"
    )
    _echo(
        "[INFO] note=若 8090 服务早于当前 .env 配置启动，需重启服务后这些固定阈值才会生效"
    )

    tester = FlowControlTester(
        base_url=base_url,
        project_id=project_id,
        agent_id=agent_id,
        redis_url=redis_url,
        session_limit=DEFAULT_SESSION_LIMIT,
        session_window=DEFAULT_SESSION_WINDOW,
        global_limit=DEFAULT_GLOBAL_LIMIT,
        global_window=DEFAULT_GLOBAL_WINDOW,
        request_timeout=args.timeout,
    )
    service_log_tail = _ServiceLogTail(
        log_pattern=service_log_pattern,
    )
    if args.tail_service_log:
        service_log_tail.start()
    try:
        return await _run_cases(tester=tester, case_ids=case_ids)
    finally:
        await service_log_tail.stop()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
