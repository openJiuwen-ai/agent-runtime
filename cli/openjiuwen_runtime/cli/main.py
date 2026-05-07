# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Agent Runtime Manager CLI v2.0

使用WHL包部署Agent和Plugin，支持虚拟环境隔离。
"""

import asyncio
import json
from pathlib import Path

import click
from dotenv import load_dotenv

from openjiuwen_runtime.management import (
    DeployAgentParams,
    DeployPluginParams,
    DeploymentManager,
    ListDeploymentsParams,
)
from openjiuwen_runtime.management.models.enums import DeploymentType, DeploymentStatus
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler

# 加载.env配置文件（固定位置：manager根目录）
_manager_root = Path(__file__).parent.parent.parent
_env_file = _manager_root / ".env"
load_dotenv(_env_file)


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


@cli.resultcallback()
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
@click.argument("python_file_path", type=click.Path(exists=True))
@click.option("--name", required=True, help="部署名称（=包名，用于打包和运行）")
@click.option("--port", type=int, help="服务端口（自动分配可用端口）")
@click.pass_context
def deploy(ctx, python_file_path, name, port):
    """部署 Agent（使用 Python 文件）

    \b
    Manager SDK 内部自动将 Python 文件打包为 WHL 包。
    name 参数既是部署名称，也是打包的包名。

    示例:
        agent-runtime agent deploy ./my_agent.py --name my_agent
        agent-runtime agent deploy ./my_agent.py --name my_agent --port 8090
    """
    manager = ctx.obj["manager"]

    async def _deploy():
        result = await manager.deploy_agent(
            DeployAgentParams(
                name=name,
                version="1.0.0",
                extras={"python_file_path": python_file_path, "port": port},
            )
        )
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))

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
        click.echo(f"{'ID':<30} {'Name':<20} {'Status':<10} {'Port':<6} {'Package':<20} {'URL'}")
        click.echo("-" * 120)
        for dep in deployments:
            name = dep.get("name") or "-"
            dep_status = dep["status"].value if hasattr(dep["status"], "value") else str(dep["status"])
            package_name = dep.get("package_name") or "-"
            url = dep.get("url") or "-"
            row = (
                f"{dep['deployment_id']:<30} {name:<20} {dep_status:<10} "
                f"{dep['port']:<6} {package_name:<20} {url:<30}"
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
            click.echo(json.dumps(deployment, indent=2, ensure_ascii=False))
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
@click.argument("python_file_path", type=click.Path(exists=True))
@click.option("--name", required=True, help="部署名称（=包名，用于打包和运行）")
@click.option("--port", type=int, help="服务端口（自动分配可用端口）")
@click.pass_context
def deploy(ctx, python_file_path, name, port):
    """部署 Plugin（使用 Python 文件）

    \b
    Manager SDK 内部自动将 Python 文件打包为 WHL 包。
    name 参数既是部署名称，也是打包的包名。

    示例:
        agent-runtime plugin deploy ./my_plugin.py --name my_plugin
        agent-runtime plugin deploy ./my_plugin.py --name my_plugin --port 8091
    """
    manager = ctx.obj["manager"]

    async def _deploy():
        result = await manager.deploy_plugin(
            DeployPluginParams(
                name=name,
                version="1.0.0",
                extras={"python_file_path": python_file_path, "port": port},
            )
        )
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))

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

        # 显示简略信息
        click.echo(f"{'ID':<30} {'Name':<20} {'Status':<10} {'Port':<6} {'Package':<20} {'URL'}")
        click.echo("-" * 120)
        for dep in deployments:
            name = dep.get("name") or "-"
            dep_status = dep["status"].value if hasattr(dep["status"], "value") else str(dep["status"])
            package_name = dep.get("package_name") or "-"
            url = dep.get("url") or "-"
            row = (
                f"{dep['deployment_id']:<30} {name:<20} {dep_status:<10} "
                f"{dep['port']:<6} {package_name:<20} {url:<30}"
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
            click.echo(json.dumps(deployment, indent=2, ensure_ascii=False))
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


if __name__ == "__main__":
    cli(obj={})
