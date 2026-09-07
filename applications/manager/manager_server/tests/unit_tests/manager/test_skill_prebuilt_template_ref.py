"""template_ref：废弃槽位 skill_whitelist 被丢弃。"""

from __future__ import annotations

from manager_server.infrastructure.template_ref import normalize_template_ref


def test_normalize_drops_skill_whitelist_slot() -> None:
    out = normalize_template_ref(
        {
            "skill_whitelist": ["tpl-a", "tpl-b"],
            "skill_prebuilt": ["tpl-c"],
            "default_model": ["mdl-1"],
        }
    )
    assert "skill_whitelist" not in out
    assert out["skill_prebuilt"] == ["tpl-c"]
    assert out["default_model"] == ["mdl-1"]
