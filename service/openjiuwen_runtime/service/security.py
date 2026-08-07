# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Small FastAPI-native OAuth2 access-control integration.

The framework declares the OAuth2 bearer scheme and delegates token validation
to an application supplied async function.  User storage and token issuance stay
in the application; this module only provides request protection and OpenAPI
integration. Password and Authorization Code declarations are supported; token
issuance remains outside the framework.
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2AuthorizationCodeBearer, OAuth2PasswordBearer

TokenValidator = Callable[[str], Awaitable[Any]]


class OAuth2AccessControl:
    """Protect REST handlers with a bearer token and expose OAuth2 in OpenAPI."""

    def __init__(
        self,
        *,
        token_url: str,
        token_validator: TokenValidator,
        authorization_url: str | None = None,
        scheme_name: str = "OAuth2PasswordBearer",
    ) -> None:
        if not token_url:
            raise ValueError("token_url must be provided")
        if not inspect.iscoroutinefunction(token_validator):
            raise TypeError("token_validator must be declared with async def")

        if authorization_url:
            self.scheme = OAuth2AuthorizationCodeBearer(
                authorizationUrl=authorization_url,
                tokenUrl=token_url,
                scheme_name=scheme_name,
            )
        else:
            self.scheme = OAuth2PasswordBearer(
                tokenUrl=token_url,
                scheme_name=scheme_name,
            )
        self._token_validator = token_validator

        scheme = self.scheme

        async def authenticate(token: str = Depends(scheme)) -> Any:
            principal = await self._token_validator(token)
            if principal is None or principal is False:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid authentication credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return principal

        self.dependency = authenticate


__all__ = ["OAuth2AccessControl", "TokenValidator"]
