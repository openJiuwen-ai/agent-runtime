# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""react 模板在 Manager 持久化中 body 完整往返（body → data → body）。"""

from __future__ import annotations

import pytest

from conftest import ManagerApiHarness

pytestmark = pytest.mark.unit

_BODY = {
    "agent_name": "main_agent",
    "skill_mode": "all",
    "enable_task_loop": True,
    "enable_read_image_multimodal": None,
    "completion_timeout": None,
    "max_iterations": 30,
    "stream_chunk_threshold": 50,
    "stream_character_threshold": 2000,
    "subagent_runtime": {"enabled": True},
    "subagents": {
        "general_agent": {"enabled": True, "max_iterations": 20},
        "code_agent": {"enabled": False},
    },
    "context_engine_config": {
        "enabled": True,
        "context_window_tokens": 200000,
        "compression_recall_config": {"enabled": False},
        "message_summary_offloader_config": {
            "protected_tool_names": ["read_file"],
            "ttl_seconds": 600,
        },
        "session_memory_compressor_config": {
            "enabled": True,
            "memory": {"update_mode": "direct_replace", "direct_replace_max_retries": 3},
        },
        "dialogue_compressor_config": {"trigger_context_ratio": 0.8},
        "current_round_compressor_config": {"keep_recent_messages": 4},
        "round_level_compressor_config": {
            "trigger_context_ratio": 0.9,
            "min_target_context_ratio": 0.5,
        },
    },
}


async def _create(h: ManagerApiHarness, body: dict) -> dict:
    response = await h.http.post(
        "/api/v1/react-templates",
        json={"template_name": "react-tpl", "body": body},
    )
    assert response.status_code == 200
    return response.json()["data"]


@pytest.mark.asyncio
async def test_react_template_body_round_trips(manager_api: ManagerApiHarness):
    h = manager_api
    created = await _create(h, _BODY)
    path = f"/api/v1/react-templates/{created['template_id']}"
    assert created["body"] == _BODY
    assert (await h.http.get(path)).json()["data"]["body"] == _BODY

    # 普通编辑（不含 body）不丢 data
    renamed = await h.http.patch(path, json={"template_name": "react-tpl-renamed"})
    assert renamed.status_code == 200
    assert renamed.json()["data"]["body"] == _BODY

    # 全量 body 编辑
    changed = {**_BODY, "enable_task_loop": False}
    edited = await h.http.patch(path, json={"body": changed})
    assert edited.status_code == 200
    assert edited.json()["data"]["body"] == changed
