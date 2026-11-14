# coding: utf-8
"""K8S_DEFAULT_CONFIG_PATH 的 JSON 形状校验。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.config import Settings


def _settings_for(tmp_path, text: str) -> Settings:
    path = tmp_path / "k8s-defaults.json"
    path.write_text(text, encoding="utf-8")
    return Settings(K8S_DEFAULT_CONFIG_PATH=str(path))


def test_object_defaults_are_returned(tmp_path):
    settings = _settings_for(tmp_path, '{"namespace": "adx", "replicas": 2}')
    assert settings.get_k8s_defaults() == {"namespace": "adx", "replicas": 2}


def test_non_object_defaults_are_ignored(tmp_path):
    for text in ("[]", '"namespace"', "1", "null"):
        settings = _settings_for(tmp_path, text)
        assert settings.get_k8s_defaults() == {}, text


def test_malformed_defaults_are_ignored(tmp_path):
    settings = _settings_for(tmp_path, "{not json")
    assert settings.get_k8s_defaults() == {}
