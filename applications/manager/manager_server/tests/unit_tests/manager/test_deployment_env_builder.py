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
        with pytest.raises(ValueError, match="JIUWENBOX_IMAGE"):
            validate_merged_env(merged, namespace="team-a")


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
        assert hosts["user_web_host"] == "http://jiuwenswarm-web.ns1:5173"


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
    """播种优先级：工具树 .env.custom（版本化清单）> 工具默认 > example。"""

    def test_env_custom_wins(self, tmp_path, monkeypatch):
        from manager_server.core.deployment import toolkit, service as svc_mod

        tool_dir = tmp_path / "tool"
        tool_dir.mkdir()
        (tool_dir / "deploy.sh").write_text("#!/bin/sh\n")
        monkeypatch.setattr(toolkit, "resolve_tool_dir", lambda: tool_dir)
        # 工具默认 + example + 版本化 custom
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
        assert seeded["GATEWAY_IMAGE"] == "swr.example/jiuwenswarm-core:0.0.16y"
        assert seeded["AGENT_RUNTIME_IMAGE"] == "swr.example/jiuwenswarm-agent-runtime:0.0.16y"
        assert "CLUSTER_TYPE" not in seeded
        assert seeded["DB_TYPE"] == "mariadb"   # example 次优先仍生效

    def test_env_custom_absent_falls_back(self, tmp_path, monkeypatch):
        from manager_server.core.deployment import toolkit, service as svc_mod

        tool_dir = tmp_path / "tool"
        tool_dir.mkdir()
        monkeypatch.setattr(toolkit, "resolve_tool_dir", lambda: tool_dir)
        (tool_dir / "global_vars.sh").write_text(
            'declare -A DEPLOY_VARS=(\n    ["MARIADB_IMAGE"]="mariadb:11.8"\n)\n'
        )

        class _H:
            async def get(self, table, filters):
                return None

            async def create(self, table, data):
                return None

        seeded = asyncio_run(svc_mod.DeploymentService(_H()).get_defaults())
        assert seeded["MARIADB_IMAGE"] == "mariadb:11.8"
