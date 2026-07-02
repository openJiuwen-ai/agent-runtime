# coding: utf-8
# Test files intentionally access private members to validate edge cases.
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.simulate_router import run_flow_control_cases as runner


class _FakeSyncRedis:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, ...]] = []
        self._members: dict[str, list[str]] = {}
        self._exists: dict[str, int] = {}

    def delete(self, *keys: str) -> int:
        self.deleted.append(tuple(keys))
        return len(keys)

    def zcard(self, key: str) -> int:
        return len(self._members.get(key, []))

    def zrange(self, key: str, start: int, end: int) -> list[str]:  # noqa: ARG002
        return self._members.get(key, [])

    def exists(self, key: str) -> int:
        return self._exists.get(key, 0)


class _FakeRedisFactory:
    last: _FakeSyncRedis | None = None

    @classmethod
    def from_url(cls, *_args, **_kwargs) -> _FakeSyncRedis:
        cls.last = _FakeSyncRedis()
        return cls.last


class _FakeResponse:
    def __init__(self, status_code: int, body) -> None:
        self.status_code = status_code
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    @property
    def text(self) -> str:
        return str(self._body)

    async def aread(self) -> bytes:
        return b"stream-body"


class _FakeAsyncClient:
    calls: list[dict] = []
    response = _FakeResponse(200, {"ok": True})

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        return None

    async def post(self, url: str, **kwargs) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.response


@pytest.fixture
def tester(monkeypatch):
    monkeypatch.setattr(runner, "Redis", _FakeRedisFactory)
    return runner.FlowControlTester(
        base_url="http://localhost:8090/",
        project_id="demo",
        agent_id="agent-a",
        redis_url="redis://localhost:6379/0",
        session_limit=1,
        session_window=10,
        global_limit=3,
        global_window=30,
        request_timeout=1.5,
    )


def test_expand_cases_presets_and_deduplication():
    assert runner.expand_cases(["core", "P01", "pressure"]) == [
        "F02", "F03", "F04", "S01", "S02", "R01", "R02", "D01", "D02", "P01", "P04", "P02", "P03"
    ]
    assert runner.expand_cases(["all"]) == runner.CASE_ORDER
    with pytest.raises(ValueError):
        runner.expand_cases(["missing"])


def test_env_helpers_build_urls_and_log_patterns(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# ignored",
                "REDIS_HOST=redis.local",
                "REDIS_PORT=6380",
                "REDIS_DB=2",
                "REDIS_PASSWORD='p@ ss'",
                "FASTAPI_HOST=0.0.0.0",
                "FASTAPI_PORT=9000",
                "LOG_FILE=logs/a2a_service.log",
            ]
        ),
        encoding="utf-8",
    )

    env = runner._load_env(env_file)

    assert runner._build_redis_url(env) == "redis://:p%40+ss@redis.local:6380/2"
    assert runner._build_base_url(env) == "http://127.0.0.1:9000"
    assert runner._mask_redis_url("redis://:secret@redis.local:6379/0") == "redis:***@redis.local:6379/0"
    assert runner._build_service_log_pattern(env_file, env).endswith(r"logs\a2a_service_*.log")
    with pytest.raises(FileNotFoundError):
        runner._load_env(tmp_path / "missing.env")
    with pytest.raises(ValueError):
        runner._build_redis_url({"REDIS_HOST": ""})


def test_log_tail_static_helpers_and_lifecycle(tmp_path):
    old_log = tmp_path / "service_1.log"
    new_log = tmp_path / "service_2.log"
    old_log.write_text("a\n", encoding="utf-8")
    new_log.write_text("b\nc\n", encoding="utf-8")

    pattern = str(tmp_path / "service_*.log")
    assert runner._ServiceLogTail._get_latest_log_file(pattern) in {str(old_log), str(new_log)}
    assert runner._ServiceLogTail._get_file_lines(str(new_log)) == ["b", "c"]
    assert runner._ServiceLogTail._get_file_lines(str(tmp_path / "none.log")) == []

    tail = runner._ServiceLogTail(log_pattern="", poll_interval_ms=1)
    tail.start()
    assert tail._task is None


