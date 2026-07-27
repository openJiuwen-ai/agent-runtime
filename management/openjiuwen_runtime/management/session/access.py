# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Access：策略生成 session_id / 并发度 / TTL，交给 ServiceManager（双队列）。"""

import asyncio
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable, List, Optional

from openjiuwen_runtime.foundation.log import get_logger

from .interfaces import (
    IAccess,
    IRequest,
    IResponseParser,
    ISessionStrategy,
    IServiceManager,
    SessionRequestWrapper, ISessionRequest,
)
from .models import AccessConfig, SessionConfig

logger = get_logger(__name__)


# 用于创建 ServiceManager 的工厂函数类型（异步）
ServiceManagerFactory = Callable[[], Awaitable[IServiceManager]]


class _AutoIdRequest(IRequest):
    """覆盖 ``request_id`` 为 Access 生成的 UUID，其余字段透传给原始 ``IRequest``。

    用途：当用户 ``IRequest.request_id`` 为空时，Access 自动生成；下游
    ``WSServiceMessageChannel`` 依靠 ``request_id`` 做多路复用，否则会拒收。
    """

    def __init__(self, base: IRequest, rid: str) -> None:
        self._base = base
        self._rid = rid

    @property
    def request_id(self) -> Optional[str]:
        return self._rid

    @property
    def chat_id(self) -> Optional[str]:
        return self._base.chat_id

    @property
    def bot_id(self) -> Optional[str]:
        return self._base.bot_id

    @property
    def user_id(self) -> Optional[str]:
        return self._base.user_id

    @property
    def session_id(self) -> Optional[str]:
        return self._base.session_id

    @property
    def channel_id(self) -> str:
        # IRequest 契约不强制 channel_id；_base 可能有（如 AgentRequest）也可能没有。
        # 用 getattr 兜底，与 SessionRequest.channel_id 的反射风格一致，避免 AttributeError。
        return str(getattr(self._base, "channel_id", "") or "")

    @property
    def wire_dict(self) -> Any:
        # 让 ws_client_channel._to_jsonable 能拿到含 request_id 的上行字典；
        # 若原对象没有 wire_dict，返回 None 走默认序列化分支。
        wd = getattr(self._base, "wire_dict", None)
        if isinstance(wd, dict):
            return wd
        return None


