# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
VersatileAdapter 进程入口（a2a-sdk 1.0.0-alpha.1）。

启动方式：
  cd agent-runtime/applications/versatile_adapter
  python main.py

暴露端点（A2A SDK 标准）：
  GET  /.well-known/agent-card.json  — AgentCard
  POST /                             — A2A JSON-RPC（message/send、message/stream）

本服务仅供 a2a_service 内部调用，不直接面向用户。
"""
from __future__ import annotations

import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from fastapi import FastAPI
from loguru import logger
from starlette.applications import Starlette

from a2a.server.tasks import InMemoryTaskStore, TaskStore
from persistence.redis_client import RedisClient
from persistence.redis_task_store import RedisTaskStore
from config import get_settings
from a2a_facade.agent_card import VERSATILE_ADAPTER_CARD
from dispatcher.runner import VersatileAdapterRunner
from a2a_facade.executor import A2aVersatileExecutor


os.environ['NO_PROXY'] = 'localhost,127.0.0.1'


def dynamic_format(record) -> str:
    if len(record["extra"]) == 0:
        return "<green>{time:YYYY-MM-DD HH:mm:ss}</green> \x01 " \
                   "<level>{level: <8}</level> \x01 " \
                   "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> \x01 " \
                   "<level>{message}</level> \n"
    elif "conv_id" in record["extra"]:
        return "<green>{time:YYYY-MM-DD HH:mm:ss}</green> \x01 " \
                   "<level>{level: <8}</level> \x01 " \
                   "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> \x01 " \
                   "<cyan>{extra[trace_id]}</cyan> \x01 " \
                   "<cyan>{extra[agent_id]}</cyan> \x01 " \
                   "<cyan>{extra[conv_id]}</cyan> \x01 " \
                   "<level>{message}</level>\n"
    else:
        return "<green>{time:YYYY-MM-DD HH:mm:ss}</green> \x01 " \
                   "<level>{level: <8}</level> \x01 " \
                   "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> \x01 " \
                   "<cyan>{extra[trace_id]}</cyan> \x01 " \
                   "<level>{message}</level>\n"


def _va_cleanup_logs(files):
    """
    loguru retention 回调函数：按天数 + 按总空间清理 VA 日志。

    【函数身份】loguru 的 retention 回调，VA 每次日志轮转后由 loguru 自动调用
    【参数 files】loguru 传入的，当前 sink 匹配到的所有日志文件列表（Path 对象列表）
    【返回值】无返回值，通过副作用（删除文件）实现清理

    清理逻辑：
    1. 按保留天数清理：删除修改时间超过 retention_days 的 .gz 归档文件
    2. 按总空间清理：如果总空间超限，从最旧 .gz 归档开始逐一删除

    注意：活跃文件（versatile_adapter_{pid}.log，无 .gz 后缀）不参与删除，
          只删 .gz 归档文件。
    """
    # ━━━ 第1步：读取配置 ━━━
    from config import get_settings
    _settings = get_settings()
    retention_days = _settings.adapter_log_retention_days
    max_total_size = _settings.adapter_log_max_total_size

    # ━━━ 第2步：空列表保护 ━━━
    if not files:
        return
    # loguru 传入空列表时直接返回，防御性编程

    # 统一转为 Path 对象（loguru 可能传入字符串）
    files = [Path(f) for f in files]

    # ━━━ 第3步：区分活跃文件和归档文件 ━━━
    # 活跃文件：无 .gz 后缀的 .log 文件（如 versatile_adapter_12345.log，正在被 loguru 写入）
    # 归档文件：.gz 后缀的压缩文件（如 versatile_adapter_12345.log.2026-07-13_10-30-00.gz）
    # 用文件名后缀判断，不用 mtime 判断，避免误删刚压缩的归档（刚压缩的归档 mtime 可能比活跃文件还新）
    active_files = [f for f in files if f.exists() and not f.name.endswith(".gz")]
    archive_files = [f for f in files if f.exists() and f.name.endswith(".gz")]

    if not archive_files:
        return
    # 没有归档文件就没有可清理的东西，活跃文件永远不删，直接返回

    # ━━━ 第4步：按天数清理（只清理归档文件）━━━
    if retention_days > 0:
        # retention_days=0 表示不按天数清理，跳过此步骤
        cutoff_time = (datetime.now() - timedelta(days=retention_days)).timestamp()
        # 计算截止时间点：当前时间 - retention_days 天
        # 例如 retention_days=7，cutoff_time 就是7天前的 Unix 时间戳
        for f in archive_files:
            # 遍历所有归档文件
            try:
                if f.stat().st_mtime < cutoff_time:
                    # f.stat().st_mtime 是文件最后修改时间（Unix 时间戳）
                    # 如果文件的 mtime 早于截止时间（即文件超过 retention_days 天没修改过）
                    f.unlink()
                    # Path.unlink() 删除文件
            except OSError as e:
                # OSError 可能原因：文件被占用、权限不足、文件已被其他进程删除
                # 用 print 到 stderr，不用 loguru（避免在 retention 回调中递归调用 loguru）
                print(f"[va_log_cleanup] WARN 按天数清理删除失败: {f} -> {e}", file=sys.stderr)

    # ━━━ 第5步：按总空间清理（活跃文件 + 剩余归档文件的总和）━━━
    if max_total_size > 0:
        # max_total_size=0 表示不按空间清理，跳过此步骤

        remaining_archives = [(f.stat().st_mtime, f) for f in archive_files if f.exists()]
        # 重新获取未被第4步删掉的归档文件，返回 [(mtime, Path), ...] 列表
        # f.exists() 过滤掉已删除的文件

        active_size = sum(f.stat().st_size for f in active_files if f.exists())
        # 计算所有活跃文件的总大小（字节数）
        # 活跃文件计入总空间，但不删除

        archive_total = sum(f.stat().st_size for _, f in remaining_archives)
        # 计算所有剩余归档文件的总大小

        total_size = active_size + archive_total
        # 总空间 = 活跃文件大小 + 归档文件大小

        if total_size > max_total_size:
            # 如果总空间超过阈值，需要清理
            remaining_archives.sort(key=lambda x: x[0])
            # 按 mtime 排序（最旧的在前），优先删最旧的归档

            for _mtime, f in remaining_archives:
                # 从最旧开始遍历
                if total_size <= max_total_size:
                    break
                # 每删一个文件后检查，如果总空间已降到阈值以下，停止删除

                try:
                    file_size = f.stat().st_size
                    # 先获取文件大小（删除后就获取不到了）
                    f.unlink()
                    # 删除文件
                    total_size -= file_size
                    # 从总空间中减去已删除文件的大小
                except OSError as e:
                    print(f"[va_log_cleanup] WARN 按空间清理删除失败: {f} -> {e}", file=sys.stderr)


def _cleanup_stale_pid_logs(log_dir: str, current_pid: int, retention_days: int) -> None:
    """
    清理旧 PID 的僵尸日志文件。

    【背景】VA 日志文件名带 PID：versatile_adapter_{pid}.log
    每次 VA 重启都会用新的 PID，产生新的日志文件。旧 PID 的日志文件不会被 loguru
    的 retention 回调清理（因为 loguru 只管当前 sink 绑定的文件），会无限累积。

    【调用时机】VA 启动时 setup_logging() 中调用一次，在 logger.add 之前执行

    【清理规则】与需求文档一致，统一按 mtime 清理（不区分 .gz/.log）
    1. 扫描日志目录下所有匹配 versatile_adapter_*.log* 的文件
    2. 排除当前 PID 的文件（当前 PID 的文件正在使用，不能删）
    3. 统一按 mtime 检查：超过 retention_days 天没修改过则删除（.gz 和 .log 同标准）
    """
    if retention_days <= 0:
        return

    log_path = Path(log_dir)
    if not log_path.exists():
        return

    cutoff_time = (datetime.now() - timedelta(days=retention_days)).timestamp()

    for f in log_path.glob("versatile_adapter_*.log*"):
        if not f.is_file():
            continue
        # 不删除当前进程的日志文件
        if f"_{current_pid}." in f.name or f.name.endswith(f"_{current_pid}.log"):
            continue
        try:
            if f.stat().st_mtime < cutoff_time:
                f.unlink()
        except OSError as e:
            print(f"[va_log_cleanup] WARN 僵尸文件清理失败: {f} -> {e}", file=sys.stderr)


def setup_logging() -> None:
    """配置日志"""
    settings = get_settings()

    logger.remove()

    logger.add(
        sys.stderr,
        level=settings.adapter_log_level.upper() if settings.adapter_log_level else "INFO",
        format=dynamic_format,
        filter=lambda record: len(record["extra"]) == 0 or "trace_id" in record["extra"]
    )

    if settings.adapter_log_file:
        log_dir = os.path.dirname(settings.adapter_log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        # 启动时清理旧 pid 残留日志（在 logger.add 之前执行，与需求文档一致）
        _cleanup_stale_pid_logs(
            log_dir,
            os.getpid(),
            settings.adapter_log_retention_days
        )

        log_file_path = settings.adapter_log_file
        base, ext = os.path.splitext(log_file_path)
        log_file_with_pid = f"{base}_{os.getpid()}{ext}"
        logger.add(
            log_file_with_pid,
            level=settings.adapter_log_level.upper() if settings.adapter_log_level else "INFO",
            rotation=settings.adapter_log_rotation_size,
            retention=_va_cleanup_logs,
            compression="gz",
            format=dynamic_format,
            filter=lambda record: len(record["extra"]) == 0 or "trace_id" in record["extra"]
        )


    logger.info(
        f"[VersatileAdapter] 日志初始化完成 "
        f"level={settings.adapter_log_level or 'INFO'} "
        f"file={settings.adapter_log_file or '-'}"
    )


setup_logging()


_TTL = 1800


async def _create_task_store(settings) -> tuple[TaskStore, RedisClient | None]:
    """根据配置创建 TaskStore：Redis 有效时使用 RedisTaskStore，否则回退 InMemoryTaskStore。

    Returns:
        (task_store, redis_client) — redis_client 在 Redis 模式下非 None，调用方需在关闭时 disconnect。
    """
    if settings.redis_host:
        redis = RedisClient()
        await redis.connect(settings.redis_url)
        task_store = RedisTaskStore(redis, ttl=settings.redis_session_ttl or _TTL)
        logger.info(f"[VersatileAdapter] TaskStore=RedisTaskStore, host={settings.redis_host}")
        return task_store, redis
    logger.info("[VersatileAdapter] TaskStore=InMemoryTaskStore（未配置 Redis）")
    return InMemoryTaskStore(), None


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    settings = get_settings()

    # 1. 创建 TaskStore（按配置选择 Redis 或 InMemory）
    task_store, redis = await _create_task_store(settings)

    # 2. 从 YAML 配置创建 Runner（动态路由）
    runner = VersatileAdapterRunner()

    # 3. 创建 A2A 薄壳
    executor = A2aVersatileExecutor(runner=runner)

    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
        agent_card=VERSATILE_ADAPTER_CARD,
    )
    a2a_routes = (
        create_agent_card_routes(VERSATILE_ADAPTER_CARD)
        + create_jsonrpc_routes(request_handler, rpc_url="/")
    )
    fastapi_app.mount("/", Starlette(routes=a2a_routes))

    logger.info("[VersatileAdapter] 启动完成")

    try:
        yield
    finally:
        if redis:
            await redis.disconnect()
        logger.info("[VersatileAdapter] 关闭完成")


app = FastAPI(
    title="VersatileAdapter",
    description="Versatile 低代码平台 A2A 适配器",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def inject_trace_id(request, call_next):
    trace_id = request.headers.get("x-trace-id") or uuid.uuid4().hex
    logger.debug(f"接收到请求: {request.method} {request.url}，trace_id={trace_id}")
    with logger.contextualize(trace_id=trace_id):
        response = await call_next(request)
    response.headers["x-trace-id"] = trace_id
    return response


@app.get("/health", tags=["Health"])
async def health_check():
    """服务健康检查"""
    logger.debug("[VersatileAdapter] health check")
    return {
        "status": "healthy",
        "service": "VersatileAdapter",
    }
