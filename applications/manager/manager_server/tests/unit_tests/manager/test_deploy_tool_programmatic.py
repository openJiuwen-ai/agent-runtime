# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""部署工具程序化调用契约测试：隔离目录 + --render-only + JSON 进度行可解析（离线）。

验证 deploy/jiuwenswarm-ee 的 DEPLOY_ENV_FILE / DEPLOY_CONF_DIR / DEPLOY_TMP_DIR /
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
JIUWENBOX_IMAGE="example/jiuwenswarm-sandbox:1.0"
WEB_IMAGE="example/jiuwenswarm-web:1.0"
WEB_SERVER_IMAGE="example/jiuwenswarm-web-server:1.0"
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

    # 产物落每任务隔离目录；up 只渲染产品面（用户面 web/web-server 随 manager 组渲染）
    for manifest in ("redis.yaml", "runtime.yaml", "gateway.yaml"):
        assert (conf_dir / manifest).is_file(), f"缺渲染产物 {manifest}"
    assert not (conf_dir / "web-server.yaml").exists()
    assert not (conf_dir / "web.yaml").exists()
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
        [
            "yq",
            ".spec.template.spec.containers[0].env[]"
            " | select(.name == \"MANAGER_DEPLOY_ENV_JSON\") | .value",
            str(manifest),
        ],
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
    bash = shutil.which("bash") or "bash"
    base_env = {"PATH": f"{stub_bin}:/usr/bin:/bin", "HOME": "/root"}
    script = (
        "set -e; source ./kubectl.sh\n"
        'declare -A DEPLOY_VARS=( ["CLUSTER_TYPE"]="openshift" )\n'
        "check_dependency && echo DEP_OK\n"
    )
    proc = subprocess.run(
        [bash, "-c", script],
        cwd=str(tool_dir),
        env={**base_env, "TOKEN": "sha256~x"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "DEP_OK" in proc.stdout, proc.stdout + proc.stderr
    # 反证：无 TOKEN 时缺 oc 应报错
    proc2 = subprocess.run(
        [bash, "-c", script],
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
    ).replace(
        'JIUWENBOX_IMAGE="example/jiuwenbox:1.0"\nAGENT_RUNTIME_DEPLOY_MODE="full"',
        'AGENT_RUNTIME_DEPLOY_MODE="routing"',
    )
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


def _write_kubectl_stub(stub_bin: Path, record: Path, create_rc: int, create_stderr: str) -> None:
    stub = stub_bin / "kubectl"
    stub.write_text(
        "#!/bin/bash\n"
        'case "$1" in\n'
        '  get) echo \'Error from server (NotFound): secrets "ut-secret" not found\' >&2; exit 1;;\n'
        # create -f <清单>：记录参数并把清单内容存档（源进程退出时 trap 会清掉临时目录）
        '  create) printf \'%s\\n\' "$@" >> "$KCTL_STUB_RECORD"; prev=""; for a in "$@"; do [ "$prev" = "-f" ] && cp "$a" "$KCTL_STUB_MANIFEST"; prev="$a"; done; echo "' + create_stderr + '" >&2; exit ' + str(create_rc) + ';;\n'
        "esac\n"
        "exit 1\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)


def _run_ensure_secret(tmp_path: Path, tool_dir: Path, stub_bin: Path, record: Path):
    import subprocess

    bash = shutil.which("bash") or "bash"
    script = (
        "set -e; source ./kubectl.sh\n"
        'declare -A DEPLOY_VARS=( ["CLUSTER_TYPE"]="k8s" )\n'
        'if _ensure_deploy_secret_via_kctl ut-ns ut-secret kubeconfig aGVsbG8=; then echo ENSURE_CREATED; else echo ENSURE_REUSED; fi\n'
    )
    return subprocess.run(
        [bash, "-c", script],
        cwd=str(tool_dir),
        env={
            "PATH": f"{stub_bin}:/usr/bin:/bin",
            "HOME": "/root",
            "DEPLOY_TMP_DIR": str(tmp_path),
            "KCTL_STUB_RECORD": str(record),
            "KCTL_STUB_MANIFEST": str(record.parent / "stub-manifest.yaml"),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


async def test_ensure_deploy_secret_k8s_uses_file_verb(tmp_path: Path):
    """k8s 形态凭证 Secret 自动创建：raw POST 不支持，须走清单文件 + kubectl create -f。"""
    import subprocess

    tool_dir = resolve_tool_dir()
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    record = tmp_path / "stub-record"
    yq_path = subprocess.run(["sh", "-c", "command -v yq"], capture_output=True, text=True).stdout.strip()
    (stub_bin / "yq").symlink_to(yq_path)

    # 创建成功路径：探测 404 → kubectl create -f <清单>，清单含 Secret data
    _write_kubectl_stub(stub_bin, record, create_rc=0, create_stderr="")
    proc = _run_ensure_secret(tmp_path, tool_dir, stub_bin, record)
    assert "ENSURE_CREATED" in proc.stdout, proc.stdout + proc.stderr
    args = record.read_text(encoding="utf-8").splitlines()
    assert args[:2] == ["create", "-f"], args
    manifest = record.parent / "stub-manifest.yaml"
    assert manifest.is_file()
    content = manifest.read_text(encoding="utf-8")
    assert "name: ut-secret" in content
    assert "namespace: ut-ns" in content
    assert "kubeconfig: aGVsbG8=" in content

    # 已存在（AlreadyExists → 409）路径：视为并发创建沿用，不报错
    record.unlink()
    _write_kubectl_stub(stub_bin, record, create_rc=1, create_stderr='Error from server (AlreadyExists): secrets "ut-secret" already exists')
    proc = _run_ensure_secret(tmp_path, tool_dir, stub_bin, record)
    assert "ENSURE_REUSED" in proc.stdout, proc.stdout + proc.stderr


async def test_render_manager_plane_urls_cross_namespace(tmp_path: Path):
    """MANAGER_PLANE_NS 注入时 web/web-server 的 IDP/manager 上游跨 ns 改写；
    未注入（CLI 单 ns 全量部署）维持同 ns 短名。"""
    work_cross = tmp_path / "cross"
    code, output = _run_render_only_sync(
        _MIN_ENV_CUSTOM + 'MANAGER_PLANE_NS="ut-mgr"\n',
        ["-n", "ut-prod", "up", "web-server", "web", "--render-only"], work_cross,
    )
    assert code == 0, output
    for manifest in ("web-server.yaml", "web.yaml"):
        content = (work_cross / "conf" / manifest).read_text(encoding="utf-8")
        assert "jiuwenswarm-identity.ut-mgr:8770" in content, manifest
        assert "jiuwenswarm-manager-server.ut-mgr:8765" in content, manifest
        assert "value: \"http://jiuwenswarm-identity:8770\"" not in content, manifest

    work_plain = tmp_path / "plain"
    code, output = _run_render_only_sync(
        _MIN_ENV_CUSTOM, ["-n", "ut-prod2", "up", "web-server", "web", "--render-only"], work_plain,
    )
    assert code == 0, output
    for manifest in ("web-server.yaml", "web.yaml"):
        content = (work_plain / "conf" / manifest).read_text(encoding="utf-8")
        assert "value: \"http://jiuwenswarm-identity:8770\"" in content, manifest
        assert "value: \"http://jiuwenswarm-manager-server:8765\"" in content, manifest


async def test_render_manager_plane_db_overrides_derived_names(tmp_path: Path):
    """管理面注入 DB 三元组时：web-server 连管理面真实库/只读账号，
    不再按产品面 ns 派生 manager_<ns>（该库无人建，ready 永不通过）。"""
    work = tmp_path / "db"
    code, output = _run_render_only_sync(
        _MIN_ENV_CUSTOM
        + 'MANAGER_PLANE_NS="ut-mgr"\n'
        + 'MANAGER_DB_NAME="manager_wmq"\n'
        + 'WEB_SERVER_DB_USER="user_backend_wmq"\n'
        + 'WEB_SERVER_DB_PASSWORD="Readonly@1"\n',
        ["-n", "ut-prod", "up", "web-server", "--render-only"], work,
    )
    assert code == 0, output
    content = (work / "conf" / "web-server.yaml").read_text(encoding="utf-8")
    assert "manager_wmq" in content
    assert "manager_ut-prod" not in content
    assert "user_backend_wmq" in content
    assert "web_server_ut_prod" not in content
    secret = (work / "conf" / "configmap-secret.yaml").read_text(encoding="utf-8")
    assert "Readonly@1" in secret


async def test_k8s_manager_render_default_kubeconfig_credential(tmp_path: Path):
    """k8s 形态部署凭证默认开（开箱即用）：未配置也注入默认名 Secret 卷/env；显式置空禁用。"""
    work = tmp_path / "k8s-default"
    code, output = _run_render_only_sync(
        _MIN_ENV_CUSTOM, ["-n", "ut-k8s", "manager", "up", "--render-only"], work,
    )
    assert code == 0, output
    content = (work / "conf" / "manager-server.yaml").read_text(encoding="utf-8")
    assert "manager-deploy-kubeconfig" in content
    assert "MANAGER_DEPLOY_KUBECONFIG" in content
    assert "MANAGER_DEPLOY_CLUSTER_TYPE" in content and "k8s" in content

    work_off = tmp_path / "k8s-off"
    code, output = _run_render_only_sync(
        _MIN_ENV_CUSTOM + 'MANAGER_DEPLOY_KUBECONFIG_SECRET=""\n',
        ["-n", "ut-k8s2", "manager", "up", "--render-only"], work_off,
    )
    assert code == 0, output
    content = (work_off / "conf" / "manager-server.yaml").read_text(encoding="utf-8")
    assert "MANAGER_DEPLOY_KUBECONFIG" not in content


async def test_render_cross_namespace_covers_gateway_upstream(tmp_path: Path):
    """跨 ns 定位覆盖全部产品面消费方：gateway 的 manager 上游同用 <<MANAGER_SVC_URL>>。"""
    work = tmp_path / "gw"
    code, output = _run_render_only_sync(
        _MIN_ENV_CUSTOM + 'MANAGER_PLANE_NS="ut-mgr"\n',
        ["-n", "ut-prod", "up", "--render-only"], work,
    )
    assert code == 0, output
    content = (work / "conf" / "gateway-env.configmap.yaml").read_text(encoding="utf-8")
    assert "GATEWAY_MANAGER_HTTP_URL: http://jiuwenswarm-manager-server.ut-mgr:8765" in content
    # 未注入时回落同 ns 短名
    work2 = tmp_path / "gw-plain"
    code, output = _run_render_only_sync(
        _MIN_ENV_CUSTOM, ["-n", "ut-prod2", "up", "--render-only"], work2,
    )
    assert code == 0, output
    assert "GATEWAY_MANAGER_HTTP_URL: http://jiuwenswarm-manager-server:8765" in (
        work2 / "conf" / "gateway-env.configmap.yaml"
    ).read_text(encoding="utf-8")


def test_missing_kubeconfig_disables_credential_injection(tmp_path: Path):
    """kubeconfig 缺失：置空 MANAGER_DEPLOY_KUBECONFIG_SECRET，apply 阶段不再注入卷
    （确保清单引用的 Secret 一定存在，manager-server 不因悬空引用卡 ContainerCreating）。"""
    import subprocess

    tool_dir = resolve_tool_dir()
    bash = shutil.which("bash") or "bash"
    empty_home = tmp_path / "home"
    empty_home.mkdir()
    script = (
        "set -e; source ./kubectl.sh\n"
        'DEPLOY_VARS["CLUSTER_TYPE"]="k8s"\n'
        'TARGETS=(manager-server)\n'
        'KCTL_NS="ut-ns"\n'
        'ensure_manager_deploy_credentials\n'
        'echo "SECRET_AFTER=${DEPLOY_VARS[MANAGER_DEPLOY_KUBECONFIG_SECRET]:-<empty>}"\n'
    )
    proc = subprocess.run(
        [bash, "-c", script],
        cwd=str(tool_dir),
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(empty_home),
            "KUBECONFIG": str(tmp_path / "nonexistent"),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    combined = proc.stdout + proc.stderr
    assert "SECRET_AFTER=<empty>" in combined, combined
    assert "跳过部署凭证注入" in combined
