# coding: utf-8
"""pytest 共享 fixtures：fakeredis + SM/RM 状态门面。

fakeredis 注意事项（CLAUDE.md 陷阱清单）：
- pubsub 需共享同一 FakeRedis 实例（本文件所有门面共享同一 client）；
- EVAL 内 PUBLISH 依赖 lupa（fakeredis[lua]）；缺失时相关用例自动 skip。
"""

from __future__ import annotations

import pytest
from fakeredis.aioredis import FakeRedis

from agent_runtime.resource_manager.state import ResourceState
from agent_runtime.session_manager.state import SessionState

HAS_LUA = True
try:
    import lupa  # noqa: F401
except ImportError:
    HAS_LUA = False

requires_lua = pytest.mark.skipif(
    not HAS_LUA, reason="fakeredis Lua 脚本支持需要 lupa（pip install fakeredis[lua]）"
)


@pytest.fixture
async def redis_client():
    client = FakeRedis()
    yield client
    await client.flushall()
    await client.aclose()


@pytest.fixture
def sm_state(redis_client) -> SessionState:
    return SessionState(redis_client)


@pytest.fixture
def rm_state(redis_client) -> ResourceState:
    return ResourceState(redis_client)


@pytest.fixture
async def db_handler(tmp_path):
    """文件型 SQLite（:memory: 在 SQLAlchemy 连接池下会丢表）。"""
    from openjiuwen_runtime.foundation.db import SQLiteHandler

    from agent_runtime.session_manager.config_store import (
        ROUTING_SCOPE_TABLE_DEF,
        SERVICE_CONFIG_CONTAINER_TABLE_DEF,
        SERVICE_CONFIG_TEMPLATE_TABLE_DEF,
    )

    handler = SQLiteHandler(str(tmp_path / "test.db"))
    await handler.connect()
    await handler.init_table(SERVICE_CONFIG_TEMPLATE_TABLE_DEF)
    await handler.init_table(SERVICE_CONFIG_CONTAINER_TABLE_DEF)
    await handler.init_table(ROUTING_SCOPE_TABLE_DEF)
    yield handler
    await handler.disconnect()


# -------------------------------------------------------------- 三段式载荷助手

