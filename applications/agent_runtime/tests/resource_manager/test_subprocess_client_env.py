# coding: utf-8
"""SubprocessPodClient 模板 env 的 ${VAR} 引用展开（deploy_mode=subprocess）。

pod_spec 是管理面下发的静态快照；单机部署里随部署位置变化的值（如与 gateway
共享的 sqlite 路径）由 launcher 注入 runtime 进程 env，模板里写 ${VAR} 占位，
deploy 期在 build_env 展开，语义见 subprocess_client._expand_env_refs。
"""

import asyncio
import logging
import os
import sys
import textwrap

import pytest

from agent_runtime.resource_manager.subprocess_client import (
    SubprocessPodClient,
    _expand_env_refs,
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME_SUBPROCESS_DATA_ROOT", str(tmp_path))
    return SubprocessPodClient(ready_timeout=30.0)


# ------------------------------------------------------- ${VAR} 展开纯函数
def test_expand_resolves_from_runtime_process_env(monkeypatch):
    monkeypatch.setenv("LITE_DB_PATH", r"E:\somewhere\data\gateway.db")
    assert _expand_env_refs("${LITE_DB_PATH}") == r"E:\somewhere\data\gateway.db"


def test_expand_embedded_in_longer_value(monkeypatch):
    monkeypatch.setenv("SHARED_ROOT", "/srv/jiuwenswarm")
    assert (_expand_env_refs("${SHARED_ROOT}/data/gateway.db")
            == "/srv/jiuwenswarm/data/gateway.db")


def test_expand_unknown_var_kept_literal_with_warning(monkeypatch, caplog):
    monkeypatch.delenv("DEFINITELY_NOT_SET_VAR_XYZ", raising=False)
    with caplog.at_level(logging.WARNING,
                         logger="agent_runtime.resource_manager.subprocess"):
        assert _expand_env_refs("${DEFINITELY_NOT_SET_VAR_XYZ}/x.db") == \
            "${DEFINITELY_NOT_SET_VAR_XYZ}/x.db"
    assert "DEFINITELY_NOT_SET_VAR_XYZ" in caplog.text


def test_expand_no_refs_untouched():
    assert _expand_env_refs("plain-value") == "plain-value"
    # 与 shell 语法区分：只有 ${NAME} 形式展开，$NAME 不动
    assert _expand_env_refs("$HOME/data") == "$HOME/data"


# ------------------------------------------------------- build_env 合成层
def test_build_env_expands_template_env(client, monkeypatch, tmp_path):
    """模板 env ${VAR} → runtime 进程 env 展开为子进程 env 值。"""
    monkeypatch.setenv("GATEWAY_SQLITE_PATH", str(tmp_path / "gateway.db"))
    main = {
        "image": "local/agentserver:lite",
        "env": {
            "GATEWAY_SQLITE_PATH": "${GATEWAY_SQLITE_PATH}",
            "GATEWAY_DB_TYPE": "sqlite",   # 无引用值原样透传
        },
        "ports": [],
    }
    env, sse_port = client.build_env(main, workspace=tmp_path / "ws")
    assert env["GATEWAY_SQLITE_PATH"] == str(tmp_path / "gateway.db")
    assert env["GATEWAY_DB_TYPE"] == "sqlite"
    # 进程覆盖层仍压过模板 env（工作区隔离不受展开影响）
    assert env["JIUWENSWARM_DATA_DIR"] == str(tmp_path / "ws")
    assert sse_port > 0


def test_build_env_non_string_env_value_ignored(client, tmp_path):
    main = {"image": "x", "env": {"KEEP": 123, "STR": "v"}, "ports": []}
    env, _ = client.build_env(main, workspace=tmp_path / "ws")
    assert "KEEP" not in env
    assert env["STR"] == "v"


def test_build_env_cannot_capture_overrides(client, monkeypatch, tmp_path):
    """模板试图用 ${JIUWENSWARM_DATA_DIR} 抢工作区覆盖也抢不动：
    展开源是 runtime 进程 env（无此变量 → 保留字面量），且进程覆盖层最后落字。
    """
    monkeypatch.delenv("JIUWENSWARM_DATA_DIR", raising=False)
    main = {"image": "x",
            "env": {"JIUWENSWARM_DATA_DIR": "${JIUWENSWARM_DATA_DIR}"},
            "ports": []}
    env, _ = client.build_env(main, workspace=tmp_path / "ws")
    assert env["JIUWENSWARM_DATA_DIR"] == str(tmp_path / "ws")


# ------------------------------------------------------- deploy 全链冒烟
def test_deploy_end_to_end_expansion(tmp_path, monkeypatch):
    """真 Popen：模板 ${VAR} 出现在子进程真实环境里（探针落盘断言）。"""
    db_path = tmp_path / "gateway.db"
    probe_out = tmp_path / "probe.out"
    monkeypatch.setenv("GATEWAY_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("AGENT_RUNTIME_SUBPROCESS_DATA_ROOT", str(tmp_path / "pods"))
    probe = tmp_path / "probe_launcher.py"
    probe.write_text(textwrap.dedent(f"""
        import os, socket, time
        # 就绪判定探 SSE 口（AGENT_HTTP_PORT）；真实 agentserver 两个门都开，
        # 探针同样把 --port（AGENT_SERVER_PORT=ws 门）一起绑上。
        srvs = []
        for name in ("AGENT_HTTP_PORT", "AGENT_SERVER_PORT"):
            srv = socket.socket()
            srv.bind(("127.0.0.1", int(os.environ[name])))
            srv.listen(1)
            srvs.append(srv)
        with open(r"{probe_out}", "w", encoding="utf-8") as fh:
            fh.write(os.environ.get("GATEWAY_SQLITE_PATH", ""))
        time.sleep(30)
    """), encoding="utf-8")
    monkeypatch.setenv("AGENT_RUNTIME_SUBPROCESS_READY_TIMEOUT", "30")
    monkeypatch.setenv("AGENT_RUNTIME_SUBPROCESS_PYTHON", sys.executable)
    monkeypatch.setenv("AGENT_RUNTIME_SUBPROCESS_LAUNCHER_SCRIPT", str(probe))

    client = SubprocessPodClient(ready_timeout=30.0)
    pod_spec = {
        "namespace": "ns",
        "main_container": {
            "image": "local/agentserver:lite",
            "env": {"GATEWAY_SQLITE_PATH": "${GATEWAY_SQLITE_PATH}"},
            "ports": [{"name": "sse", "container_port": 18092}],
        },
    }
    info = asyncio.run(client.deploy(pod_spec))
    try:
        assert info.published_port > 0
        assert probe_out.read_text(encoding="utf-8") == str(db_path)
    finally:
        asyncio.run(client.delete(info.pod_id, "ns"))
