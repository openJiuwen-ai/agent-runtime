"""Provision template credentials through Keycloak's Admin API."""

from __future__ import annotations

import logging
import ssl
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from manager_server.infrastructure.config import Settings

CLIENT_PREFIX = "jiuwen-agent-"
MANAGED_BY = "jiuwen-manager"
logger = logging.getLogger(__name__)


class RegistrationError(Exception):
    """An actionable registration error containing no upstream response body."""


class RegistrationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    admin_base_url: str
    token_url: str
    management_client_id: str = Field(min_length=1)
    management_client_secret: SecretStr
    agent_token_audience: str = Field(min_length=1)
    ca_bundle: str | None = None

    @field_validator("admin_base_url", "token_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parts = urlsplit(value)
        local_http = parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}
        if (parts.scheme != "https" and not local_http) or not parts.hostname:
            raise ValueError("Keycloak URLs require HTTPS, except on local loopback")
        if any((parts.username, parts.password, parts.query, parts.fragment)):
            raise ValueError("Keycloak URLs must not contain credentials, queries or fragments")
        return value

    @field_validator("management_client_secret")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("management client secret is required")
        return value


def load_registration_config(settings: Settings) -> RegistrationConfig:
    return load_registration_service(settings).config


def load_registration_service(settings: Settings) -> KeycloakAgentClients:
    """Load cached configuration and TLS material; callers offload file access."""
    if not settings.agent_client_registration_enabled:
        raise RegistrationError("Agent 客户端注册未启用")
    try:
        path = settings.agent_client_registration_config_file.strip()
        if not path:
            raise ValueError("missing configuration file")
        version = _file_version(path)
        config = _read_registration_config(path, version)
        ca_version = _file_version(config.ca_bundle) if config.ca_bundle else None
        return _cached_registration_service(path, version, ca_version)
    except (OSError, ValueError):
        raise RegistrationError("Agent 客户端注册配置不可用，请检查配置文件和 CA 文件") from None