def split_sync_payload(templates: list[dict], scopes: list[dict] | None = None) -> dict:
    """legacy 内联形态模板 dict 列表 → 三段式 config_sync 载荷(测试助手)。

    wire 层收紧为三段式独占(2026-08-31)后,存量测试的 legacy 风格模板构造
    (agent_image/sidecars/agent_*_mounts 等内联键)经本助手无损转成
    containers/templates/scopes——测试体保持 legacy 拼写零改动。
    转换是值级等价的(同值必同 deploy_ver,见 test_split_contract_*)。

    主容器 id = ``c-{template_id}``;sidecar = ``c-{template_id}-sc{i}``;
    卷 id = ``v{i}-{kind}``(模板内唯一)。
    """
    containers: list[dict] = []
    split_templates: list[dict] = []
    for t in templates:
        tid = t["template_id"]
        volumes: list[dict] = []
        mounts: list[dict] = []

        def _vol(source: dict, kind: str, mount: dict) -> None:
            name = f"v{len(volumes)}-{kind}"
            volumes.append({"name": name, **source})
            mounts.append({"name": name, **mount})

        sse_port = t.get("sse_port", 8080)
        container_port = t.get("container_port", 8080)
        ports: list[dict] = [{"name": "sse", "containerPort": sse_port}]
        if container_port != sse_port:
            ports.append({"name": "http", "containerPort": container_port})
        probe: dict = {"httpGet": {"path": t.get("health_path", "/health"),
                                   "port": sse_port},
                       "initialDelaySeconds": t.get("readiness_initial_delay", 5),
                       "periodSeconds": t.get("readiness_period", 5)}
        secctx = {wire: t[legacy] for wire, legacy in
                  (("runAsUser", "run_as_user"), ("runAsGroup", "run_as_group"))
                  if t.get(legacy) is not None}
        resources = {}
        for wire, legacy in (("cpu", "agent_cpu_request"), ("memory", "agent_memory_request")):
            if t.get(legacy): resources.setdefault("requests", {})[wire] = t[legacy]
        for wire, legacy in (("cpu", "agent_cpu_limit"), ("memory", "agent_memory_limit")):
            if t.get(legacy): resources.setdefault("limits", {})[wire] = t[legacy]
        env_from = [
            {"prefix": e.get("prefix"),
             **({"secretRef": e["secret_ref"]} if e.get("secret_ref")
                else {"configMapRef": e["config_map_ref"]})}
            for e in (t.get("agent_env_from") or [])]
        if t.get("nfs_server"):
            _vol({"nfs": {"server": t["nfs_server"],
                          **({"path": t["nfs_path"]} if t.get("nfs_path") else {})}},
                 "nfs", {"mountPath": t.get("nfs_mount_path") or "/data"})
        for kind, legacy_key in (("hp", "agent_host_path_mounts"),
                                 ("cm", "agent_configmap_mounts"),
                                 ("pvc", "agent_pvc_mounts")):
            for m in t.get(legacy_key) or []:
                if kind == "hp":
                    source = {"hostPath": {
                        "path": m["host_path"],
                        **({"type": m["host_path_type"]} if m.get("host_path_type") else {})}}
                    mount = {"mountPath": m["mount_path"],
                             **({"readOnly": m["read_only"]} if m.get("read_only") is not None else {})}
                elif kind == "cm":
                    source = {"configMap": {
                        "name": m["config_map_name"],
                        **({"items": m["items"]} if m.get("items") else {})}}
                    mount = {"mountPath": m["mount_path"],
                             **({"subPath": m["sub_path"]} if m.get("sub_path") else {}),
                             **({"readOnly": m["read_only"]} if m.get("read_only") is not None else {})}
                else:
                    source = {"persistentVolumeClaim": {"claimName": m["claim_name"]}}
                    mount = {"mountPath": m["mount_path"],
                             **({"readOnly": m["read_only"]} if m.get("read_only") is not None else {})}
                _vol(source, kind, mount)

        sidecar_ids: list[str] = []
        for i, sc in enumerate(t.get("sidecars") or []):
            cid = f"c-{tid}-sc{i}"
            sidecar_ids.append(cid)
            sc_mounts: list[dict] = []
            for kind, items in (("hp", sc.get("host_path_mounts") or []),
                                ("cm", sc.get("configmap_mounts") or []),
                                ("pvc", sc.get("pvc_mounts") or [])):
                for m in items:
                    name = f"v{len(volumes)}-{kind}"
                    if kind == "hp":
                        volumes.append({"name": name, "hostPath": {
                            "path": m["host_path"],
                            **({"type": m["host_path_type"]} if m.get("host_path_type") else {})}})
                        sc_mounts.append({"name": name, "mountPath": m["mount_path"],
                                          **({"readOnly": m["read_only"]} if m.get("read_only") is not None else {})})
                    elif kind == "cm":
                        volumes.append({"name": name, "configMap": {
                            "name": m["config_map_name"],
                            **({"items": m["items"]} if m.get("items") else {})}})
                        sc_mounts.append({"name": name, "mountPath": m["mount_path"],
                                          **({"subPath": m["sub_path"]} if m.get("sub_path") else {}),
                                          **({"readOnly": m["read_only"]} if m.get("read_only") is not None else {})})
                    else:
                        volumes.append({"name": name, "persistentVolumeClaim": {
                            "claimName": m["claim_name"]}})
                        sc_mounts.append({"name": name, "mountPath": m["mount_path"],
                                          **({"readOnly": m["read_only"]} if m.get("read_only") is not None else {})})
            sc_secctx = {}
            if sc.get("privileged"): sc_secctx["privileged"] = True
            if sc.get("capabilities_add") or sc.get("capabilities_drop"):
                sc_secctx["capabilities"] = {
                    **({"add": sc["capabilities_add"]} if sc.get("capabilities_add") else {}),
                    **({"drop": sc["capabilities_drop"]} if sc.get("capabilities_drop") else {})}
            if sc.get("seccomp_unconfined"):
                sc_secctx["seccompProfile"] = {"type": "Unconfined"}
            if sc.get("apparmor_unconfined"):
                sc_secctx["appArmorProfile"] = {"type": "Unconfined"}
            for wire, legacy in (("runAsUser", "run_as_user"),
                                 ("runAsGroup", "run_as_group")):
                if sc.get(legacy) is not None: sc_secctx[wire] = sc[legacy]
            sc_resources = {}
            for wire, legacy in (("cpu", "cpu_request"), ("memory", "memory_request")):
                if sc.get(legacy): sc_resources.setdefault("requests", {})[wire] = sc[legacy]
            for wire, legacy in (("cpu", "cpu_limit"), ("memory", "memory_limit")):
                if sc.get(legacy): sc_resources.setdefault("limits", {})[wire] = sc[legacy]
            sc_probe: dict = {}
            if sc.get("readiness_probe_type"):
                handler_key = "tcpSocket" if sc["readiness_probe_type"] == "tcp" else "httpGet"
                sc_probe[handler_key] = {"port": sc["port"]}
                if handler_key == "httpGet" and sc.get("readiness_path"):
                    sc_probe[handler_key]["path"] = sc["readiness_path"]
            if sc.get("readiness_initial_delay") is not None:
                sc_probe["initialDelaySeconds"] = sc["readiness_initial_delay"]
            if sc.get("readiness_period") is not None:
                sc_probe["periodSeconds"] = sc["readiness_period"]
            if sc.get("readiness_timeout_seconds") is not None:
                sc_probe["timeoutSeconds"] = sc["readiness_timeout_seconds"]
            sc_env_from = [
                {"prefix": e.get("prefix"),
                 **({"secretRef": e["secret_ref"]} if e.get("secret_ref")
                    else {"configMapRef": e["config_map_ref"]})}
                for e in (sc.get("env_from") or [])]
            wire_sc = {
                "container_id": cid,
                "name": sc["name"],
                "image": sc["image"],
                "securityContext": sc_secctx or None,
                **({"ports": [{"containerPort": sc["port"]}]} if sc.get("port") else {}),
                **({"env": [{"name": k, "value": v} for k, v in sc["env"].items()]}
                   if sc.get("env") else {}),
                **({"envFrom": sc_env_from} if sc_env_from else {}),
                **({"imagePullPolicy": sc["image_pull_policy"]}
                   if sc.get("image_pull_policy") else {}),
                **({"resources": sc_resources} if sc_resources else {}),
                **({"volumeMounts": sc_mounts} if sc_mounts else {}),
                **({"readinessProbe": sc_probe} if sc_probe else {}),
            }
            wire_sc = {k: v for k, v in wire_sc.items() if v is not None}
            containers.append(wire_sc)

        main = {
            "container_id": f"c-{tid}",
            "name": t.get("container_name", "agent"),
            "image": t.get("agent_image", ""),
            "ports": ports,
            "readinessProbe": probe,
            **({"env": [{"name": k, "value": v} for k, v in t["agent_env"].items()]}
               if t.get("agent_env") else {}),
            **({"envFrom": env_from} if env_from else {}),
            **({"imagePullPolicy": t["image_pull_policy"]}
               if t.get("image_pull_policy") else {}),
            **({"resources": resources} if resources else {}),
            **({"securityContext": secctx} if secctx else {}),
            **({"volumeMounts": mounts} if mounts else {}),
        }
        containers.append(main)

        template = {k: v for k, v in t.items() if k in (
            "template_id", "template_name", "description", "enabled", "data",
            "namespace", "pod_name", "sse_path", "kubeconfig", "ready_timeout",
            "ready_poll_interval", "scope_concurrency", "pod_concurrency",
            "session_ttl", "pod_ttl", "min_idle_pods", "message_timeout")}
        if "node_name" in t:
            template["nodeName"] = t["node_name"]
        template["main_container_id"] = f"c-{tid}"
        if sidecar_ids:
            template["sidecar_container_ids"] = sidecar_ids
        if volumes:
            template["volumes"] = volumes
        split_templates.append(template)

    if scopes is None:
        scopes = [{"scope_id": "scope-main", "index": 0,
                   "template_id": templates[0]["template_id"],
                   "routing_rules": ""}] if templates else []
    return {"containers": containers, "templates": split_templates,
            "scopes": scopes}


