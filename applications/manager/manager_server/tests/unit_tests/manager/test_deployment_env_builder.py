# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""env_builder 单元测试：白名单/强制项/校验/派生/掩码/生成。"""

from __future__ import annotations

import pytest
from asyncio import run as asyncio_run

from manager_server.core.deployment.env_builder import (
    ALLOWED_ENV_KEYS,
    FORCED_ENV,
    build_env_custom,
    derive_db_connection,
    derive_instance_hosts,
    derive_product_databases,
    mask_sensitive_env,
    merge_env,
    validate_merged_env,
)

_MIN_ENV = {
    "AGENT_RUNTIME_IMAGE": "swr.example/jiuwenswarm-agent-runtime:1.0",
    "GATEWAY_IMAGE": "swr.example/jiuwenswarm-core:1.0",
    "WEB_IMAGE": "swr.example/jiuwenswarm-web:1.0",
    "IDENTITY_IMAGE": "swr.example/jiuwenswarm-identity:1.0",
    "MANAGER_SERVER_IMAGE": "swr.example/jiuwenswarm-manager-server:1.0",
    "MANAGER_WEB_IMAGE": "swr.example/jiuwenswarm-manager-web:1.0",
    "MARIADB_IMAGE": "mariadb:11.8",
    "MYSQL_IMAGE": "mysql:8.0",
    "POSTGRESQL_IMAGE": "postgres:16",
    "REDIS_IMAGE": "redis:7-alpine",
    "MINIO_IMAGE": "minio/minio:latest",
    "AGENTSERVER_IMAGE": "swr.example/jiuwenswarm-core:1.0",
    "JIUWENBOX_IMAGE": "swr.example/jiuwenswarm-sandbox:1.0",
    "MARIADB_ROOT_PASSWORD": "Root@123456",
}


class TestMergeEnv:
    def test_whitelist_rejects_unknown_key(self):
        with pytest.raises(ValueError, match="不支持的配置键"):
            merge_env({}, {"EVIL_KEY": "x"})

    def test_forced_env_always_applied(self):
        # CLUSTER_TYPE/MODE 不在白名单：请求传入忽略，强制值由服务端写入
        merged = merge_env(_MIN_ENV, {"CLUSTER_TYPE": "openshift", "MODE": "dev"})
        assert merged["CLUSTER_TYPE"] == "k8s"
        assert merged["MODE"] == "product"
        merged = merge_env(_MIN_ENV, {})
        assert merged["CLUSTER_TYPE"] == "k8s"
        assert merged["MODE"] == "product"

    def test_forced_keys_in_defaults_tolerated(self):
        # 存量脏数据（defaults 表混入强制键）不让任务创建失败（E2E 实测回归）
        poisoned = {**_MIN_ENV, "CLUSTER_TYPE": "openshift", "MODE": "dev"}
        merged = merge_env(poisoned, {})
        assert merged["CLUSTER_TYPE"] == "k8s"
        assert merged["MODE"] == "product"

    def test_override_wins_over_defaults(self):
        merged = merge_env({"GATEWAY_REPLICAS": "1"}, {"GATEWAY_REPLICAS": "3"})
        assert merged["GATEWAY_REPLICAS"] == "3"

    def test_unsafe_value_rejected(self):
        with pytest.raises(ValueError, match="非法字符"):
            merge_env({}, {"DB_PASSWORD": 'bad"quote'})
        with pytest.raises(ValueError, match="非法字符"):
            merge_env({}, {"DB_HOST": "host\ninjection"})

    def test_replica_must_be_positive_int(self):
        with pytest.raises(ValueError, match="正整数"):
            merge_env({}, {"GATEWAY_REPLICAS": "0"})
        with pytest.raises(ValueError, match="正整数"):
            merge_env({}, {"GATEWAY_REPLICAS": "abc"})

    def test_image_charset(self):
        with pytest.raises(ValueError, match="镜像名非法"):
            merge_env({}, {"GATEWAY_IMAGE": "bad image;rm -rf"})

    def test_deploy_mode_enum(self):
        with pytest.raises(ValueError, match="仅支持"):
            merge_env({}, {"AGENT_RUNTIME_DEPLOY_MODE": "off"})

    def test_db_type_enum(self):
        with pytest.raises(ValueError, match="DB_TYPE"):
            merge_env({}, {"DB_TYPE": "sqlite"})


