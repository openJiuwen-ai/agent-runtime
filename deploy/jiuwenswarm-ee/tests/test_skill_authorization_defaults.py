# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Exercise deployment defaults through the real Redis resolver and env renderer."""

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_FOR_TESTS") or shutil.which("bash")
pytestmark = pytest.mark.skipif(
    not BASH
    or subprocess.run(
        [BASH, "-c", "(( BASH_VERSINFO[0] >= 4 ))"], check=False
    ).returncode
    != 0,
    reason="Deployment scripts require Bash 4+; select it with BASH_FOR_TESTS",
)


def render(tmp_path, overrides=None):
    assignments = "\n".join(
        f"DEPLOY_VARS[{shlex.quote(key)}]={shlex.quote(value)}"
        for key, value in (overrides or {}).items()
    )
    script = f"""
set -euo pipefail
source {shlex.quote(str(DEPLOY / "global_vars.sh"))}
source {shlex.quote(str(DEPLOY / "check_handler.sh"))}
source {shlex.quote(str(DEPLOY / "template_handler.sh"))}
info() {{ :; }}
success() {{ :; }}
warning() {{ :; }}
error() {{ echo "$*" >&2; exit 1; }}
exec_cmd() {{ "$@"; }}
# Existing Redis Deployment; this check performs no Kubernetes operation.
check_k8s_resource_exists() {{ return 0; }}
DEPLOY_VARS[OS_TYPE]=linux
{assignments}
check_vars
ensure_redis_up
render_config_template {shlex.quote(str(DEPLOY / "templates/agentserver.template.env"))} \
    {shlex.quote(str(tmp_path / "agentserver.env"))} DEPLOY_VARS
"""
    result = subprocess.run(
        [BASH, "-c", script], text=True, capture_output=True, check=False
    )
    return result


def values(tmp_path, overrides=None):
    result = render(tmp_path, overrides)
    assert result.returncode == 0, result.stderr
    return dict(
        line.split("=", 1)
        for line in (tmp_path / "agentserver.env").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )


def test_existing_installation_needs_no_new_settings(tmp_path):
    env = values(tmp_path)
    assert env["SKILL_GRANT_STORE_BACKEND"] == "redis"
    assert env["REDIS_HOST"] == "jiuwenclaw-redis"
    assert env["REDIS_MODE"] == "standalone"
    assert env["REDIS_DB"] == "0"
    assert env["SKILL_GRANT_STORE_NAMESPACE"] == "jiuwenclaw:skill-state:default"
    assert env["SKILL_GRANT_STORE_TTL"] == "604800"
    assert env["SKILL_GRANT_STORE_OPERATION_TIMEOUT"] == "0.3"
    assert "REDIS_PASSWORD" not in env  # Credentials continue to come from Secret.


def test_same_external_redis_is_isolated_by_deployment(tmp_path):
    a = values(tmp_path, {"NAMESPACE": "team-a", "REDIS_HOST": "redis.example"})
    b = values(tmp_path, {"NAMESPACE": "team-b", "REDIS_HOST": "redis.example"})
    assert a["REDIS_HOST"] == b["REDIS_HOST"] == "redis.example"
    assert a["SKILL_GRANT_STORE_NAMESPACE"] != b["SKILL_GRANT_STORE_NAMESPACE"]


def test_overrides_and_cluster_connection_are_forwarded(tmp_path):
    overrides = {
        "REDIS_MODE": "cluster",
        "REDIS_HOST": "redis-a:7000,redis-b:7001",
        "REDIS_PORT": "7000",
        "REDIS_USER": "app-user",
        "REDIS_KEY_PREFIX": "tenant",
        "SKILL_GRANT_STORE_NAMESPACE": "stable-deployment",
        "SKILL_GRANT_STORE_TTL": "1209600",
        "SKILL_GRANT_STORE_OPERATION_TIMEOUT": "0.5",
    }
    env = values(tmp_path, overrides)
    assert all(env[key] == value for key, value in overrides.items())


def test_explicit_memory_opt_out_and_prefix_normalization(tmp_path):
    env = values(
        tmp_path, {"SKILL_GRANT_STORE_BACKEND": "memory", "REDIS_KEY_PREFIX": "tenant"}
    )
    assert env["SKILL_GRANT_STORE_BACKEND"] == "memory"
    assert env["SKILL_GRANT_STORE_NAMESPACE"] == "tenant:skill-state:default"


@pytest.mark.parametrize(
    "overrides",
    [
        {"SKILL_GRANT_STORE_BACKEND": "invalid"},
        {"SKILL_GRANT_STORE_TTL": "0"},
        {"SKILL_GRANT_STORE_TTL": "-1"},
        {"SKILL_GRANT_STORE_OPERATION_TIMEOUT": "0"},
        {"SKILL_GRANT_STORE_OPERATION_TIMEOUT": "5.1"},
        {"SKILL_GRANT_STORE_OPERATION_TIMEOUT": "invalid"},
    ],
)
def test_invalid_settings_stop_before_rendering(tmp_path, overrides):
    result = render(tmp_path, overrides)
    assert result.returncode != 0
    assert "SKILL_GRANT_STORE" in result.stderr
    assert not (tmp_path / "agentserver.env").exists()
