# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Service context capability example backed by MySQL, Redis, and etcd."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    TableDefinition,
)
from openjiuwen_runtime.service import (
    App,
    Envelope,
    ErrorCode,
    FrameworkError,
    LockBackendUnavailable,
    LockLost,
    NotFoundError,
    ServiceConfig,
    SystemContext,
    TypedAppContext,
)


USER_TABLE_NAME = "context_capability_users"
USER_CACHE_TTL_SECONDS = 60.0
USER_LOCK_TTL_SECONDS = 30.0
USER_LOCK_WAIT_SECONDS = 5.0

USER_TABLE = TableDefinition(
    table_name=USER_TABLE_NAME,
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            nullable=False,
            autoincrement=True,
        ),
        ColumnDefinition("email", "string", nullable=False, unique=True, length=320),
        ColumnDefinition("name", "string", nullable=False, length=128),
        ColumnDefinition("fence_token", "integer", nullable=False, default=0),
        ColumnDefinition("created_at", "datetime", nullable=False),
    ],
)


class CreateUserInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    name: str = Field(min_length=1, max_length=128)


class UserIdInput(BaseModel):
    id: int = Field(gt=0)


class UpdateUserInput(UserIdInput):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)


class ManualLockInput(BaseModel):
    key: str = Field(min_length=1, max_length=256)
    ttl: float = Field(default=30.0, gt=0, le=300)
    wait_timeout: float = Field(default=5.0, ge=0, le=60)