class TestValidateMergedEnv:
    def test_ok(self):
        merged = merge_env(_MIN_ENV, {"AGENT_RUNTIME_DEPLOY_MODE": "full"})
        validate_merged_env(merged, namespace="team-a")

    def test_bad_namespace(self):
        merged = merge_env(_MIN_ENV, {})
        with pytest.raises(ValueError, match="namespace"):
            validate_merged_env(merged, namespace="Bad_NS")
        with pytest.raises(ValueError, match="namespace"):
            validate_merged_env(merged, namespace="-lead")

    def test_runtime_image_required(self):
        merged = merge_env(_MIN_ENV, {})
        merged.pop("AGENT_RUNTIME_IMAGE")
        with pytest.raises(ValueError, match="AGENT_RUNTIME_IMAGE"):
            validate_merged_env(merged, namespace="team-a")

    def test_external_db_requires_password(self):
        no_pw = {k: v for k, v in _MIN_ENV.items() if "PASSWORD" not in k}
        merged = merge_env(no_pw, {"DB_HOST": "db.example.com"})
        with pytest.raises(ValueError, match="DB_PASSWORD"):
            validate_merged_env(merged, namespace="team-a")
        merged = merge_env(no_pw, {"DB_HOST": "db.example.com", "DB_PASSWORD": "p@ss"})
        validate_merged_env(merged, namespace="team-a")

    def test_jiuwenbox_requires_image(self):
        merged = merge_env(_MIN_ENV, {"SANDBOX_TYPE": "jiuwenbox"})
        merged.pop("JIUWENBOX_IMAGE", None)
        with pytest.raises(ValueError, match="JIUWENBOX_IMAGE"):
            validate_merged_env(merged, namespace="team-a")

    def test_k8s_derived_sandbox_requires_image(self):
        # SANDBOX_TYPE 留空时 bash 侧按 CLUSTER_TYPE 派生（k8s→jiuwenbox），
        # 创建时须按派生后语义校验，避免任务起跑后才在 validate 阶段失败
        no_sandbox = {k: v for k, v in _MIN_ENV.items() if k != "JIUWENBOX_IMAGE"}
        merged = merge_env(no_sandbox, {})
        with pytest.raises(ValueError, match="JIUWENBOX_IMAGE"):
            validate_merged_env(merged, namespace="team-a")
        # 显式 off 不需要沙箱镜像
        merged = merge_env(no_sandbox, {"SANDBOX_TYPE": "off"})
        validate_merged_env(merged, namespace="team-a")

    def test_openshift_derived_sandbox_off(self, monkeypatch):
        from manager_server.infrastructure.config import settings
        monkeypatch.setattr(settings, "deploy_cluster_type", "openshift")
        no_sandbox = {k: v for k, v in _MIN_ENV.items() if k != "JIUWENBOX_IMAGE"}
        merged = merge_env(no_sandbox, {})
        assert merged["CLUSTER_TYPE"] == "openshift"
        validate_merged_env(merged, namespace="team-a")


def test_web_server_parameters_survive_task_generation():
    values = {
        "WEB_SERVER_IMAGE": "swr.example/web-server:1.0",
        "WEB_SERVER_REPLICAS": "2",
        "WEB_SERVER_DB_USER": "web_server_team_a",
        "WEB_SERVER_DB_PASSWORD": "Readonly@1",
        "WEB_NODE_PORT": "30080",
    }
    merged = merge_env({}, values)
    content = build_env_custom(merged, namespace="team-a")
    for key, value in values.items():
        assert f'{key}="{value}"' in content
    masked = mask_sensitive_env(merged)
    assert masked["WEB_SERVER_DB_PASSWORD"] == "******"
    assert masked["WEB_SERVER_DB_USER"] == "web_server_team_a"


@pytest.mark.parametrize(
    "key,value",
    [("WEB_SERVER_IMAGE", "bad image"), ("WEB_SERVER_REPLICAS", "0")],
)
def test_web_server_rejects_invalid_task_parameters(key, value):
    with pytest.raises(ValueError):
        merge_env({}, {key: value})


class TestBuildEnvCustom:
    def test_lines_are_quoted_and_namespace_first(self):
        content = build_env_custom({"GATEWAY_REPLICAS": "2"}, namespace="team-a")
        lines = content.strip().splitlines()
        assert lines[0] == 'NAMESPACE="team-a"'
        assert 'GATEWAY_REPLICAS="2"' in lines


