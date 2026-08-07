# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""REST/SSE multi-handler example with extensible handlers and simple OAuth2.

Run from the ``service`` directory::

    uv run python examples/multi_handler_app.py

Then open ``http://127.0.0.1:8090/docs`` and click ``Authorize``.  The OAuth2
authorization page supports the local credentials ``demo`` / ``demo`` and a
clearly labelled enterprise IdP simulation.  Federated shadow identities are
persisted in a local SQLite file; the example's business user store remains
process-local and should be replaced by ``ctx.db`` or ``ctx.kv`` in a service.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from pydantic import BaseModel, Field

from custom_handlers import custom_handlers
from federated_auth import (
    DatabaseFederatedIdentityStore,
    DemoEnterpriseIdentityProvider,
    DemoFederationProvider,
    ExampleOAuth2AuthorizationServer,
    FederatedAuthModule,
    FederationConnection,
)
from openjiuwen_runtime.service import (
    App,
    Envelope,
    HandlerSpec,
    MessageHandler,
    OAuth2AccessControl,
    StreamMessageHandler,
    SystemContext,
)


class CreateUserInput(BaseModel):
    name: str = Field(min_length=1, examples=["alice"])


class ByIdInput(BaseModel):
    id: int = Field(gt=0, examples=[1])


class ChatInput(BaseModel):
    text: str = Field(min_length=1, examples=["hello"])


class ErrorDemoInput(BaseModel):
    message: str = "example validation error"


class UserOutput(BaseModel):
    id: int
    name: str


class CreatedUserOutput(UserOutput):
    created_by: str


class UserListOutput(BaseModel):
    users: list[UserOutput]
    total: int


class RemoveUserOutput(BaseModel):
    removed: bool


class IdentityOutput(BaseModel):
    user_id: str
    organization_id: str
    display_name: str
    roles: list[str]
    auth_source: str


class DemoUserStore:
    """Small process-local repository used only by this runnable example."""

    def __init__(self) -> None:
        self._users: dict[int, dict] = {}
        self._next_id = 1
        self._lock = asyncio.Lock()

    async def create(self, name: str) -> dict:
        async with self._lock:
            user = {"id": self._next_id, "name": name}
            self._users[self._next_id] = user
            self._next_id += 1
            return dict(user)

    async def list(self) -> list[dict]:
        async with self._lock:
            return [dict(user) for user in self._users.values()]

    async def get(self, user_id: int) -> dict | None:
        async with self._lock:
            user = self._users.get(user_id)
            return dict(user) if user is not None else None

    async def remove(self, user_id: int) -> bool:
        async with self._lock:
            return self._users.pop(user_id, None) is not None


class CreateUserHandler(MessageHandler):
    spec = HandlerSpec(
        msg_type="users.create",
        request_model=CreateUserInput,
        response_model=CreatedUserOutput,
        summary="Create user",
        tags=("users",),
    )

    def __init__(self, store: DemoUserStore) -> None:
        self._store = store

    async def handle(self, ctx, env: Envelope):
        user = await self._store.create(env.rawdata.name)
        user["created_by"] = ctx.principal["username"]
        return user


class ListUsersHandler(MessageHandler):
    spec = HandlerSpec(
        msg_type="users.list",
        response_model=UserListOutput,
        summary="List users",
        tags=("users",),
    )

    def __init__(self, store: DemoUserStore) -> None:
        self._store = store

    async def handle(self, ctx, env: Envelope):
        users = await self._store.list()
        return {"users": users, "total": len(users)}


class GetUserHandler(MessageHandler):
    spec = HandlerSpec(
        msg_type="users.get",
        request_model=ByIdInput,
        response_model=UserOutput,
        summary="Get user",
        tags=("users",),
    )

    def __init__(self, store: DemoUserStore) -> None:
        self._store = store

    async def handle(self, ctx, env: Envelope):
        user = await self._store.get(env.rawdata.id)
        if user is None:
            from openjiuwen_runtime.service import NotFoundError

            raise NotFoundError(f"user {env.rawdata.id} not found")
        return user


