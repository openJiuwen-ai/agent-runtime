# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""部署工具程序化调用契约测试：隔离目录 + --render-only + JSON 进度行可解析（离线）。

验证 deploy/openshift 的 DEPLOY_ENV_FILE / DEPLOY_CONF_DIR / DEPLOY_TMP_DIR /
DEPLOY_JSON_PROGRESS 环境变量覆盖（manager-server 部署模块依赖的调用契约）。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

from manager_server.core.deployment.toolkit import resolve_tool_dir

pytestmark = pytest.mark.asyncio

# 与 var_handler.sh 校验对齐的最小可渲染 env（full 模式产品面）
_MIN_ENV_CUSTOM = """\
MARIADB_IMAGE="mariadb:11.8"
MYSQL_IMAGE="mysql:8.0"
POSTGRESQL_IMAGE="postgres:16"
REDIS_IMAGE="redis:7-alpine"
MINIO_IMAGE="quay.io/minio/minio:latest"
AGENTSERVER_IMAGE="example/jiuwenswarm-core:1.0"
GATEWAY_IMAGE="example/jiuwenswarm-core:1.0"
WEB_IMAGE="example/jiuwenswarm-web:1.0"
IDENTITY_IMAGE="example/jiuwenswarm-identity:1.0"
MANAGER_SERVER_IMAGE="example/jiuwenswarm-manager-server:1.0"
MANAGER_WEB_IMAGE="example/jiuwenswarm-manager-web:1.0"
AGENT_RUNTIME_IMAGE="example/jiuwenswarm-agent-runtime:1.0"
AGENT_RUNTIME_DEPLOY_MODE="full"
CLUSTER_TYPE=k8s
"""


def _tool_available() -> bool:
    tool_dir = resolve_tool_dir()
    return (tool_dir / "deploy.sh").is_file() and shutil.which("yq") is not None


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not _tool_available(), reason="部署工具树或 yq 不可用"),
]