class TestDerive:
    def test_builtin_mariadb_connection(self):
        conn = derive_db_connection({"DB_TYPE": "mariadb", "MARIADB_ROOT_PASSWORD": "pw"})
        assert conn == {
            "db_type": "mariadb",
            "host": "mariadb-headless.default",
            "port": 3306,
            "user": "root",
            "password": "pw",
        }

    def test_external_pg_connection(self):
        conn = derive_db_connection(
            {"DB_TYPE": "postgresql", "DB_HOST": "pg.example", "DB_PORT": "5433",
             "DB_USER": "app", "DB_PASSWORD": "pw2"}
        )
        assert conn["host"] == "pg.example"
        assert conn["port"] == 5433
        assert conn["user"] == "app"
        assert conn["password"] == "pw2"

    def test_mysql_product_databases_namespaced(self):
        dbs = derive_product_databases({"DB_TYPE": "mysql"}, namespace="ns1")
        assert [d["database"] for d in dbs] == ["gateway_ns1", "web_ns1"]
        assert all(d["schema"] is None for d in dbs)

    def test_pg_product_databases_schema_isolated(self):
        dbs = derive_product_databases({"DB_TYPE": "postgresql"}, namespace="ns1")
        assert [d["database"] for d in dbs] == ["gateway", "web"]
        assert [d["schema"] for d in dbs] == ["ns1", "ns1"]

    def test_explicit_db_name_respected(self):
        dbs = derive_product_databases(
            {"DB_TYPE": "mysql", "GATEWAY_DB_NAME": "custom_gw"}, namespace="ns1"
        )
        assert dbs[0]["database"] == "custom_gw"

    def test_instance_hosts_match_frontend_convention(self):
        hosts = derive_instance_hosts("ns1")
        assert hosts["gateway_host"] == "http://jiuwenswarm-gateway.ns1:19000"
        assert hosts["runtime_host"] == "http://jiuwenswarm-agent-runtime.ns1:8091"


class TestMask:
    def test_passwords_masked(self):
        masked = mask_sensitive_env(
            {"DB_PASSWORD": "secret", "OBS_ACCESS_KEY": "ak", "GATEWAY_IMAGE": "img:1"}
        )
        assert masked["DB_PASSWORD"] == "******"
        assert masked["OBS_ACCESS_KEY"] == "******"
        assert masked["GATEWAY_IMAGE"] == "img:1"

    def test_all_allowed_keys_are_covered_somewhere(self):
        # 白名单键要么可校验、要么属于镜像/副本/形态分组（防手滑加入未处理键）
        assert "AGENT_RUNTIME_DEPLOY_MODE" in ALLOWED_ENV_KEYS
        assert "CLUSTER_TYPE" not in ALLOWED_ENV_KEYS


class TestSeedingFromEnvCustom:
    """播种来源：.env.custom 是唯一真源；缺失或未覆盖的键一律不播种。"""

    def test_env_custom_is_sole_source(self, tmp_path, monkeypatch):
        from manager_server.core.deployment import toolkit, service as svc_mod

        tool_dir = tmp_path / "tool"
        tool_dir.mkdir()
        (tool_dir / "deploy.sh").write_text("#!/bin/sh\n")
        monkeypatch.setattr(toolkit, "resolve_tool_dir", lambda: tool_dir)
        # 工具默认 + example 提供干扰值；版本化 custom 只提供两个镜像
        (tool_dir / "global_vars.sh").write_text(
            'declare -A DEPLOY_VARS=(\n    ["GATEWAY_IMAGE"]="example/gw:public"\n)\n'
        )
        (tool_dir / ".env.example").write_text('GATEWAY_IMAGE="example/gw:example"\nDB_TYPE=mariadb\n')
        (tool_dir / ".env.custom").write_text(
            'GATEWAY_IMAGE="swr.example/jiuwenswarm-core:0.0.16y"\n'
            'AGENT_RUNTIME_IMAGE="swr.example/jiuwenswarm-agent-runtime:0.0.16y"\n'
            'CLUSTER_TYPE=k8s\n'   # 白名单外 → 过滤
        )

        class _H:  # 最小 handler：get_defaults 直接走播种分支
            async def get(self, table, filters):
                return None

            async def create(self, table, data):
                return None

        seeded = asyncio_run(svc_mod.DeploymentService(_H()).get_defaults())
        # 唯一真源：不被工具默认/example 稀释，未覆盖键（DB_TYPE 等）不播种
        assert seeded == {
            "GATEWAY_IMAGE": "swr.example/jiuwenswarm-core:0.0.16y",
            "AGENT_RUNTIME_IMAGE": "swr.example/jiuwenswarm-agent-runtime:0.0.16y",
        }

    def test_env_custom_absent_seeds_empty(self, tmp_path, monkeypatch):
        from manager_server.core.deployment import toolkit, service as svc_mod

        tool_dir = tmp_path / "tool"
        tool_dir.mkdir()
        monkeypatch.setattr(toolkit, "resolve_tool_dir", lambda: tool_dir)
        # 仅有工具默认/example，没有 .env.custom → 播种为空（其余来源不准确，宁缺毋滥）
        (tool_dir / "global_vars.sh").write_text(
            'declare -A DEPLOY_VARS=(\n    ["MARIADB_IMAGE"]="mariadb:11.8"\n)\n'
        )
        (tool_dir / ".env.example").write_text('MARIADB_IMAGE="mariadb:11.8"\n')

        class _H:
            async def get(self, table, filters):
                return None

            async def create(self, table, data):
                return None

        seeded = asyncio_run(svc_mod.DeploymentService(_H()).get_defaults())
        assert seeded == {}