class RemoveUserHandler(MessageHandler):
    spec = HandlerSpec(
        msg_type="users.remove",
        request_model=ByIdInput,
        response_model=RemoveUserOutput,
        summary="Remove user",
        tags=("users",),
    )

    def __init__(self, store: DemoUserStore) -> None:
        self._store = store

    async def handle(self, ctx, env: Envelope):
        return {"removed": await self._store.remove(env.rawdata.id)}


class ChatHandler(StreamMessageHandler):
    spec = HandlerSpec(
        msg_type="chat",
        request_model=ChatInput,
        summary="Stream chat characters",
        tags=("chat",),
    )

    async def handle_stream(self, ctx, env: Envelope):
        for character in env.rawdata.text:
            yield {"chunk": character, "user": ctx.principal["username"]}


enterprise_connection = FederationConnection(
    connection_id="enterprise-demo",
    issuer="https://idp.enterprise-demo.example",
    organization_id="virtual-org-enterprise-demo",
    organization_name="Enterprise Demo SSO",
)
federation_connections = {
    enterprise_connection.connection_id: enterprise_connection,
}

default_identity_database = (
    Path(__file__).resolve().parent / "federated_auth" / ".data" / "federated_auth.db"
)
identity_store = DatabaseFederatedIdentityStore(
    Path(os.getenv("FEDERATED_AUTH_DATABASE_PATH", default_identity_database))
)
oauth2_server = ExampleOAuth2AuthorizationServer()


oauth2 = OAuth2AccessControl(
    token_url="/oauth/token",
    authorization_url="/oauth/authorize",
    token_validator=oauth2_server.validate_access_token,
    scheme_name="OAuth2AuthorizationCode",
)

app = App(
    lambda: SystemContext(),
    title="OpenJiuwen Multi Handler Example",
    enable_ws=False,
    oauth2=oauth2,
)
app.asgi.swagger_ui_init_oauth = {
    "clientId": oauth2_server.client_id,
    "usePkceWithAuthorizationCodeGrant": True,
}

oauth2_server.mount(app.asgi, federation_connections.values())
FederatedAuthModule(
    provider=DemoFederationProvider(),
    identity_store=identity_store,
    oauth2_server=oauth2_server,
    connections=federation_connections,
).mount(app.asgi)
DemoEnterpriseIdentityProvider().mount(app.asgi)

demo_user_store = DemoUserStore()

# Register one object, then a batch of object-oriented handlers.
app.register(CreateUserHandler(demo_user_store))
app.register_all(
    [
        ListUsersHandler(demo_user_store),
        GetUserHandler(demo_user_store),
        RemoveUserHandler(demo_user_store),
        ChatHandler(),
    ]
)

# Include a separately maintained handler module without modifying the host app.
app.include(custom_handlers)


@app.handle(
    "ping",
    summary="Ping",
    description="Decorator registration remains the shortest option.",
    tags=["system"],
)
async def ping(ctx, env: Envelope):
    return {
        "pong": True,
        "request_id": ctx.request_id,
        "authenticated_user": ctx.principal["username"],
    }


@app.handle(
    "identity.me",
    response_model=IdentityOutput,
    summary="Current local identity",
    description="Shows the local principal created by local or federated login.",
    tags=["identity"],
)
async def identity_me(ctx, env: Envelope):
    return {
        "user_id": ctx.principal["user_id"],
        "organization_id": ctx.principal["organization_id"],
        "display_name": ctx.principal["username"],
        "roles": ctx.principal["roles"],
        "auth_source": ctx.principal["auth_source"],
    }


@app.handle(
    "demo.error",
    request_model=ErrorDemoInput,
    summary="Return a validation error envelope",
    tags=["errors"],
)
async def demo_error(ctx, env: Envelope):
    from openjiuwen_runtime.service import ValidationError

    raise ValidationError(env.rawdata.message)


@app.asgi.get("/health", tags=["system"])
async def health():
    return {"status": "healthy", "app": "OpenJiuwen Multi Handler Example"}


if __name__ == "__main__":
    app.run()