class Access(IAccess):
    def __init__(
        self,
        service_manager_factory: ServiceManagerFactory,
    ) -> None:
        self._service_manager_factory = service_manager_factory
        self._service_manager: Optional[IServiceManager] = None
        self._strategy: Optional[ISessionStrategy] = None
        self._response_parser: Optional[IResponseParser] = None
        self._config: Optional[AccessConfig] = None
        self._service_configs: Optional[list[dict[str, Any]]] = None
        self._shutdown_done: bool = False
        # 保护 update_config 的切换阶段，确保原子性
        self._update_lock = asyncio.Lock()

    async def init(
            self,
            response_parser: IResponseParser,
            config: AccessConfig,
            session_config: SessionConfig,
            strategy: ISessionStrategy = None,
            service_configs: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        # 与入口共享的会话维度：并发与 TTL 写入策略，供 handle_session 填充 ISessionRequest
        self._response_parser = response_parser
        self._config = config
        self._service_configs = service_configs
        if strategy:
            self._strategy = strategy
            self._strategy.configure(concurrency=session_config.concurrency, ttl=session_config.ttl)
        # 通过工厂函数创建 ServiceManager（异步）
        self._service_manager = await self._service_manager_factory()
        await self._service_manager.init(response_parser)
        await self._service_manager.start()
        logger.info(
            "Access 已初始化: user_q=%s sys_q=%s image=%s session_max=%s session_ttl=%s "
            "service_concurrency=%s min_idle=%s max=%s port=%s path=%s ws_tls=%s service_ttl=%s",
            config.user_queue_size,
            config.system_queue_size,
            config.image,
            session_config.concurrency,
            session_config.ttl,
            config.service_concurrency,
            config.min_idle_services,
            config.max_services,
            config.target_port,
            config.invoke_path,
            config.ws_use_tls,
            config.service_ttl,
        )
        logger.debug(
            "Access init 完成: message_timeout=%s", getattr(config, "message_timeout", None)
        )

    def get_service_manager_stats(self) -> dict | None:
        """返回当前 ServiceManager 的业务量统计，未初始化时返回 None。"""
        if self._service_manager is None or not hasattr(self._service_manager, "get_stats"):
            return None
        try:
            return self._service_manager.get_stats()
        except Exception as exc:
            logger.warning("Access get_service_manager_stats failed: %s", exc)
            return None

    async def shutdown(self) -> None:
        """优雅退出：停 ServiceManager 内全部 asyncio 任务与双队列、取消定时器、delete 已拉起的服务。"""
        if self._shutdown_done:
            logger.debug("Access shutdown 被忽略(幂等): 已关闭")
            return
        self._shutdown_done = True
        # 停止当前 ServiceManager
        if self._service_manager:
            await self._service_manager.stop()
        logger.info("Access 已 shutdown")

    async def cleanup_all_pods(self, namespace: str, *, kubeconfig: Optional[str] = None,
                               label_selector: str = "") -> None:
        """通过 K8s label selector 清理所有 AgentServer Pod（主备切换时调用）。"""
        if self._shutdown_done:
            logger.warning("Access cleanup_all_pods 被忽略: 已 shutdown")
            return
        try:
            from .k8s_service_handler import K8sServiceHandler
            deleted = await K8sServiceHandler.cleanup_all_agentserver_pods(
                namespace=namespace,
                kubeconfig=kubeconfig,
                label_selector=label_selector,
            )
            logger.info("Access cleanup_all_pods 完成: deleted=%s", deleted)
        except Exception as e:
            logger.error("Access cleanup_all_pods 失败: %s", e, exc_info=True)

    async def update_config(
            self, config: Optional[AccessConfig] = None, session_config: Optional[SessionConfig] = None,
            strategy: Optional[ISessionStrategy] = None,
    ) -> None:
        """运行时热更新配置。创建新的 ServiceManager，异步停止旧的 ServiceManager。
        
        使用优化的串行化策略：
        - 在锁外并行创建和启动新 SM（最大化并发性能）
        - 持锁时原子切换引用并清理旧 SM
        - 虽然串行执行切换，但创建阶段可并行，总体延迟更低
        
        注意：由于 ServiceManager 是有状态的（包含运行中的服务实例），
        必须保证切换的原子性，避免中间版本泄漏。
        """
        # 阶段1：在锁外创建并启动新 SM（可与其他 update_config 的创建阶段并发）
        logger.info("Access 开始热更新配置，正在创建新 ServiceManager...")
        new_sm = await self._service_manager_factory()
        await new_sm.init(self._response_parser)
        await new_sm.start()
        logger.info("新 ServiceManager 已启动，准备切换")
        
        # 阶段2：原子切换（持锁，但时间极短）
        async with self._update_lock:
            # 读取当前的 ServiceManager
            old_sm = self._service_manager
            
            # 切换到新的 ServiceManager
            self._service_manager = new_sm
            
            # 更新配置
            if config:
                self._config = config
            if strategy:
                self._strategy = strategy
            if session_config and self._strategy:
                self._strategy.configure(session_config.concurrency, session_config.ttl)
            
            logger.info("Access 配置已热更新, strategy=%s", type(self._strategy).__name__ if self._strategy else None)
        
        # 阶段3：异步清理旧 SM（不持锁，不阻塞后续请求）
        if old_sm:
            asyncio.create_task(self._graceful_stop_old_service_manager(old_sm))
        if old_sm:
            asyncio.create_task(self._graceful_stop_old_service_manager(old_sm))
    
    async def _graceful_stop_old_service_manager(self, old_sm: IServiceManager) -> None:
        """优雅停止旧的 ServiceManager：先标记为 deprecated，然后尝试清理。
        
        该方法在后台异步执行，不会阻塞新服务的运行。
        """
        try:
            # 标记为待老化状态
            old_sm.mark_deprecated()
            logger.info("旧 ServiceManager 已标记为待老化状态")
            
            # 尝试立即清理（如果没有活跃任务）
            cleaned = await old_sm.try_cleanup_if_idle()
            if cleaned:
                logger.info("旧 ServiceManager 已在后台异步清理完成")
                return
            
            # 如果无法立即清理，启动定期清理任务
            logger.info("旧 ServiceManager 仍有活跃任务，启动后台清理监控")
            await self._periodic_cleanup_deprecated_sm(old_sm)
            
        except Exception as e:
            logger.error("停止旧 ServiceManager 失败: %s", e, exc_info=True)
    
    async def _periodic_cleanup_deprecated_sm(self, old_sm: IServiceManager, max_retries: int = 20, interval: float = 30.0) -> None:
        """定期尝试清理已废弃的 ServiceManager。
        
        Args:
            old_sm: 待清理的旧 ServiceManager
            max_retries: 最大重试次数（默认 20 次，约 10 分钟）
            interval: 重试间隔秒数（默认 30 秒）
        """
        for attempt in range(max_retries):
            try:
                # 检查是否已经停止
                if not hasattr(old_sm, '_running') or not getattr(old_sm, '_running', True):
                    logger.info("旧 ServiceManager 已停止，退出清理监控")
                    return
                
                # 尝试清理
                cleaned = await old_sm.try_cleanup_if_idle()
                if cleaned:
                    logger.info("旧 ServiceManager 在第 %d 次重试后清理成功", attempt + 1)
                    return
                
                # 等待下次重试
                await asyncio.sleep(interval)
                
            except Exception as e:
                logger.warning("第 %d 次清理旧 ServiceManager 失败: %s", attempt + 1, e)
                await asyncio.sleep(interval)
        
        # 达到最大重试次数，强制停止
        logger.warning("旧 ServiceManager 在 %d 次重试后仍未清理", max_retries)

    async def send_message(self, msg: IRequest | ISessionRequest) -> AsyncIterator[Any]:
        # 1) 未 init 时直接失败并打 error
        if self._shutdown_done:
            logger.error("Access 已 shutdown，不再收消息")
            return
        if not self._response_parser:
            logger.error("ResponseParser 未设置")
            return
        if not self._service_manager:
            logger.error("ServiceManager 未初始化")
            return

        # 记录当前使用的 ServiceManager（可能在 update_config 后被标记为老化）
        current_sm = self._service_manager

        if isinstance(msg, ISessionRequest):
            session_request = msg
            rid = session_request.request_id
            logger.debug(
                "Access receive session: session_id=%s session_conc=%s session_ttl=%s request_id=%s",
                session_request.session_id,
                session_request.session_concurrency,
                session_request.session_ttl,
                rid,
            )
        else:
            rid = getattr(msg, "request_id", None)
            if not rid:
                rid = uuid.uuid4().hex
                logger.info(
                    "Access 自动生成 request_id=%s（请求未提供，多路复用必须非空）", rid
                )
                wd = getattr(msg, "wire_dict", None)
                if isinstance(wd, dict) and not wd.get("request_id"):
                    # 对端按 request_id 回包路由；inplace 注入到原 wire_dict 即可
                    wd["request_id"] = rid
                msg = _AutoIdRequest(msg, rid)
            logger.info("Access 收到请求: request_id=%s", rid)
            # 2) 策略层：从业务请求解析出 session_id、会话级并发、TTL
            if not self._strategy:
                logger.error("未设置session策略")
                return
            session_request = self._strategy.handle_session(msg)
            logger.debug(
                "Access 策略生成 session: session_id=%s session_conc=%s session_ttl=%s",
                session_request.session_id,
                session_request.session_concurrency,
                session_request.session_ttl,
            )

        # 3) 每个入口请求独占一条响应队列 + cancel，用于多路复用/取消
        response_queue: asyncio.Queue[Any] = asyncio.Queue()
        cancel: asyncio.Future = asyncio.get_running_loop().create_future()
        wrapper = SessionRequestWrapper(session_request, response_queue, cancel)

        # 4) 入用户队列，由 ServiceManager 异步消费并路由到具体服务实例
        await self._service_manager.handle_message(wrapper)
        logger.debug("Access 已将请求投递 ServiceManager, request_id=%s", rid)

        try:
            while True:
                try:
                    to = self._config.message_timeout if self._config else 600
                    data = await asyncio.wait_for(response_queue.get(), timeout=to)
                except asyncio.TimeoutError:
                    # 等响应超时: yield 一个 error chunk 给上游, 否则前端只能看到流静默结束。
                    # 注意: 不能只 put 到 response_queue 后 break —— 此时 wait_for 已抛 TimeoutError
                    # 退出, 没人再 get 这个 queue, chunk 会丢失。必须直接 yield。
                    logger.error(
                        "Access 等待下游响应超时: request_id=%s timeout=%s", rid, to
                    )
                    try:
                        chunk = self._build_timeout_error_chunk(session_request, rid, to)
                        yield self._response_parser.response(chunk)
                    except Exception as put_err:  # noqa: BLE001
                        logger.error(
                            "Access 超时 yield error chunk 失败: request_id=%s err=%s",
                            rid, put_err, exc_info=True,
                        )
                    break
                logger.debug("Access 收到流式分片, request_id=%s", rid)
                yield self._response_parser.response(data)
                if self._response_parser.is_completed(data):
                    logger.debug("Access 收到终态分片, request_id=%s", rid)
                    break
                if cancel.done():
                    logger.debug("Access 因 cancel 结束收包, request_id=%s", rid)
                    break
        finally:
            if not cancel.done():
                cancel.set_result(None)
            logger.debug("Access send_message 协程结束, request_id=%s", rid)

            # 请求处理完成后，检查当前 ServiceManager 是否被标记为老化且可以清理
            if current_sm.is_deprecated():
                cleaned = await current_sm.try_cleanup_if_idle()
                if cleaned:
                    logger.info("老化的 ServiceManager 已成功清理")

    async def _put_timeout_error_chunk(
            self,
            response_queue: asyncio.Queue[Any],
            session_request: ISessionRequest,
            rid: str,
            timeout: int,
    ) -> None:
        """写入一个超时 error chunk, 让前端立即拿到失败而非流静默结束。"""
        chunk = self._build_timeout_error_chunk(session_request, rid, timeout)
        await response_queue.put(chunk)

    @staticmethod
    def _build_timeout_error_chunk(
            session_request: ISessionRequest,
            rid: str,
            timeout: int,
    ) -> dict[str, Any]:
        """构造一个超时 error chunk。

        供 Access 等待下游响应超时时直接 ``yield`` 给上游, 避免前端只看到流静默结束。
        """
        channel_id = str(session_request.channel_id or "")
        return {
            "request_id": rid,
            "channel_id": channel_id,
            "is_complete": True,
            "payload": {
                "error": f"下游响应超时({timeout}s)",
                "message": f"下游响应超时({timeout}s)",
            },
        }