class TestOcpClusterType:
    """openshift 形态：强制键随 manager 所在集群切换，API 地址入白名单。"""

    def test_forced_cluster_type_openshift(self, monkeypatch):
        from manager_server.infrastructure.config import settings

        monkeypatch.setattr(settings, "deploy_cluster_type", "openshift")
        merged = merge_env(_MIN_ENV, {})
        assert merged["CLUSTER_TYPE"] == "openshift"
        assert merged["MODE"] == "product"

    def test_forced_cluster_type_invalid_falls_back(self, monkeypatch):
        from manager_server.infrastructure.config import settings

        monkeypatch.setattr(settings, "deploy_cluster_type", " bogus ")
        assert merge_env(_MIN_ENV, {})["CLUSTER_TYPE"] == "k8s"

    def test_cluster_api_server_whitelisted_and_validated(self):
        merged = merge_env(
            _MIN_ENV,
            {"CLUSTER_API_SERVER": "https://api.ocp.example:6443", "CLUSTER_API_INSECURE": "true"},
        )
        assert merged["CLUSTER_API_SERVER"] == "https://api.ocp.example:6443"
        with pytest.raises(ValueError, match="http\\(s\\) URL"):
            merge_env(_MIN_ENV, {"CLUSTER_API_SERVER": "ftp://bad"})
        with pytest.raises(ValueError, match="true/false"):
            merge_env(_MIN_ENV, {"CLUSTER_API_INSECURE": "yes"})

    def test_tool_available_openshift_branch(self, monkeypatch, tmp_path):
        import shutil as _shutil
        from manager_server.core.deployment import toolkit
        from manager_server.infrastructure.config import settings

        tool_dir = tmp_path / "tool"
        tool_dir.mkdir()
        (tool_dir / "deploy.sh").write_text("#!/bin/sh\n")
        token_file = tmp_path / "token"
        token_file.write_text("sha256~abc")

        monkeypatch.setattr(toolkit, "resolve_tool_dir", lambda: tool_dir)
        monkeypatch.setattr(settings, "deploy_cluster_type", "openshift")
        monkeypatch.setattr(_shutil, "which", lambda c: "/usr/bin/" + c)
        monkeypatch.setattr(settings, "deploy_token", str(tmp_path / "missing"))
        ok, reason = toolkit.check_tool_available()
        assert not ok and "token" in reason

        monkeypatch.setattr(settings, "deploy_token", str(token_file))
        ok, reason = toolkit.check_tool_available()
        assert ok, reason


class TestOcpDbHost:
    """内置 DB 实例地址随形态：k8s=default 共享；openshift=任务目标 ns。"""

    def test_k8s_default_host(self):
        conn = derive_db_connection({"DB_TYPE": "mariadb", "CLUSTER_TYPE": "k8s"})
        assert conn["host"] == "mariadb-headless.default"

    def test_openshift_namespace_host(self):
        conn = derive_db_connection(
            {"DB_TYPE": "mariadb", "CLUSTER_TYPE": "openshift"}, namespace="proj-x"
        )
        assert conn["host"] == "mariadb-headless.proj-x"

    def test_external_host_unaffected(self):
        conn = derive_db_connection(
            {"DB_TYPE": "mariadb", "CLUSTER_TYPE": "openshift", "DB_HOST": "db.example"},
            namespace="proj-x",
        )
        assert conn["host"] == "db.example"


