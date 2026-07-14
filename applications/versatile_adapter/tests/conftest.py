# coding: utf-8
"""pytest 公共 fixture：VA 进程级 sidecar 测试用。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 把 versatile_adapter 根目录加入 sys.path，让 tests 能 import adapters/dispatcher 等
_VA_ROOT = Path(__file__).resolve().parent.parent
if str(_VA_ROOT) not in sys.path:
    sys.path.append(str(_VA_ROOT))


@pytest.fixture(autouse=True)
def _va_workflow_adapter_type_workflow(monkeypatch):
    """现有测试的 YAML 配置均使用 type: workflow 适配器，
    默认设置 VA_WORKFLOW_ADAPTER_TYPE=workflow 使 _match_workflow 匹配 workflow 类型。
    需测试 a2a_gateway 路由的用例可覆盖此环境变量。
    """
    monkeypatch.setenv("VA_WORKFLOW_ADAPTER_TYPE", "workflow")


@pytest.fixture
def va_root() -> Path:
    """VA 进程根目录。"""
    return _VA_ROOT


@pytest.fixture
def sample_yaml_text() -> str:
    """覆盖 controller + workflow 两类 adapter 的最小 YAML。"""
    return """\
adapters:
  - name: default_controller
    type: controller
    url_template: "http://mock-host/v1/agents/agent-a/conversations/{conversation_id}"
    timeout: 60
    forward_header_whitelist:
      - x-user-id
      - cookie
    workflow_result_node: GXZQAResponseNode

  - name: wf_knowledge_qa
    type: workflow
    url_template: "http://mock-host/v1/workflows/{workflow_id}/conversations/{conversation_id}"
    timeout: 30
    workflow_id: wf_knowledge_qa
    intent: knowledge_qa
    workflow_result_node: WorkflowQAResponseNode
    forward_header_whitelist:
      - x-trace-id

  - name: wf_wealth
    type: workflow
    url_template: "http://mock-host/v1/workflows/{workflow_id}/conversations/{conversation_id}"
    timeout: 30
    workflow_id: wf_wealth
    intent: "理财推荐"
    workflow_result_node: WealthQAResponseNode
"""


@pytest.fixture
def write_yaml(tmp_path: Path, sample_yaml_text: str):
    """写入一个临时 YAML 配置文件，返回路径。"""

    def _writer(content: str | None = None) -> Path:
        p = tmp_path / "versatile_proxy.yaml"
        p.write_text(content if content is not None else sample_yaml_text, encoding="utf-8")
        return p

    return _writer
