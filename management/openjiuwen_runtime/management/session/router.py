# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Session 映射管理"""

import asyncio
from typing import Optional, Dict


class SessionRouter:
    """Session 映射管理，支持并发安全访问"""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._request_session: Dict[str, str] = {}

    async def acquire(self) -> None:
        """获取锁"""
        await self._lock.acquire()

    def release(self) -> None:
        """释放锁"""
        self._lock.release()

    async def get_request_session(self, request_id: str) -> Optional[str]:
        """获取 request_id 对应的 session_id"""
        async with self._lock:
            return self._request_session.get(request_id)

    async def set_request_session(self, request_id: str, session_id: str) -> None:
        """设置 request_id 到 session_id 的映射"""
        async with self._lock:
            self._request_session[request_id] = session_id

    async def delete_request_session(self, request_id: str) -> bool:
        """删除 request_id 的映射"""
        async with self._lock:
            if request_id in self._request_session:
                del self._request_session[request_id]
                return True
            return False

    async def clear(self) -> None:
        """清空 request → session 映射（服务缩容/销毁时调用）。"""
        async with self._lock:
            self._request_session.clear()

    async def get_request_session_size(self) -> int:
        """获取 request_session 映射大小"""
        async with self._lock:
            return len(self._request_session)


class ServiceRouter:
    """Service 映射管理，支持并发安全访问"""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._session_service: Dict[str, str] = {}

    async def acquire(self) -> None:
        """获取锁"""
        await self._lock.acquire()

    def release(self) -> None:
        """释放锁"""
        self._lock.release()

    async def get_session_service(self, session_id: str) -> Optional[str]:
        """获取 session_id 对应的 service_id"""
        async with self._lock:
            return self._session_service.get(session_id)

    async def set_session_service(self, session_id: str, service_id: str) -> None:
        """设置 session_id 到 service_id 的映射"""
        async with self._lock:
            self._session_service[session_id] = service_id

    async def delete_session_service(self, session_id: str) -> bool:
        """删除 session_id 的映射"""
        async with self._lock:
            if session_id in self._session_service:
                del self._session_service[session_id]
                return True
            return False

    async def get_session_service_size(self) -> int:
        """获取 session_service 映射大小"""
        async with self._lock:
            return len(self._session_service)
