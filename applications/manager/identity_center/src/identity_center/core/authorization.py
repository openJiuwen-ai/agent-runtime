"""Query directory-management eligibility through the authorization service API."""

from __future__ import annotations

import asyncio
from typing import Literal

import httpx
from pydantic import BaseModel, StrictBool, StrictStr


class AuthorizationUnavailableError(RuntimeError):
    """The authorization service did not return a valid decision."""


class AuthorizationSessionInvalidError(RuntimeError):
    """The authorization service rejected the caller's login session."""


class _AuthorizationData(BaseModel):
    user_id: StrictStr
    is_platform_admin: StrictBool


class _AuthorizationResponse(BaseModel):
    code: Literal[200]
    data: _AuthorizationData


class DirectoryAuthorizationClient:
    """Resolve current management eligibility without accessing the role database."""

    def __init__(
        self,
        manager_base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        base_url = manager_base_url.strip().rstrip("/")
        self._endpoint = base_url + "/api/v1/authz/me" if base_url else ""
        self._http = httpx.AsyncClient(
            timeout=3.0, trust_env=False, follow_redirects=False, transport=transport,
        )

    async def is_directory_admin(self, token: str, user_id: str) -> bool:
        """Check the supplied session's latest eligibility, bound to its subject."""
        if not self._endpoint:
            raise AuthorizationUnavailableError("authorization endpoint is not configured")
        try:
            async with asyncio.timeout(3.0):
                response = await self._http.get(
                    self._endpoint,
                    headers={"Authorization": f"Bearer {token}"},
                )
                if response.status_code == 401:
                    raise AuthorizationSessionInvalidError("login session rejected")
                if response.status_code == 403:
                    return False
                response.raise_for_status()
                decision = _AuthorizationResponse.model_validate(response.json())
        except (httpx.HTTPError, httpx.InvalidURL, TimeoutError, ValueError) as exc:
            raise AuthorizationUnavailableError("authorization query failed") from exc
        if decision.data.user_id != user_id:
            raise AuthorizationUnavailableError("authorization subject mismatch")
        return decision.data.is_platform_admin

    async def close(self) -> None:
        """Close the HTTP connection pool owned by this application."""
        await self._http.aclose()
