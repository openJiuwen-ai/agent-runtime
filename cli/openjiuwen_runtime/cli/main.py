# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Agent Runtime Manager CLI v2.0

使用WHL包部署Agent和Plugin，支持虚拟环境隔离。
"""

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path

import click
from dotenv import load_dotenv

from openjiuwen_runtime.cli.templates import get_pyproject, get_init, get_main, get_runner

# 加载全局配置（固定位置：~/.openjiuwen/.env）
_GLOBAL_ENV = Path.home() / ".openjiuwen" / ".env"
if _GLOBAL_ENV.exists():
    load_dotenv(_GLOBAL_ENV, override=False)

# 注入默认值，确保 Settings 校验通过（需在 import management 之前）
os.environ.setdefault("IP", "127.0.0.1")
os.environ.setdefault("LOWCODE_IMAGE", "")

from openjiuwen_runtime.management import (
    DeployAgentParams,
    DeployPluginParams,
    DeploymentManager,
    ListDeploymentsParams,
)
from openjiuwen_runtime.management.models.enums import DeploymentType, DeploymentStatus
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler


def _resolve_module_path(project_dir: Path) -> str:
    """从项目目录推导完整 Python 模块路径。

    扫描 openjiuwen_runtime/ 下的子目录，
    返回 'openjiuwen_runtime.xxx' 形式的模块路径。
    """
    pkg_root = project_dir / "openjiuwen_runtime"
    if not pkg_root.exists():
        raise click.ClickException(
            f"项目目录 {project_dir} 下未找到 openjiuwen_runtime/ 结构"
        )
    for child in sorted(pkg_root.iterdir()):
        if child.is_dir() and (child / "__main__.py").exists():
            return f"openjiuwen_runtime.{child.name}"
    raise click.ClickException(
        f"在 {pkg_root} 下未找到包含 __main__.py 的子目录"
    )


def _build_project(project_dir: Path) -> Path:
    """构建项目 WHL 包，返回 WHL 文件路径。"""
    pyproject = project_dir / "pyproject.toml"
    if not pyproject.exists():
        raise click.ClickException(f"项目目录 {project_dir} 下未找到 pyproject.toml")

    dist_dir = Path(os.getenv("DIST_DIR", "dist")).resolve()
    dist_dir.mkdir(parents=True, exist_ok=True)

    click.echo(f"Building project: {project_dir}")
    click.echo(f"Output dist: {dist_dir}")

    try:
        result = subprocess.run(
            ["uv", "build", str(project_dir), "--out-dir", str(dist_dir)],
            capture_output=True, text=True, timeout=300,
        )
    except FileNotFoundError:
        raise click.ClickException("uv not found, please install uv first")
    except subprocess.TimeoutExpired:
        raise click.ClickException("Build timed out (300s)")

    if result.returncode != 0:
        click.echo(result.stderr, err=True)
        raise click.ClickException("Build failed")

    # 找到刚构建的 WHL 文件（按修改时间取最新的）
    whl_files = sorted(dist_dir.glob("*.whl"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not whl_files:
        raise click.ClickException(f"Build succeeded but no WHL file found in {dist_dir}")

    whl_path = whl_files[0]
    click.echo(f"Build succeeded: {whl_path.name}")
    return whl_path


@click.group()
@click.pass_context
def cli(ctx):
    """Agent Runtime Manager CLI v2.0"""
    ctx.ensure_object(dict)

    # 从环境变量读取配置
    db_handler = SQLiteHandler('deployments.db')
    manager = DeploymentManager(db_handler)
    ctx.obj["manager"] = manager
    ctx.obj["db_handler"] = db_handler

    # 初始化 manager
    asyncio.run(manager.initialize())


@cli.result_callback()
@click.pass_context
def cleanup(ctx, result, **kwargs):
    """清理资源"""
    if "manager" in ctx.obj:
        asyncio.run(ctx.obj["manager"].shutdown())


# ==================== Agent 命令 ====================

@cli.group()
def agent():
    """Agent 管理"""
    pass


@agent.command()
@click.argument("project_dir", type=click.Path(exists=True))
@click.option("--port", type=int, help="服务端口（自动分配可用端口）")
@click.pass_context
def deploy(ctx, project_dir, port):
    """部署 Agent（使用项目目录）

    \b
    传入包含 pyproject.toml 的项目目录，CLI 会自动构建 WHL 包并部署。

    示例:
        agent-runtime agent deploy ./my_agent
        agent-runtime agent deploy ./my_agent --port 8090
    """
    manager = ctx.obj["manager"]
    project_path = Path(project_dir).resolve()
    module_path = _resolve_module_path(project_path)

    # 构建 WHL 包
    whl_path = _build_project(project_path)

    async def _deploy():
        result = await manager.deploy_agent(
            DeployAgentParams(
                name=module_path,
                version="1.0.0",
                extras={"port": port, "whl_path": str(whl_path)},
            )
        )
        data = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))

    asyncio.run(_deploy())


@agent.command(name="list")
@click.option("--status", type=click.Choice(["pending", "running", "stopped", "failed"]), help="过滤状态")
@click.pass_context
def list_deployments(ctx, status):
    """查询 Agent 列表"""
    manager = ctx.obj["manager"]

    async def _list():
        deployments = await manager.list_deployments(
            ListDeploymentsParams(
                deployment_type=DeploymentType.AGENT,
                deployment_status=DeploymentStatus(status) if status else None,
            )
        )

        if not deployments:
            click.echo("No deployments found")
            return

        # 显示简略信息
        click.echo(f"{'ID':<30} {'Name':<20} {'Status':<10} {'Port':<6} {'URL'}")
        click.echo("-" * 120)
        for dep in deployments:
            name = dep.name or "-"
            dep_status = dep.deployment_status.value
            url = dep.url or "-"
            data = dep.data or {}
            port = data.get("port", "-")
            row = (
                f"{dep.deployment_id:<30} {name:<20} {dep_status:<10} "
                f"{port:<6} {url}"
            )
            click.echo(row)

    asyncio.run(_list())


@agent.command()
@click.argument("deployment_id")
@click.pass_context
def get(ctx, deployment_id):
    """获取 Agent 详情"""
    manager = ctx.obj["manager"]

    async def _get():
        deployment = await manager.get_deployment(deployment_id)
        if deployment:
            click.echo(json.dumps(deployment.model_dump(mode="json"), indent=2, ensure_ascii=False))
        else:
            click.echo(f"Deployment {deployment_id} not found", err=True)
            raise click.Abort()

    asyncio.run(_get())


@agent.command()
@click.argument("deployment_id")
@click.pass_context
def delete(ctx, deployment_id):
    """删除 Agent（会自动清理虚拟环境）"""
    manager = ctx.obj["manager"]

    async def _delete():
        success = await manager.delete_deployment(deployment_id)
        if success:
            click.echo(f"Deployment {deployment_id} deleted")
        else:
            click.echo(f"Deployment {deployment_id} not found", err=True)
            raise click.Abort()

    asyncio.run(_delete())


# ==================== Plugin 命令 ====================

@cli.group()
def plugin():
    """Plugin 管理"""
    pass


@plugin.command()
@click.argument("project_dir", type=click.Path(exists=True))
@click.option("--port", type=int, help="服务端口（自动分配可用端口）")
@click.pass_context
def deploy(ctx, project_dir, port):
    """部署 Plugin（使用项目目录）

    \b
    传入包含 pyproject.toml 的项目目录，CLI 会自动构建 WHL 包并部署。

    示例:
        agent-runtime plugin deploy ./my_plugin
        agent-runtime plugin deploy ./my_plugin --port 8091
    """
    manager = ctx.obj["manager"]
    project_path = Path(project_dir).resolve()
    module_path = _resolve_module_path(project_path)

    whl_path = _build_project(project_path)

    async def _deploy():
        result = await manager.deploy_plugin(
            DeployPluginParams(
                name=module_path,
                version="1.0.0",
                extras={"port": port, "whl_path": str(whl_path)},
            )
        )
        data = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))

    asyncio.run(_deploy())


@plugin.command(name="list")
@click.option("--status", type=click.Choice(["pending", "running", "stopped", "failed"]), help="过滤状态")
@click.pass_context
def list_plugin_deployments(ctx, status):
    """查询 Plugin 列表"""
    manager = ctx.obj["manager"]

    async def _list():
        deployments = await manager.list_deployments(
            ListDeploymentsParams(
                deployment_type=DeploymentType.PLUGIN,
                deployment_status=DeploymentStatus(status) if status else None,
            )
        )

        if not deployments:
            click.echo("No deployments found")
            return

        click.echo(f"{'ID':<30} {'Name':<20} {'Status':<10} {'Port':<6} {'URL'}")
        click.echo("-" * 120)
        for dep in deployments:
            name = dep.name or "-"
            dep_status = dep.deployment_status.value
            url = dep.url or "-"
            data = dep.data or {}
            port = data.get("port", "-")
            row = (
                f"{dep.deployment_id:<30} {name:<20} {dep_status:<10} "
                f"{port:<6} {url}"
            )
            click.echo(row)

    asyncio.run(_list())


@plugin.command()
@click.argument("deployment_id")
@click.pass_context
def get(ctx, deployment_id):
    """获取 Plugin 详情"""
    manager = ctx.obj["manager"]

    async def _get():
        deployment = await manager.get_deployment(deployment_id)
        if deployment:
            click.echo(json.dumps(deployment.model_dump(mode="json"), indent=2, ensure_ascii=False))
        else:
            click.echo(f"Deployment {deployment_id} not found", err=True)
            raise click.Abort()

    asyncio.run(_get())


@plugin.command()
@click.argument("deployment_id")
@click.pass_context
def delete(ctx, deployment_id):
    """删除 Plugin（会自动清理虚拟环境）"""
    manager = ctx.obj["manager"]

    async def _delete():
        success = await manager.delete_deployment(deployment_id)
        if success:
            click.echo(f"Deployment {deployment_id} deleted")
        else:
            click.echo(f"Deployment {deployment_id} not found", err=True)
            raise click.Abort()

    asyncio.run(_delete())


# ==================== New 命令 ====================

@cli.group()
def new():
    """创建新工程"""
    pass


@new.command()
@click.argument("name")
@click.option("--template", "template_type",
              type=click.Choice(["empty", "react", "workflow"]),
              default="empty", help="模板类型 (默认: empty)")
@click.option("--output-dir", default=".", help="输出目录 (默认: 当前目录)")
def agent(name, template_type, output_dir):
    """创建 Agent 模板工程

    \b
    模板类型:
      empty   - 空白 Agent，回显消息，无 LLM
      react   - ReAct Agent，带 LLM 和示例工具
      workflow - Workflow Agent，带 LLM 和最简工作流

    \b
    示例:
        openjiuwen new agent my_agent
        openjiuwen new agent my_agent --template react
        openjiuwen new agent my_agent --template workflow --output-dir ./projects
    """
    # 校验工程名
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_-]*$', name):
        click.echo(f"错误：工程名 '{name}' 不合法，仅允许字母开头，包含字母、数字、下划线、连字符", err=True)
        raise click.Abort()

    # 转换为合法 Python 包名
    pkg_name = name.replace("-", "_")
    project_dir = Path(output_dir) / name
    pkg_dir = project_dir / "openjiuwen_runtime" / pkg_name

    # 检查目标目录是否已存在
    if project_dir.exists():
        click.echo(f"错误：目录 {project_dir} 已存在", err=True)
        raise click.Abort()

    # 创建目录结构
    pkg_dir.mkdir(parents=True, exist_ok=True)

    # 写入文件
    (project_dir / "pyproject.toml").write_text(
        get_pyproject(name, pkg_name, template_type), encoding="utf-8"
    )
    (pkg_dir / "__init__.py").write_text(get_init(), encoding="utf-8")
    (pkg_dir / "__main__.py").write_text(get_main(pkg_name), encoding="utf-8")
    (pkg_dir / f"{pkg_name}_runner.py").write_text(
        get_runner(pkg_name, template_type), encoding="utf-8"
    )

    # 输出结果
    click.echo(f"Agent template created: {project_dir}")
    click.echo(f"   模板类型: {template_type}")
    click.echo()
    click.echo("下一步:")
    click.echo(f"  1. cd {project_dir}")
    click.echo(f"  2. 编辑 {pkg_name}_runner.py 添加业务逻辑")
    click.echo(f"  3. 部署: openjiuwen agent deploy {project_dir}")


if __name__ == "__main__":
    cli(obj={})