class ChatInput(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    delay_seconds: float = Field(default=0.05, ge=0, le=1)


class UserView(BaseModel):
    id: int
    email: EmailStr
    name: str
    fence_token: int
    created_at: datetime


def _cache_key(user_id: int) -> str:
    return f"user:{user_id}"


def _record_values(record: Any) -> Mapping[str, Any]:
    if isinstance(record, Mapping):
        return record
    mapping = getattr(record, "_mapping", None)
    if mapping is not None:
        return mapping
    to_dict = getattr(record, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return {
        name: getattr(record, name)
        for name in ("id", "email", "name", "fence_token", "created_at")
    }


def _user_view(record: Any) -> UserView:
    return UserView.model_validate(_record_values(record))


def _credential_view(credential: Any) -> dict[str, Any]:
    return {
        "key": credential.key,
        "backend": credential.backend,
        "lease_id": credential.lease_id,
        "fencing_token": credential.fencing_token,
        "acquired_at": credential.acquired_at,
        "expires_at": credential.expires_at,
        "remaining_seconds": max(0.0, credential.expires_at - time.monotonic()),
    }


def create_system_context() -> SystemContext:
    """Build process resources from ``OPENJIUWEN_SERVICE_*`` variables."""
    settings = ServiceConfig.from_env()
    return SystemContext.from_settings(
        settings=settings,
        table_definitions=(USER_TABLE,),
    )


app = App(
    create_system_context,
    title="Service Context Capabilities",
)
asgi = app.asgi


@app.handle("users/create", request_model=CreateUserInput)
async def create_user(
    ctx: TypedAppContext[CreateUserInput],
    env: Envelope[CreateUserInput],
) -> dict[str, Any]:
    request = ctx.request
    try:
        record = await ctx.db_create(
            USER_TABLE_NAME,
            {
                "email": str(request.email),
                "name": request.name,
                "fence_token": 0,
                "created_at": datetime.now(timezone.utc),
            },
        )
    except IntegrityError as exc:
        raise FrameworkError(
            f"user email {request.email!s} already exists",
            code=ErrorCode.CONFLICT,
        ) from exc

    user = _user_view(record)
    await ctx.cache.set_json(_cache_key(user.id), user, ttl=USER_CACHE_TTL_SECONDS)
    await ctx.audit(
        "users.create",
        resource=f"user:{user.id}",
        details={"email": str(user.email)},
    )
    return user.model_dump(mode="json")


@app.handle("users/get", request_model=UserIdInput)
async def get_user(
    ctx: TypedAppContext[UserIdInput],
    env: Envelope[UserIdInput],
) -> dict[str, Any]:
    key = _cache_key(ctx.request.id)
    cached = await ctx.cache.get_model(key, UserView)
    if cached is not None:
        return {"user": cached.model_dump(mode="json"), "cache_hit": True}

    record = await ctx.db_get(USER_TABLE_NAME, {"id": ctx.request.id})
    if record is None:
        raise NotFoundError(f"user {ctx.request.id} not found")
    user = _user_view(record)
    await ctx.cache.set_json(key, user, ttl=USER_CACHE_TTL_SECONDS)
    return {"user": user.model_dump(mode="json"), "cache_hit": False}


@app.handle("users/update", request_model=UpdateUserInput)
async def update_user(
    ctx: TypedAppContext[UpdateUserInput],
    env: Envelope[UpdateUserInput],
) -> dict[str, Any]:
    request = ctx.request
    locks = ctx.require_locks(distributed=True, fencing=True)
    lock_key = f"user:{request.id}"

    async with locks.hold(
        lock_key,
        ttl=USER_LOCK_TTL_SECONDS,
        wait_timeout=USER_LOCK_WAIT_SECONDS,
        auto_renew=True,
    ) as lease:
        fence_token = lease.credential.fencing_token
        if fence_token is None:
            raise LockBackendUnavailable(
                "the selected lock backend did not issue a fencing token"
            )

        async with ctx.transaction() as session:
            claim = await session.execute(
                text(
                    f"UPDATE {USER_TABLE_NAME} "
                    "SET fence_token = :fence_token "
                    "WHERE id = :user_id AND fence_token < :fence_token"
                ),
                {"user_id": request.id, "fence_token": fence_token},
            )
            if claim.rowcount != 1:
                existing = await session.execute(
                    text(f"SELECT id FROM {USER_TABLE_NAME} WHERE id = :user_id"),
                    {"user_id": request.id},
                )
                if existing.scalar_one_or_none() is None:
                    raise NotFoundError(f"user {request.id} not found")
                raise LockLost(f"stale lock credential for user:{request.id}")

            updated = await session.execute(
                text(
                    f"UPDATE {USER_TABLE_NAME} "
                    "SET name = :name "
                    "WHERE id = :user_id AND fence_token = :fence_token"
                ),
                {
                    "user_id": request.id,
                    "name": request.name,
                    "fence_token": fence_token,
                },
            )
            if updated.rowcount != 1:
                raise LockLost(f"lock credential superseded for user:{request.id}")
            result = await session.execute(
                text(
                    f"SELECT id, email, name, fence_token, created_at "
                    f"FROM {USER_TABLE_NAME} WHERE id = :user_id"
                ),
                {"user_id": request.id},
            )
            user = _user_view(result.mappings().one())
            lease.ensure_valid()

        await ctx.cache.delete(_cache_key(request.id))
        await ctx.audit(
            "users.update",
            resource=f"user:{request.id}",
            details={"fencing_token": fence_token},
        )
        return user.model_dump(mode="json")


@app.handle("users/remove", request_model=UserIdInput)
async def remove_user(
    ctx: TypedAppContext[UserIdInput],
    env: Envelope[UserIdInput],
) -> dict[str, Any]:
    user_id = ctx.request.id
    removed = await ctx.db_delete(USER_TABLE_NAME, {"id": user_id})
    await ctx.cache.delete(_cache_key(user_id))
    await ctx.audit(
        "users.remove",
        outcome="success" if removed else "not_found",
        resource=f"user:{user_id}",
        details={"removed": removed},
    )
    return {"id": user_id, "removed": removed}


@app.handle("locks/manual", request_model=ManualLockInput)
async def manual_lock(
    ctx: TypedAppContext[ManualLockInput],
    env: Envelope[ManualLockInput],
) -> dict[str, Any]:
    request = ctx.request
    lease = await ctx.locks.acquire(
        request.key,
        ttl=request.ttl,
        wait_timeout=request.wait_timeout,
        auto_renew=False,
    )
    acquired = _credential_view(lease.credential)
    released = False
    try:
        renewed = _credential_view(await lease.renew())
    finally:
        released = await lease.release()
    return {
        "acquired": acquired,
        "renewed": renewed,
        "released": released,
    }


@app.stream("chat", request_model=ChatInput)
async def chat(
    ctx: TypedAppContext[ChatInput],
    env: Envelope[ChatInput],
) -> AsyncIterator[dict[str, Any]]:
    request = ctx.request

    async def log_cleanup() -> None:
        ctx.logger.info("chat request resources released")

    ctx.add_cleanup(log_cleanup)
    for sequence, chunk in enumerate(request.text.split(), start=1):
        ctx.check_interrupted()
        if request.delay_seconds:
            await asyncio.sleep(request.delay_seconds)
        ctx.check_interrupted()
        yield {"sequence": sequence, "text": chunk}


if __name__ == "__main__":
    app.run()
