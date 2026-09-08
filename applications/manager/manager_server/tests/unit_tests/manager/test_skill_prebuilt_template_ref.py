"""template_ref：skill_prebuilt 槽位归一化。"""

from __future__ import annotations

from manager_server.infrastructure.template_ref import normalize_template_ref


def test_normalize_keeps_skill_prebuilt_slot() -> None:
    out = normalize_template_ref(
        {
            "skill_prebuilt": ["tpl-c"],
            "skill_whitelist": ["old-1"],
            "default_model": ["mdl-1"],
            "service_config": ["svc-1"],
        }
    )
    assert out["skill_prebuilt"] == ["tpl-c"]
    assert out["skill_whitelist"] == ["old-1"]
    assert out["default_model"] == ["mdl-1"]
    assert "service_config" not in out
