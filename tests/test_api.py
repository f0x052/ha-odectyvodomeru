"""Tests for the portal authentication API."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from aiohttp import ClientConnectionError, ClientSession, ClientTimeout
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.odectyvodomeru.api import (
    PortalAccessRestrictedError,
    PortalAuthenticationError,
    PortalClient,
    PortalConnectionError,
    PortalRateLimitError,
    PortalResponseError,
)
from custom_components.odectyvodomeru.const import API_BASE_URL, REQUEST_TIMEOUT

LOGIN_URL = f"{API_BASE_URL}/api/login_check"
PROFILE_URL = f"{API_BASE_URL}/api/user/profile"


async def test_authenticate(responses: AiohttpClientMocker) -> None:
    """Validate login and a protected request without changing shared headers."""
    responses.post(LOGIN_URL, json={"token": "synthetic-access-token"})
    responses.get(PROFILE_URL, json={"user": {"id": 42}})

    async with responses.create_session(asyncio.get_running_loop()) as session:
        client = PortalClient(session)
        assert (
            await client.async_authenticate(" resident@example.test ", " secret ")
            == "42"
        )
        assert "Authorization" not in session.headers

    login, profile = responses.mock_calls
    assert str(login[1]) == LOGIN_URL
    assert login[2] == {"username": "resident@example.test", "password": " secret "}
    assert login[3] == {}
    assert str(profile[1]) == PROFILE_URL
    assert profile[3] == {"Authorization": "Bearer synthetic-access-token"}


async def test_request_options() -> None:
    """Bound requests and do not forward credentials through redirects."""
    session = MagicMock(spec=ClientSession)
    response = session.request.return_value.__aenter__.return_value
    response.status = 200
    response.json = AsyncMock(
        side_effect=[{"token": "synthetic-access-token"}, {"user": {"id": 42}}]
    )
    assert await PortalClient(session).async_authenticate("resident", "secret") == "42"
    assert session.request.call_count == 2
    for call in session.request.call_args_list:
        assert call.kwargs["allow_redirects"] is False
        assert call.kwargs["timeout"] == ClientTimeout(total=REQUEST_TIMEOUT)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, PortalAuthenticationError),
        (403, PortalAccessRestrictedError),
        (429, PortalRateLimitError),
        (500, PortalConnectionError),
        (503, PortalConnectionError),
        (302, PortalResponseError),
        (400, PortalResponseError),
    ],
)
@pytest.mark.parametrize("endpoint", ["login", "profile"])
async def test_http_errors(
    responses: AiohttpClientMocker, status: int, error: type[Exception], endpoint: str
) -> None:
    """Classify errors from both login and the protected profile."""
    if endpoint == "profile":
        responses.post(LOGIN_URL, json={"token": "synthetic-access-token"})
        responses.get(PROFILE_URL, status=status, text="private server details")
    else:
        responses.post(LOGIN_URL, status=status, text="private server details")

    async with responses.create_session(asyncio.get_running_loop()) as session:
        with pytest.raises(error) as raised:
            await PortalClient(session).async_authenticate("resident", "secret")
    assert "private server details" not in str(raised.value)
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize(
    "payload",
    [{}, {"token": None}, {"token": ""}, {"token": " "}, {"token": 123}, []],
)
async def test_invalid_login_payload(
    responses: AiohttpClientMocker, payload: object
) -> None:
    """A successful HTTP response alone is not a successful login."""
    responses.post(LOGIN_URL, json=payload)
    async with responses.create_session(asyncio.get_running_loop()) as session:
        with pytest.raises(PortalResponseError):
            await PortalClient(session).async_authenticate("resident", "secret")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"user": None},
        {"user": {}},
        {"user": {"id": None}},
        {"user": {"id": True}},
        {"user": {"id": 0}},
        {"user": {"id": "42"}},
    ],
)
async def test_invalid_profile_payload(
    responses: AiohttpClientMocker, payload: object
) -> None:
    """Do not create entries without a stable, valid account ID."""
    responses.post(LOGIN_URL, json={"token": "synthetic-access-token"})
    responses.get(PROFILE_URL, json=payload)
    async with responses.create_session(asyncio.get_running_loop()) as session:
        with pytest.raises(PortalResponseError):
            await PortalClient(session).async_authenticate("resident", "secret")


@pytest.mark.parametrize("content_type", ["text/html", "application/json"])
async def test_invalid_json(responses: AiohttpClientMocker, content_type: str) -> None:
    """Report HTML error pages and broken JSON as invalid responses."""
    responses.post(
        LOGIN_URL,
        text="<html>unavailable</html>",
        headers={"content-type": content_type},
    )
    async with responses.create_session(asyncio.get_running_loop()) as session:
        with pytest.raises(PortalResponseError):
            await PortalClient(session).async_authenticate("resident", "secret")


@pytest.mark.parametrize("error", [ClientConnectionError("offline"), TimeoutError()])
async def test_network_errors(responses: AiohttpClientMocker, error: Exception) -> None:
    """Connection failures and timeouts are not credential failures."""
    responses.post(LOGIN_URL, exc=error)
    async with responses.create_session(asyncio.get_running_loop()) as session:
        with pytest.raises(PortalConnectionError):
            await PortalClient(session).async_authenticate("resident", "secret")


@pytest.mark.parametrize(
    ("username", "password"), [("", "secret"), (" ", "secret"), ("resident", "")]
)
async def test_empty_credentials(username: str, password: str) -> None:
    """Reject missing credentials before making any request."""
    session = Mock(spec=ClientSession)
    with pytest.raises(PortalAuthenticationError):
        await PortalClient(session).async_authenticate(username, password)
    session.request.assert_not_called()
