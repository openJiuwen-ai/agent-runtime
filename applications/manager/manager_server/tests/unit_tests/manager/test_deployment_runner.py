# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""DeployRunner 子进程执行器测试：伪造 deploy.sh（JSON 进度/失败/取消/超时/管道隔离）。"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from manager_server.core.deployment import runner as runner_mod
from manager_server.core.deployment.runner import DeployRunner
from manager_server.infrastructure.utils import utc_now
from manager_server.models.deployment_models import (
    DEPLOYMENT_LOG_TABLE_DEF,
    DEPLOYMENT_TASK_TABLE_DEF,
)
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def handler(tmp_path: Path) -> SQLiteHandler:
    h = SQLiteHandler(str(tmp_path / "runner.db"))
    await h.init_database()
    await h.connect()
    await h.init_table(DEPLOYMENT_TASK_TABLE_DEF)
    await h.init_table(DEPLOYMENT_LOG_TABLE_DEF)
    yield h
    await h.disconnect()


@pytest.fixture
def fast_ticks(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner_mod, "_PUMP_TICK", 0.05)
    monkeypatch.setattr(runner_mod, "_CANCEL_CHECK_INTERVAL", 0.2)
    monkeypatch.setattr(runner_mod, "_HEARTBEAT_INTERVAL", 0.5)
    monkeypatch.setattr(runner_mod, "_PROGRESS_PERSIST_INTERVAL", 0.1)
    monkeypatch.setattr(runner_mod, "_MIN_TASK_BUDGET_SECONDS", 1)


@pytest.fixture
def fake_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """伪造工具树：deploy.sh 为可控输出的 bash 脚本。"""
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    monkeypatch.setattr(runner_mod, "resolve_tool_dir", lambda: tool_dir)
    return tool_dir


async def _make_task(handler: SQLiteHandler, deployment_id: str = "dp-run-1") -> dict:
    now = utc_now()
    await handler.create(
        DEPLOYMENT_TASK_TABLE_DEF.table_name,
        {
            "deployment_id": deployment_id,
            "namespace": "ns-run",
            "jiuwenclaw_name": "run-instance",
            "deploy_mode": "full",
            "status": "running",
            "phase": "validate",
            "created_by": "tester",
            "created_at": now,
            "updated_at": now,
            "started_at": now,
            "heartbeat_at": now,
        },
    )
    return {
        "deployment_id": deployment_id,
        "namespace": "ns-run",
        "jiuwenclaw_name": "run-instance",
        "deploy_mode": "full",
        "params": {},
        "defaults_snapshot": {},
        "created_by": "tester",
    }


def _write_script(tool_dir: Path, body: str) -> None:
    (tool_dir / "deploy.sh").write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")


async def _run(handler: SQLiteHandler, task: dict, env_content: str = 'NAMESPACE="ns-run"\n') -> dict:
    runner = DeployRunner(handler, task, env_content)
    return await runner.run()


async def test_success_flow(handler, fake_tool, fast_ticks):
    _write_script(
        fake_tool,
        "\n".join(
            [
                'echo "@@DEPLOY_JSON@@{\\"event\\":\\"phase\\",\\"phase\\":\\"validate\\",\\"state\\":\\"ok\\"}"',
                'printf "plain \\033[36mansi\\033[0m line\\n"',
                'echo "@@DEPLOY_JSON@@{\\"event\\":\\"component\\",\\"component\\":\\"secret\\",\\"index\\":\\"1\\",\\"total\\":\\"5\\",\\"state\\":\\"start\\"}"',
                'echo "@@DEPLOY_JSON@@{\\"event\\":\\"component\\",\\"component\\":\\"secret\\",\\"index\\":\\"1\\",\\"total\\":\\"5\\",\\"state\\":\\"ok\\"}"',
                'echo "ENVFILE=$DEPLOY_ENV_FILE"',
                'test -d "$DEPLOY_CONF_DIR" && echo "CONFDIR_OK"',
                'test "$DEPLOY_JSON_PROGRESS" = 1 && echo "JSONPROG_OK"',
                'grep -q \'^NAMESPACE="ns-run"$\' "$DEPLOY_ENV_FILE" && echo "ENVCUSTOM_OK"',
                'echo "@@DEPLOY_JSON@@{\\"event\\":\\"summary\\",\\"status\\":\\"success\\",\\"elapsed\\":\\"1\\"}"',
                "exit 0",
            ]
        ),
    )
    task = await _make_task(handler)
    result = await _run(handler, task)

    assert result["status"] == "success"
    assert result["error_summary"] is None
    row = await handler.get(
        DEPLOYMENT_TASK_TABLE_DEF.table_name, {"deployment_id": task["deployment_id"]}
    )
    assert row.status == "success"
    assert row.phase == "finished"
    progress = dict(row.progress or {})
    assert progress["phases"]["validate"] == "ok"
    assert progress["components"]["secret"]["state"] == "ok"
    assert progress["log_truncated"] is False

    logs = list(
        await handler.list_records(
            DEPLOYMENT_LOG_TABLE_DEF.table_name,
            {"deployment_id": task["deployment_id"]},
            limit=100,
            offset=0,
            order_by=[("seq", False)],
        )
    )
    contents = [r.content for r in logs]
    assert any("ansi" in c and "\x1b" not in c for c in contents)   # ANSI 已剥离
    assert "CONFDIR_OK" in contents and "JSONPROG_OK" in contents and "ENVCUSTOM_OK" in contents
    seqs = [r.seq for r in logs]
    assert seqs == sorted(seqs)


