"""预置技能模板 CreateBody：provider / url 双模式校验。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from manager_server.schemas.template_schemas import (
    SkillPrebuiltTemplateCreateBody,
    SkillPrebuiltTemplateUpdateBody,
)


def test_url_mode_accepts_package_url() -> None:
    body = SkillPrebuiltTemplateCreateBody(
        template_name="weather",
        skill_id="search/weather",
        package_url="https://artifacts.example.com/skills/weather.zip",
    )
    assert body.package_url.startswith("https://")
    assert body.source_id is None


def test_provider_mode_requires_source_and_version() -> None:
    body = SkillPrebuiltTemplateCreateBody(
        template_name="spi-skill",
        skill_id="crm/lead",
        source_id="skillhub",
        version_id="1.2.3",
    )
    assert body.source_id == "skillhub"
    assert body.version_id == "1.2.3"
    assert body.package_url is None


def test_provider_mode_rejects_partial_ids() -> None:
    with pytest.raises(ValidationError) as exc:
        SkillPrebuiltTemplateCreateBody(
            template_name="bad",
            skill_id="x",
            source_id="skillhub",
        )
    assert "provider path requires source_id and version_id" in str(exc.value)


def test_rejects_when_neither_path_present() -> None:
    with pytest.raises(ValidationError) as exc:
        SkillPrebuiltTemplateCreateBody(
            template_name="empty",
            skill_id="x",
        )
    assert "cannot infer install path" in str(exc.value)


def test_provider_preferred_when_both_present() -> None:
    """有完整 SPI 三元组即可通过，即使也带 package_url。"""
    body = SkillPrebuiltTemplateCreateBody(
        template_name="both",
        skill_id="crm/lead",
        package_url="https://artifacts.example.com/skills/lead.zip",
        source_id="skillhub",
        version_id="2.0.0",
    )
    assert body.source_id == "skillhub"
    assert body.package_url is not None


def test_update_blank_package_url_becomes_none() -> None:
    """编辑 SPI 模板时前端会把空 URL 打成 ''，不得触发 min_length=1。"""
    body = SkillPrebuiltTemplateUpdateBody(
        template_name="employment-rights-team",
        skill_id="c0f2e4ba2d7d4cb2b6050de03b92db5b",
        source_id="swarmskillhub",
        version_id="v1.0.0",
        package_url="",
    )
    assert body.package_url is None
