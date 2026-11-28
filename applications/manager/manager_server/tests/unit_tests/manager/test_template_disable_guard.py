"""Agent 资源模板：被引用时禁用应与删除一样被拒绝。"""

from __future__ import annotations

import pytest

from manager_server.core.template.errors import TemplateInUseError
from manager_server.core.template.push_template_to_gateway import (
    assert_template_not_referenced,
)


@pytest.mark.asyncio
async def test_assert_template_not_referenced_raises_for_disable(monkeypatch):
    async def _count(_handler, _template_id, _kind):
        return 2

    monkeypatch.setattr(
        "manager_server.core.template.push_template_to_gateway."
        "count_config_effective_policy_references_for_template",
        _count,
    )

    with pytest.raises(TemplateInUseError) as exc:
        await assert_template_not_referenced(
            handler=None,  # type: ignore[arg-type]
            template_id="tpl-1",
            kind="model_templates",
            action="disable",
        )

    assert exc.value.code == "agent_templates_referenced"
    assert exc.value.count == 2
    assert "cannot disable template" in str(exc.value)


@pytest.mark.asyncio
async def test_assert_template_not_referenced_allows_when_unused(monkeypatch):
    async def _count(_handler, _template_id, _kind):
        return 0

    monkeypatch.setattr(
        "manager_server.core.template.push_template_to_gateway."
        "count_config_effective_policy_references_for_template",
        _count,
    )

    await assert_template_not_referenced(
        handler=None,  # type: ignore[arg-type]
        template_id="tpl-1",
        kind="model_templates",
        action="disable",
    )