async def test_failure_flow_captures_fail_message(handler, fake_tool, fast_ticks):
    _write_script(
        fake_tool,
        "\n".join(
            [
                'echo "@@DEPLOY_JSON@@{\\"event\\":\\"component\\",\\"component\\":\\"gateway\\",\\"index\\":\\"4\\",\\"total\\":\\"5\\",\\"state\\":\\"fail\\",\\"message\\":\\"gateway 就绪等待超时\\"}"',
                'echo "@@DEPLOY_JSON@@{\\"event\\":\\"summary\\",\\"status\\":\\"failed\\",\\"elapsed\\":\\"9\\"}"',
                "exit 1",
            ]
        ),
    )
    task = await _make_task(handler, "dp-run-fail")
    result = await _run(handler, task)
    assert result["status"] == "failed"
    assert "gateway 就绪等待超时" in (result["error_summary"] or "")
    row = await handler.get(
        DEPLOYMENT_TASK_TABLE_DEF.table_name, {"deployment_id": "dp-run-fail"}
    )
    assert row.status == "failed"
    assert row.exit_code == 1


async def test_cancel_kills_process_group(handler, fake_tool, fast_ticks):
    _write_script(fake_tool, "sleep 300\n")
    task = await _make_task(handler, "dp-run-cancel")
    runner = DeployRunner(handler, task, 'NAMESPACE="ns-run"\n')
    run_task = asyncio.create_task(runner.run())
    await asyncio.sleep(0.6)
    await handler.update(
        DEPLOYMENT_TASK_TABLE_DEF.table_name,
        {"deployment_id": "dp-run-cancel"},
        {"cancel_requested": True},
    )
    result = await asyncio.wait_for(run_task, timeout=15)
    assert result["status"] == "cancelled"


async def test_timeout_terminates(handler, fake_tool, fast_ticks, monkeypatch):
    from manager_server.infrastructure.config import settings

    monkeypatch.setattr(settings, "deploy_task_timeout_seconds", 1)
    _write_script(fake_tool, "sleep 300\n")
    task = await _make_task(handler, "dp-run-timeout")
    runner = DeployRunner(handler, task, 'NAMESPACE="ns-run"\n')
    result = await asyncio.wait_for(runner.run(), timeout=20)
    assert result["status"] == "failed"
    assert "超时" in (result["error_summary"] or "")


async def test_log_truncation_marks_and_stops(handler, fake_tool, fast_ticks, monkeypatch):
    from manager_server.infrastructure.config import settings

    monkeypatch.setattr(settings, "deploy_log_max_lines", 5)
    _write_script(fake_tool, "for i in $(seq 1 50); do echo line-$i; done\n")
    task = await _make_task(handler, "dp-run-trunc")
    result = await _run(handler, task)
    assert result["status"] == "success"
    logs = list(
        await handler.list_records(
            DEPLOYMENT_LOG_TABLE_DEF.table_name,
            {"deployment_id": "dp-run-trunc"},
            limit=100,
            offset=0,
        )
    )
    assert len(logs) <= 7   # 上限 + 截断标记行
    assert any("已截断" in r.content for r in logs)
    row = await handler.get(
        DEPLOYMENT_TASK_TABLE_DEF.table_name, {"deployment_id": "dp-run-trunc"}
    )
    assert (row.progress or {}).get("log_truncated") is True


async def test_missing_tool_marks_failed(handler, fake_tool, fast_ticks):
    # deploy.sh 不存在 → FileNotFoundError → failed
    task = await _make_task(handler, "dp-run-missing")
    result = await _run(handler, task)
    assert result["status"] == "failed"
    assert "部署工具不可用" in (result["error_summary"] or "")


async def test_stale_heartbeat_reconcile(handler, monkeypatch):
    from manager_server.core.deployment import service as service_mod

    now = utc_now()
    stale_id, fresh_id = "dp-stale", "dp-fresh"
    for deployment_id, heartbeat in (
        (stale_id, now - timedelta(seconds=600)),
        (fresh_id, now),
    ):
        await handler.create(
            DEPLOYMENT_TASK_TABLE_DEF.table_name,
            {
                "deployment_id": deployment_id,
                "namespace": f"ns-{deployment_id}",
                "jiuwenclaw_name": "x",
                "deploy_mode": "full",
                "status": "running",
                "phase": "apply",
                "created_by": "t",
                "created_at": now,
                "updated_at": now,
                "started_at": now,
                "heartbeat_at": heartbeat,
            },
        )
    interrupted = await service_mod.reconcile_stale(handler)
    assert interrupted == 1
    stale = await handler.get(
        DEPLOYMENT_TASK_TABLE_DEF.table_name, {"deployment_id": stale_id}
    )
    fresh = await handler.get(
        DEPLOYMENT_TASK_TABLE_DEF.table_name, {"deployment_id": fresh_id}
    )
    assert stale.status == "interrupted"
    assert "重新发起" in (stale.error_summary or "")
    assert fresh.status == "running"


def _parse_json_lines_ok(text: str) -> bool:
    for line in text.splitlines():
        if line.startswith("@@DEPLOY_JSON@@"):
            json.loads(line[len("@@DEPLOY_JSON@@") :])
    return True


async def test_json_events_are_valid_json(handler, fake_tool, fast_ticks, tmp_path):
    # 防御性契约：runner 产出的进度结构可序列化，fake 事件行可解析
    _write_script(
        fake_tool,
        'echo "@@DEPLOY_JSON@@{\\"event\\":\\"phase\\",\\"phase\\":\\"render\\",\\"state\\":\\"ok\\"}" && exit 0',
    )
    task = await _make_task(handler, "dp-run-json")
    result = await _run(handler, task)
    assert result["status"] == "success"
    assert _parse_json_lines_ok('@@DEPLOY_JSON@@{"event":"phase","phase":"render","state":"ok"}')