class KeycloakAgentClients:
    def __init__(self, config: RegistrationConfig, *, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self.transport = transport
        self._ssl_context = ssl.create_default_context(cafile=config.ca_bundle)

    def _endpoint(self, *segments: str) -> httpx.URL:
        base = httpx.URL(f"{self.config.admin_base_url}/")
        return base.join("/".join(quote(segment, safe="") for segment in segments))

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            verify=self._ssl_context,
            timeout=10.0, follow_redirects=False, trust_env=False, transport=self.transport,
        )

    async def _request(self, http: httpx.AsyncClient, method: str, url: str | httpx.URL,
                       stage: str, *, allowed: tuple[int, ...] = (), **kwargs) -> httpx.Response:
        try:
            response = await http.request(method, url, **kwargs)
        except httpx.HTTPError:
            raise RegistrationError(f"{stage}失败：无法连接 Keycloak，请检查网络和 CA 配置") from None
        if not response.is_success and response.status_code not in allowed:
            raise RegistrationError(f"{stage}失败：Keycloak 返回 HTTP {response.status_code}")
        return response

    @staticmethod
    def _json(response: httpx.Response, expected: type, stage: str):
        try:
            value = response.json()
            if not isinstance(value, expected):
                raise ValueError()
            return value
        except (ValueError, TypeError):
            raise RegistrationError(f"{stage}失败：Keycloak 返回格式不正确") from None

    async def _authorize(self, http: httpx.AsyncClient) -> None:
        response = await self._request(http, "POST", self.config.token_url, "获取管理 Token", data={
            "grant_type": "client_credentials",
            "client_id": self.config.management_client_id,
            "client_secret": self.config.management_client_secret.get_secret_value(),
        })
        token = self._json(response, dict, "获取管理 Token").get("access_token")
        if not isinstance(token, str) or not token:
            raise RegistrationError("获取管理 Token 失败：响应缺少 access_token")
        http.headers["Authorization"] = f"Bearer {token}"

    async def _find(self, http: httpx.AsyncClient, registration_id: UUID, owner: str) -> dict | None:
        client_id = CLIENT_PREFIX + registration_id.hex
        response = await self._request(http, "GET", self._endpoint("clients"),
                                       "查询 Agent 客户端", params={"clientId": client_id, "search": "false"})
        matches = [row for row in self._json(response, list, "查询 Agent 客户端")
                   if isinstance(row, dict) and row.get("clientId") == client_id]
        if not matches:
            return None
        if len(matches) != 1:
            raise RegistrationError("同名 Client 已存在，且不属于本次注册")
        row = matches[0]
        if not isinstance(row.get("id"), str) or not row["id"]:
            raise RegistrationError("同名 Client 已存在，且不属于本次注册")
        attributes = row.get("attributes") or {}
        if not isinstance(attributes, dict):
            raise RegistrationError("同名 Client 已存在，且不属于本次注册")
        expected = {"jiuwen.managed_by": MANAGED_BY,
                    "jiuwen.registration_id": str(registration_id),
                    "jiuwen.registration_owner": owner}
        if any(attributes.get(key) != value for key, value in expected.items()):
            raise RegistrationError("同名 Client 已存在，且不属于本次注册")
        return row

    def _created_client_id(self, response: httpx.Response) -> str | None:
        location = response.headers.get("Location")
        if not location:
            return None
        try:
            url = response.request.url.join(location)
        except httpx.InvalidURL:
            return None
        client_uuid = url.path.rsplit("/", 1)[-1]
        # Use only a Location under this realm's configured clients endpoint.
        if client_uuid and url == self._endpoint("clients", client_uuid):
            return client_uuid
        return None

    async def _rollback_creation(
        self, http: httpx.AsyncClient, client_uuid: str | None, registration_id: UUID, owner: str,
    ) -> None:
        try:
            if client_uuid is None:
                row = await self._find(http, registration_id, owner)
                if row is None:
                    return
                client_uuid = row["id"]
            await self._request(
                http, "DELETE", self._endpoint("clients", client_uuid),
                "清理未保存的 Agent 客户端", allowed=(404,),
            )
        except RegistrationError:
            logger.warning("Agent client rollback incomplete: registration_id=%s", registration_id)

    async def _ensure_audience(self, http: httpx.AsyncClient, row: dict) -> None:
        url = self._endpoint("clients", row["id"], "protocol-mappers", "models")
        response = await self._request(http, "GET", url, "读取受众配置")
        if self._has_audience(self._json(response, list, "读取受众配置")):
            return
        response = await self._request(http, "POST", url, "配置 Agent 受众", json={
            "name": "jiuwen-agent-audience", "protocol": "openid-connect",
            "protocolMapper": "oidc-audience-mapper", "consentRequired": False,
            "config": {"included.custom.audience": self.config.agent_token_audience,
                       "access.token.claim": "true", "id.token.claim": "false"},
        }, allowed=(409,))
        if response.status_code == 409:
            response = await self._request(http, "GET", url, "读取受众配置")
            if not self._has_audience(self._json(response, list, "读取受众配置")):
                raise RegistrationError("配置 Agent 受众失败：已有同名 Mapper，请检查受众配置")

    def _has_audience(self, mappers: list) -> bool:
        return any(isinstance(mapper, dict)
                   and isinstance(mapper.get("config"), dict)
                   and mapper.get("protocolMapper") == "oidc-audience-mapper"
                   and mapper["config"].get("included.custom.audience") == self.config.agent_token_audience
                   and mapper["config"].get("access.token.claim") == "true"
                   for mapper in mappers)

    async def register(self, registration_id: UUID, template_name: str, owner: str) -> dict:
        async with self._client() as http:
            await self._authorize(http)
            row = await self._find(http, registration_id, owner)
            created = False
            created_id = None
            try:
                if row is None:
                    body = {
                        "clientId": CLIENT_PREFIX + registration_id.hex, "name": template_name,
                        "enabled": True, "protocol": "openid-connect", "publicClient": False,
                        "clientAuthenticatorType": "client-secret", "serviceAccountsEnabled": True,
                        "standardFlowEnabled": False, "implicitFlowEnabled": False,
                        "directAccessGrantsEnabled": False,
                        "attributes": {"jiuwen.managed_by": MANAGED_BY,
                                       "jiuwen.registration_id": str(registration_id),
                                       "jiuwen.registration_owner": owner},
                    }
                    response = await self._request(http, "POST", self._endpoint("clients"),
                                                   "创建 Agent 客户端", json=body, allowed=(409,))
                    created = response.status_code == 201
                    if created:
                        created_id = self._created_client_id(response)
                    row = await self._find(http, registration_id, owner)
                    if row is None:
                        raise RegistrationError("创建 Agent 客户端后未能取得内部标识，请重试")
                    if created and created_id is not None and row["id"] != created_id:
                        raise RegistrationError("创建 Agent 客户端后的内部标识不一致，请重试")
                await self._ensure_audience(http, row)
                response = await self._request(
                    http, "GET", self._endpoint("clients", row["id"], "client-secret"), "读取 Agent Secret",
                )
                secret = self._json(response, dict, "读取 Agent Secret").get("value")
                if not isinstance(secret, str) or not secret.strip():
                    raise RegistrationError("读取 Agent Secret 失败：响应缺少密钥")
                response = await self._request(http, "POST", self.config.token_url, "验证 Agent 凭据", data={
                    "grant_type": "client_credentials", "client_id": row["clientId"], "client_secret": secret,
                }, headers={"Authorization": ""})
                token = self._json(response, dict, "验证 Agent 凭据")
                if not isinstance(token.get("access_token"), str) or not token["access_token"]:
                    raise RegistrationError("验证 Agent 凭据失败：响应缺少 access_token")
                return {"registration_id": str(registration_id), "client_uuid": row["id"],
                        "client_id": row["clientId"], "client_secret": secret}
            except RegistrationError:
                if created:
                    await self._rollback_creation(http, created_id, registration_id, owner)
                raise

    async def cancel(self, registration_id: UUID, owner: str) -> bool:
        async with self._client() as http:
            await self._authorize(http)
            row = await self._find(http, registration_id, owner)
            if row is None:
                return False
            await self._request(
                http, "DELETE", self._endpoint("clients", row["id"]),
                "清理未保存的 Agent 客户端", allowed=(404,),
            )
            return True


def _file_version(path: str) -> tuple[int, int, int, int]:
    stat = Path(path).stat()
    return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino


@lru_cache(maxsize=1)
def _read_registration_config(path: str, _version: tuple[int, int, int, int]) -> RegistrationConfig:
    return RegistrationConfig.model_validate_json(Path(path).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _cached_registration_service(
    path: str, version: tuple[int, int, int, int], _ca_version: tuple[int, int, int, int] | None,
) -> KeycloakAgentClients:
    # File revisions invalidate both caches, including mounted CA rotations.
    return KeycloakAgentClients(_read_registration_config(path, version))