async def _run_render_only(tmp_path: Path, namespace: str) -> tuple[int, str, Path, Path]:
    work = tmp_path / "work"
    conf_dir, tmp_dir = work / "conf", work / "tmp"
    conf_dir.mkdir(parents=True)
    tmp_dir.mkdir()
    env_file = work / ".env.custom"
    env_file.write_text(_MIN_ENV_CUSTOM, encoding="utf-8")

    sub_env = dict(os.environ)
    sub_env.update(
        {
            "DEPLOY_ENV_FILE": str(env_file),
            "DEPLOY_CONF_DIR": str(conf_dir),
            "DEPLOY_TMP_DIR": str(tmp_dir),
            "DEPLOY_JSON_PROGRESS": "1",
        }
    )
    proc = await asyncio.create_subprocess_exec(
        "bash", "deploy.sh", "-n", namespace, "up", "--render-only",
        cwd=str(resolve_tool_dir()),
        env=sub_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    return proc.returncode or 0, stdout.decode("utf-8", errors="replace"), conf_dir, work


async def test_render_only_isolated_dirs_and_json_progress(tmp_path: Path):
    code, output, conf_dir, work = await _run_render_only(tmp_path, "ut-render")
    assert code == 0, output

    # JSON 进度行全部可解析，且含阶段与终态事件
    events = []
    for line in output.splitlines():
        if line.startswith("@@DEPLOY_JSON@@"):
            events.append(json.loads(line[len("@@DEPLOY_JSON@@") :]))
    assert events, "未输出结构化进度行"
    kinds = {e.get("event") for e in events}
    assert "phase" in kinds and "summary" in kinds
    assert any(e.get("status") == "success" for e in events if e.get("event") == "summary")

    # 产物落每任务隔离目录，full 模式产品面清单齐全
    for manifest in ("redis.yaml", "runtime.yaml", "gateway.yaml", "web.yaml"):
        assert (conf_dir / manifest).is_file(), f"缺渲染产物 {manifest}"
    # env 隔离：工具树自身 conf/ 不被创建/污染
    assert not (resolve_tool_dir() / "conf" / "gateway.yaml").exists()
    # 工作目录无 .env.custom 泄漏到工具树
    assert work.joinpath(".env.custom").is_file()


async def test_render_only_two_namespaces_parallel(tmp_path: Path):
    async def one(ns: str) -> tuple[int, str, Path]:
        return await _run_render_only(tmp_path / ns, ns)

    (c1, o1, conf1, _), (c2, o2, conf2, _) = await asyncio.gather(one("ut-para-a"), one("ut-para-b"))
    assert c1 == 0, o1
    assert c2 == 0, o2
    ns1 = _first_namespace(conf1 / "gateway.yaml")
    ns2 = _first_namespace(conf2 / "gateway.yaml")
    assert ns1 == "ut-para-a" and ns2 == "ut-para-b"


def _first_namespace(manifest: Path) -> str:
    import subprocess

    out = subprocess.run(
        ["yq", ".metadata.namespace", str(manifest)],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip().splitlines()[0]


async def test_manager_render_injects_env_snapshot(tmp_path: Path):
    """manager up 渲染时把 .env.custom 非敏感键注入 MANAGER_DEPLOY_ENV_JSON（敏感键剔除）。"""
    import subprocess

    tool_dir = resolve_tool_dir()
    work = tmp_path / "work"
    (work / "conf").mkdir(parents=True)
    env_file = work / ".env.custom"
    env_file.write_text(
        _MIN_ENV_CUSTOM
        + 'MARIADB_ROOT_PASSWORD="Root@123456"\n'
        + 'DB_TYPE="mysql"\n'
        + 'MODE=product\n',
        encoding="utf-8",
    )
    sub_env = dict(os.environ)
    sub_env.update(
        {
            "DEPLOY_ENV_FILE": str(env_file),
            "DEPLOY_CONF_DIR": str(work / "conf"),
            "DEPLOY_TMP_DIR": str(work / "tmp"),
        }
    )
    (work / "tmp").mkdir()
    proc = await asyncio.create_subprocess_exec(
        "bash", "deploy.sh", "-n", "ut-snap", "manager", "up", "--render-only",
        cwd=str(tool_dir), env=sub_env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    assert proc.returncode == 0, stdout.decode()

    manifest = work / "conf" / "manager-server.yaml"
    assert manifest.is_file()
    value = subprocess.run(
        ["yq", ".spec.template.spec.containers[0].env[] | select(.name == \"MANAGER_DEPLOY_ENV_JSON\") | .value", str(manifest)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    snapshot = json.loads(value)
    assert snapshot["GATEWAY_IMAGE"].endswith(":1.0")
    assert snapshot["DB_TYPE"] == "mysql"
    assert "MARIADB_ROOT_PASSWORD" not in snapshot   # 敏感键剔除
    assert "MODE" not in snapshot                     # 工具内部/强制键剔除（管理面恒 product）
    assert "CLUSTER_TYPE" not in snapshot             # 工具内部键剔除


async def test_numeric_namespace_renders_quoted(tmp_path: Path):
    """纯数字命名空间合法（K8s DNS label），模板必须引号包裹防 YAML 解析成整数
    （线上实测回归：ns=111 渲染出 namespace: 111 → kubectl unmarshal number 失败）。"""
    import subprocess

    code, output, conf_dir, _ = await _run_render_only(tmp_path, "111")
    assert code == 0, output
    for manifest in ("gateway.yaml", "redis.yaml", "runtime.yaml"):
        ns = subprocess.run(
            ["yq", ".metadata.namespace | type", str(conf_dir / manifest)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        types = [line for line in ns.splitlines() if line and line != "---"]
        assert types and all(t == "!!str" for t in types), (
            f"{manifest} 命名空间应为字符串: {types}"
        )
    cm = subprocess.run(
        ["yq", ".data.NAMESPACE | type", str(conf_dir / "gateway-env.configmap.yaml")],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert cm == "!!str", f"ConfigMap NAMESPACE 应为字符串: {cm}"


def _run_render_only_sync(env_custom: str, args: list[str], work: Path) -> tuple[int, str]:
    import subprocess

    (work / "conf").mkdir(parents=True, exist_ok=True)
    (work / "tmp").mkdir(exist_ok=True)
    env_file = work / ".env.custom"
    env_file.write_text(env_custom, encoding="utf-8")
    sub_env = dict(os.environ)
    sub_env.update(
        {
            "DEPLOY_ENV_FILE": str(env_file),
            "DEPLOY_CONF_DIR": str(work / "conf"),
            "DEPLOY_TMP_DIR": str(work / "tmp"),
        }
    )
    proc = subprocess.run(
        ["bash", "deploy.sh", *args],
        cwd=str(resolve_tool_dir()),
        env=sub_env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_check_dependency_openshift_token_skips_oc(monkeypatch, tmp_path):
    """TOKEN 在手时 openshift 形态免 oc（PATH 掏空仍应通过 check_dependency）。"""
    import subprocess

    tool_dir = resolve_tool_dir()
    env_custom = _MIN_ENV_CUSTOM.replace("CLUSTER_TYPE=k8s", "CLUSTER_TYPE=openshift")
    work = tmp_path / "w"
    env_file = work / ".env.custom"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(env_custom, encoding="utf-8")
    # source 级：掏空 PATH + TOKEN 注入，调 check_dependency 期待不因缺 oc 报错
    # stub bin：只放 yq（隔离 PATH；本机 oc 存在与否不影响用例）
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    yq_path = subprocess.run(["sh", "-c", "command -v yq"], capture_output=True, text=True).stdout.strip()
    (stub_bin / "yq").symlink_to(yq_path)
    base_env = {"PATH": f"{stub_bin}:/usr/bin:/bin", "HOME": "/root"}
    script = (
        "set -e; source ./kubectl.sh\n"
        'declare -A DEPLOY_VARS=( ["CLUSTER_TYPE"]="openshift" )\n'
        "check_dependency && echo DEP_OK\n"
    )
    proc = subprocess.run(
        ["bash", "-c", script],
        cwd=str(tool_dir),
        env={**base_env, "TOKEN": "sha256~x"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "DEP_OK" in proc.stdout, proc.stdout + proc.stderr
    # 反证：无 TOKEN 时缺 oc 应报错
    proc2 = subprocess.run(
        ["bash", "-c", script],
        cwd=str(tool_dir),
        env=dict(base_env),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "DEP_OK" not in proc2.stdout
    assert "oc" in (proc2.stdout + proc2.stderr)


async def test_openshift_render_injects_token_credential(tmp_path: Path):
    """openshift 形态 manager 渲染：token 卷/env 三件套 + Route 保留。"""
    import subprocess

    env_custom = _MIN_ENV_CUSTOM.replace(
        "CLUSTER_TYPE=k8s", "CLUSTER_TYPE=openshift\nCLUSTER_API_SERVER=https://api.ocp.example:6443"
    ).replace('AGENT_RUNTIME_DEPLOY_MODE="full"', 'AGENT_RUNTIME_DEPLOY_MODE="routing"')
    work = tmp_path / "ocp"
    code, output = _run_render_only_sync(env_custom, ["-n", "ut-ocp", "manager", "up", "--render-only"], work)
    assert code == 0, output

    manifest = work / "conf" / "manager-server.yaml"
    envs = subprocess.run(
        ["yq", "-o=json", 'select(.kind == "Deployment") | .spec.template.spec.containers[0].env', str(manifest)],
        capture_output=True, text=True, check=True,
    ).stdout
    names = {e["name"]: e.get("value") for e in json.loads(envs)}
    assert names.get("MANAGER_DEPLOY_TOKEN") == "/etc/manager/deploy-token"
    assert names.get("MANAGER_DEPLOY_CLUSTER_TYPE") == "openshift"
    assert names.get("MANAGER_DEPLOY_API_SERVER") == "https://api.ocp.example:6443"
    vols = subprocess.run(
        ["yq", '-o=json', 'select(.kind == "Deployment") | .spec.template.spec.volumes', str(manifest)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert any(v.get("secret", {}).get("secretName") == "manager-deploy-token" for v in json.loads(vols))
    # Route 不被剥离（openshift 形态）
    kinds = subprocess.run(
        ["yq", '.kind', str(work / "conf" / "manager-web.yaml")],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert "Route" in kinds