class TestManagerPlaneNs:
    def test_detect_explicit_env_wins(self, monkeypatch):
        from manager_server.core.deployment import env_builder
        monkeypatch.setattr(env_builder, "_SA_NAMESPACE_FILE", "/nonexistent")
        monkeypatch.setenv("MANAGER_PLANE_NS", "explicit-ns")
        assert env_builder._detect_manager_plane_ns() == "explicit-ns"

    def test_detect_from_sa_namespace_file(self, monkeypatch, tmp_path):
        from manager_server.core.deployment import env_builder
        ns_file = tmp_path / "namespace"
        ns_file.write_text("pod-ns\n", encoding="utf-8")
        monkeypatch.setattr(env_builder, "_SA_NAMESPACE_FILE", str(ns_file))
        monkeypatch.delenv("MANAGER_PLANE_NS", raising=False)
        assert env_builder._detect_manager_plane_ns() == "pod-ns"

    def test_detect_source_run_empty(self, monkeypatch):
        from manager_server.core.deployment import env_builder
        monkeypatch.setattr(env_builder, "_SA_NAMESPACE_FILE", "/nonexistent")
        monkeypatch.delenv("MANAGER_PLANE_NS", raising=False)
        assert env_builder._detect_manager_plane_ns() == ""

    def test_forced_env_carries_plane_ns(self, monkeypatch):
        from manager_server.core.deployment import env_builder
        monkeypatch.setenv("MANAGER_PLANE_NS", "mgr-ns")
        merged = merge_env(_MIN_ENV, {})
        assert merged["MANAGER_PLANE_NS"] == "mgr-ns"
        assert 'MANAGER_PLANE_NS="mgr-ns"' in build_env_custom(merged, namespace="team-a")

    def test_plane_ns_in_inputs_ignored(self):
        # 存量脏数据（defaults/请求混入强制键）不落任务 env，服务端值唯一
        poisoned = {**_MIN_ENV, "MANAGER_PLANE_NS": "evil-ns"}
        merged = merge_env(poisoned, {"MANAGER_PLANE_NS": "evil-ns2"})
        assert "MANAGER_PLANE_NS" not in merged or merged["MANAGER_PLANE_NS"] != "evil-ns2"

    def test_manager_plane_db_env_trio(self, monkeypatch):
        from manager_server.core.deployment import env_builder
        monkeypatch.setenv("MANAGER_DB_NAME", "manager_wmq")
        monkeypatch.setenv("WEB_SERVER_DB_USER", "user_backend_wmq")
        monkeypatch.setenv("WEB_SERVER_DB_PASSWORD", "Readonly@1")
        assert env_builder._manager_plane_db_env() == {
            "MANAGER_DB_NAME": "manager_wmq",
            "WEB_SERVER_DB_USER": "user_backend_wmq",
            "WEB_SERVER_DB_PASSWORD": "Readonly@1",
        }
        # 三键不全整体不注入（源码运行无这些 env）
        monkeypatch.delenv("WEB_SERVER_DB_USER", raising=False)
        assert env_builder._manager_plane_db_env() == {}

    def test_plane_defaults_fill_only_missing(self, monkeypatch):
        from manager_server.core.deployment import env_builder
        monkeypatch.setattr(env_builder, "_SA_NAMESPACE_FILE", "/nonexistent")
        for key, value in (
            ("MANAGER_DB_NAME", "manager_wmq"),
            ("WEB_SERVER_DB_USER", "user_backend_wmq"),
            ("WEB_SERVER_DB_PASSWORD", "Readonly@1"),
        ):
            monkeypatch.setenv(key, value)
        # 无 plane ns（源码运行）：不补全，维持按部署 ns 派生的单 ns 语义
        assert env_builder.manager_plane_defaults({}) == {}
        # 有 plane ns（集群内）：缺失键补管理面事实
        monkeypatch.setenv("MANAGER_PLANE_NS", "wmq")
        seeded = env_builder.manager_plane_defaults({})
        assert seeded["MANAGER_DB_NAME"] == "manager_wmq"
        assert seeded["WEB_SERVER_DB_USER"] == "user_backend_wmq"
        # 显式 defaults 优先：已配置的只读账号不被覆盖（补全结果不含该键，合成时保留显式值）
        partial = env_builder.manager_plane_defaults({"WEB_SERVER_DB_USER": "custom"})
        assert "WEB_SERVER_DB_USER" not in partial
        assert "MANAGER_DB_NAME" in partial
        assert {**partial, **{"WEB_SERVER_DB_USER": "custom"}}["WEB_SERVER_DB_USER"] == "custom"
        # 合成后经 merge_env 白名单放行，任务 env 带管理面库事实
        merged = merge_env({**seeded, **_MIN_ENV}, {})
        assert merged["MANAGER_DB_NAME"] == "manager_wmq"
        assert 'MANAGER_DB_NAME="manager_wmq"' in build_env_custom(merged, namespace="team-a")
