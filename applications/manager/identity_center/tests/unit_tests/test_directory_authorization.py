"""Authorization API decisions must be current, explicit, and bound to the caller."""

import httpx
import pytest

from identity_center.core.authorization import (
    AuthorizationSessionInvalidError,
    AuthorizationUnavailableError,
    DirectoryAuthorizationClient,
)


@pytest.mark.asyncio
async def test_authorization_queries_the_same_subject_on_every_request():
    requests = []
    allowed = True

    def authorize(request):
        requests.append(request)
        return httpx.Response(200, json={
            "code": 200, "data": {"user_id": "A", "is_platform_admin": allowed},
        })

    client = DirectoryAuthorizationClient(
        "https://authorization.test/api/v1/authz/me", httpx.MockTransport(authorize),
    )
    try:
        assert await client.is_directory_admin("session-A", "A") is True
        allowed = False
        assert await client.is_directory_admin("session-A", "A") is False
    finally:
        await client.close()
    assert len(requests) == 2
    assert all(request.headers["Authorization"] == "Bearer session-A" for request in requests)
    assert all(str(request.url) == "https://authorization.test/api/v1/authz/me" for request in requests)


@pytest.mark.parametrize("data", [
    {"code": 200, "data": {"user_id": "B", "is_platform_admin": True}},
    {"code": 200, "data": {"user_id": "A", "is_platform_admin": "true"}},
    {"code": 200, "data": {"user_id": "A", "is_platform_admin": 1}},
    {"code": 200, "data": {"user_id": "A", "manager_access": True}},
    {"code": 500, "data": {"user_id": "A", "is_platform_admin": True}},
    {"data": {"user_id": "A", "is_platform_admin": True}},
])
@pytest.mark.asyncio
async def test_invalid_or_mismatched_authorization_is_unavailable(data):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=data))
    client = DirectoryAuthorizationClient("https://authorization.test/me", transport)
    try:
        with pytest.raises(AuthorizationUnavailableError):
            await client.is_directory_admin("session-A", "A")
    finally:
        await client.close()


@pytest.mark.parametrize("http_status", [302, 500, 503])
@pytest.mark.asyncio
async def test_failed_authorization_service_does_not_allow_directory_access(http_status):
    transport = httpx.MockTransport(lambda request: httpx.Response(http_status))
    client = DirectoryAuthorizationClient("https://authorization.test/me", transport)
    try:
        with pytest.raises(AuthorizationUnavailableError):
            await client.is_directory_admin("session-A", "A")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_authorization_timeout_is_unavailable():
    def timeout(request):
        raise httpx.ReadTimeout("authorization timeout", request=request)

    client = DirectoryAuthorizationClient("https://authorization.test/me", httpx.MockTransport(timeout))
    try:
        with pytest.raises(AuthorizationUnavailableError):
            await client.is_directory_admin("session-A", "A")
    finally:
        await client.close()


@pytest.mark.parametrize("http_status", [401, 403])
@pytest.mark.asyncio
async def test_authorization_rejects_the_session_or_permission(http_status):
    transport = httpx.MockTransport(lambda request: httpx.Response(http_status))
    client = DirectoryAuthorizationClient("https://authorization.test/me", transport)
    try:
        if http_status == 401:
            with pytest.raises(AuthorizationSessionInvalidError):
                await client.is_directory_admin("session-A", "A")
        else:
            assert await client.is_directory_admin("session-A", "A") is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_missing_endpoint_makes_no_outbound_request():
    def unexpected_request(request):
        pytest.fail("missing endpoint must not make an outbound request")

    client = DirectoryAuthorizationClient("", httpx.MockTransport(unexpected_request))
    try:
        with pytest.raises(AuthorizationUnavailableError):
            await client.is_directory_admin("session-A", "A")
    finally:
        await client.close()
