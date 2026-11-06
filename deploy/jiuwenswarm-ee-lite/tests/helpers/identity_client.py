# coding: utf-8
"""identity_center 客户端：OAuth2 密码流登录 + 当前用户。"""

from __future__ import annotations

from typing import Any

import httpx


class IdentityClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def login(self, username: str, password: str) -> dict[str, Any]:
        """OAuth2 密码流 → {access_token, token_type, expires_in, refresh_token}。"""
        resp = httpx.post(
            f"{self.base_url}/v1/auth/token",
            data={"username": username, "password": password},
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()

    def me(self, access_token: str) -> dict[str, Any]:
        resp = httpx.get(
            f"{self.base_url}/v1/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()

    def public_key(self) -> str:
        resp = httpx.get(f"{self.base_url}/v1/auth/public_key", timeout=15.0)
        resp.raise_for_status()
        return resp.text