@pytest.mark.asyncio
async def test_flow_control_tester_request_helpers(tester, monkeypatch):
    monkeypatch.setattr(runner.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.response = _FakeResponse(200, {"ok": True})

    assert tester.endpoint("conv-1") == "http://localhost:8090/v1/demo/agents/agent-a/conversations/conv-1"
    payload = tester.payload("conv-1", stream=False)
    assert payload["custom_data"]["inputs"]["query"] == "flow-control-test:conv-1"

    status_code, body = await tester.post("conv-1", stream=False)
    assert status_code == 200
    assert body == {"ok": True}
    assert _FakeAsyncClient.calls[0]["headers"] == {"Content-Type": "application/json"}

    status_code, body = await tester.post("conv-2", stream=True)
    assert status_code == 200
    assert body == b"stream-body"

    tester.track("conv-1", "conv-2")
    tester.cleanup()
    assert _FakeRedisFactory.last is not None
    deleted = _FakeRedisFactory.last.deleted[-1]
    assert tester.global_key() in deleted
    assert tester.session_key("conv-1") in deleted
    assert tester.task_mapping_key("conv-2") in deleted


@pytest.mark.asyncio
async def test_flow_control_case_methods_cover_success_paths(tester, monkeypatch):
    async def fake_post(conversation_id: str, *, stream: bool):
        if conversation_id.startswith(("f02", "r01", "r02")) and conversation_id in seen:
            body = {"error_code": "100001", "success": False} if not stream else b'{"error_code":"100001"}'
            return 429, body
        seen.add(conversation_id)
        if conversation_id.startswith("p02"):
            return (200, {}) if len(seen) <= tester.global_limit else (429, {})
        return 200, {"ok": True}

    async def fake_direct_check(_redis_client, conversation_id: str, **kwargs):
        if conversation_id.startswith(("s01-new", "p03-new")):
            return False, "limited", "100001"
        if conversation_id.startswith("d03-old") and d03_seen["old"]:
            return False, "limited", "100001"
        if conversation_id.startswith("d03-old"):
            d03_seen["old"] = True
        return True, None, None

    class _AsyncRedis:
        async def disconnect(self) -> None:
            return None

    seen: set[str] = set()
    d03_seen = {"old": False}
    monkeypatch.setattr(tester, "post", fake_post)
    monkeypatch.setattr(tester, "_direct_check", fake_direct_check)

    async def fake_with_async_redis():
        return _AsyncRedis()

    async def fast_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(tester, "_with_async_redis", fake_with_async_redis)
    monkeypatch.setattr(runner.asyncio, "sleep", fast_sleep)
    assert _FakeRedisFactory.last is not None
    _FakeRedisFactory.last._exists = {tester.task_mapping_key("r03-static"): 1}

    for method_name in (
        "case_f02",
        "case_f03",
        "case_f04",
        "case_s01",
        "case_s02",
        "case_r01",
        "case_r02",
        "case_d01",
        "case_d02",
        "case_d03",
        "case_p01",
        "case_p02",
        "case_p03",
        "case_p04",
    ):
        result = await getattr(tester, method_name)()
        assert isinstance(result, runner.CaseResult)


@pytest.mark.asyncio
async def test_run_cases_and_main(monkeypatch, tmp_path):
    calls: list[str] = []

    class _CaseTester:
        @staticmethod
        def cleanup() -> None:
            calls.append("cleanup")

        async def case_f02(self):
            return runner.CaseResult("F02", True, "ok")

        async def case_f03(self):
            return runner.CaseResult("F03", False, "bad")

    assert await runner._run_cases(tester=_CaseTester(), case_ids=["F02"]) == 0
    assert await runner._run_cases(tester=_CaseTester(), case_ids=["F03"]) == 1
    assert calls == ["cleanup", "cleanup"]

    env_file = tmp_path / ".env"
    env_file.write_text("REDIS_HOST=localhost\n", encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "_parse_args",
        lambda: SimpleNamespace(
            env_file=str(env_file),
            project_id="demo",
            agent_id="",
            base_url="",
            timeout=1.0,
            tail_service_log=True,
            cases=["core"],
        ),
    )
    monkeypatch.setattr(runner, "_load_env", lambda _path: {"REDIS_HOST": "localhost", "DPA_AGENT_ID": "agent-x"})
    monkeypatch.setattr(runner, "_build_redis_url", lambda _env: "redis://localhost/0")
    monkeypatch.setattr(runner, "_build_base_url", lambda _env: "http://localhost")
    monkeypatch.setattr(runner, "_build_service_log_pattern", lambda _path, _env: "")
    monkeypatch.setattr(runner, "expand_cases", lambda _cases: ["F02"])

    class _MainTester:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class _Tail:
        def __init__(self, *, log_pattern: str) -> None:
            self.log_pattern = log_pattern
            self.started = False

        def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            self.started = False

    async def fake_run_cases(*, tester, case_ids):
        assert isinstance(tester, _MainTester)
        assert case_ids == ["F02"]
        return 0

    monkeypatch.setattr(runner, "FlowControlTester", _MainTester)
    monkeypatch.setattr(runner, "_ServiceLogTail", _Tail)
    monkeypatch.setattr(runner, "_run_cases", fake_run_cases)
    assert await runner.main() == 0