class Runtime:
    """组件全链路装配：SM（orchestrator/sweeper/facade/config_store）+ RM
    （orchestrator/sweeper/facade）+ FakeK8s，共享一个 fakeredis。"""

    def __init__(self, db, redis_client, k8s, *, scope_full_timeout: float = 30.0):
        from agent_runtime.resource_manager.facade import ResourceManagerFacade
        from agent_runtime.resource_manager.orchestrator import ResourceOrchestrator
        from agent_runtime.resource_manager.sweeper import ResourceSweeper
        from agent_runtime.session_manager.config_store import ConfigStore
        from agent_runtime.session_manager.facade import SessionManagerFacade
        from agent_runtime.session_manager.orchestrator import SessionOrchestrator
        from agent_runtime.session_manager.sweeper import SessionSweeper

        self.redis = redis_client
        self.k8s = k8s
        self.db = db
        self.sm_state = SessionState(redis_client)
        self.rm_state = ResourceState(redis_client)

        self.sm_facade = SessionManagerFacade(self.sm_state)
        self.rm_orchestrator = ResourceOrchestrator(self.rm_state, k8s)
        self.rm_facade = ResourceManagerFacade(self.rm_orchestrator)
        # 池参数推送记录（断言 config_sync 是否推 RM 用）
        self.pool_pushes: list[tuple[str, dict, dict | None]] = []

        async def _push(scope_id, pool, pod_spec):
            self.pool_pushes.append((scope_id, pool, pod_spec))
            await self.rm_facade.update_pool_config(scope_id, pool, pod_spec)

        self.config_store = ConfigStore(
            db, self.sm_state, push_pool_config=_push,
            known_rm_scopes=self.rm_facade.known_scope_ids,
        )
        self.orchestrator = SessionOrchestrator(
            self.sm_state, self.config_store, self.rm_facade,
            scope_full_timeout=scope_full_timeout,
        )
        self.sm_sweeper = SessionSweeper(self.sm_state, self.rm_facade)
        self.rm_sweeper = ResourceSweeper(
            self.rm_state, k8s, self.sm_facade,
            orchestrator=self.rm_orchestrator,
        )

    async def seed_template(self, template_id="tpl-1", scope_id="scope-main",
                            **overrides) -> None:
        """全量下发一个 template + 一个通配兜底 scope（空 routing_rules 表达式）。

        模板用 legacy 内联拼写 + split_sync_payload 转三段式(wire 独占)。
        """
        template = {
            "agent_image": "agentserver:1.0",
            "namespace": "default",
            "scope_concurrency": 3,
            "pod_concurrency": 2,
            "session_ttl": 60,
            "pod_ttl": 300,
            "min_idle_pods": 0,
            **overrides,
        }
        await self.config_store.config_sync(split_sync_payload(
            [{"template_id": template_id, **template}],
            [{"scope_id": scope_id, "index": 0,
              "template_id": template_id, "routing_rules": ""}],
        ))

    async def route(self, session_id, group_id="grp", bot_id="bot",
                    user_id="user", request_id=None):
        return await self.orchestrator.route(
            request_id=request_id or f"req-{session_id}",
            session_id=session_id, group_id=group_id, bot_id=bot_id,
            user_id=user_id,
        )


@pytest.fixture
def k8s():
    from agent_runtime.resource_manager.k8s import FakeK8sPodClient

    return FakeK8sPodClient()


@pytest.fixture
def runtime(db_handler, redis_client, k8s):
    return Runtime(db_handler, redis_client, k8s)
