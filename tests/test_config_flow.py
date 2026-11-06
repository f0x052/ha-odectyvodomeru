"""Tests for initial setup and reauthentication."""

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import probatio
import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.odectyvodomeru.const import API_BASE_URL, CONF_METERS, DOMAIN

CREDENTIALS = {"username": "resident@example.test", "password": "synthetic-password"}


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Iterator[AsyncMock]:
    """Test the flow without starting a second authentication during setup."""
    with patch(
        "custom_components.odectyvodomeru.async_setup_entry", return_value=True
    ) as mocked:
        yield mocked


def mock_login(responses: AiohttpClientMocker, account_id: int = 42) -> None:
    """Provide a synthetic successful login."""
    responses.post(f"{API_BASE_URL}/api/login_check", json={"token": "synthetic-token"})
    responses.get(f"{API_BASE_URL}/api/user/profile", json={"user": {"id": account_id}})


async def test_user_flow(
    hass: HomeAssistant, responses: AiohttpClientMocker, discovery: dict
) -> None:
    """Create an entry only after the user explicitly selects meters."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    mock_login(responses)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**CREDENTIALS, "username": " resident@example.test "}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "meters"
    assert result["data_schema"]({}) == {CONF_METERS: []}
    assert not hass.config_entries.async_entries(DOMAIN)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_METERS: []}
    )
    assert result["errors"] == {"base": "select_meters"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_METERS: ["101", "103"]}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Odecty vodomeru (resident@example.test)"
    assert result["data"] == CREDENTIALS
    assert result["result"].unique_id == "42"
    assert result["options"] == {CONF_METERS: ["101", "103"]}


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, "invalid_auth"),
        (403, "access_restricted"),
        (429, "rate_limited"),
        (503, "cannot_connect"),
        (400, "invalid_response"),
    ],
)
async def test_login_errors(
    hass: HomeAssistant, responses: AiohttpClientMocker, status: int, error: str
) -> None:
    """Keep the form open with a specific error and no entry."""
    responses.post(f"{API_BASE_URL}/api/login_check", status=status)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=CREDENTIALS
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_duplicate_account(
    hass: HomeAssistant, responses: AiohttpClientMocker
) -> None:
    """Prevent duplicate entries using the API account ID."""
    MockConfigEntry(domain=DOMAIN, unique_id="42", data=CREDENTIALS).add_to_hass(hass)
    mock_login(responses)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=CREDENTIALS
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


@pytest.mark.parametrize("account_id", [42, 99])
@pytest.mark.parametrize(
    "title",
    ["Odecty vodomeru", "Odecty vodomeru (resident@example.test)", "My meters"],
)
async def test_reauthentication(
    hass: HomeAssistant, responses: AiohttpClientMocker, account_id: int, title: str
) -> None:
    """Update the same entry and reject a switch to a different account."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        title=title,
        data=CREDENTIALS,
        options={CONF_METERS: ["101"]},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    mock_login(responses, account_id)
    updated = {
        "username": "new-login@example.test",
        "password": "synthetic-new-password",
    }
    with patch.object(hass.config_entries, "async_reload", return_value=True) as reload:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], updated
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert entry.options == {CONF_METERS: ["101"]}
    if account_id == 42:
        assert result["reason"] == "reauth_successful"
        assert entry.data == updated
        assert entry.title == (
            "My meters"
            if title == "My meters"
            else "Odecty vodomeru (new-login@example.test)"
        )
        reload.assert_awaited_once_with(entry.entry_id)
    else:
        assert result["reason"] == "unique_id_mismatch"
        assert entry.data == CREDENTIALS
        assert entry.title == title
        reload.assert_not_awaited()


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, "invalid_auth"),
        (403, "access_restricted"),
        (429, "rate_limited"),
        (503, "cannot_connect"),
        (400, "invalid_response"),
    ],
)
async def test_discovery_errors(
    hass: HomeAssistant, responses: AiohttpClientMocker, status: int, error: str
) -> None:
    """Discovery failures do not persist a half-configured entry."""
    mock_login(responses)
    responses.get(f"{API_BASE_URL}/api/structure/user-buildings", status=status)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=CREDENTIALS
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_no_meters(hass: HomeAssistant, responses: AiohttpClientMocker) -> None:
    """Explain an empty account without automatically configuring it."""
    mock_login(responses)
    responses.get(
        f"{API_BASE_URL}/api/structure/user-buildings", json={"buildings": []}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=CREDENTIALS
    )
    assert result["errors"] == {"base": "no_meters"}
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize(
    ("language", "label"), [("en", "Cold water"), ("cs", "Studená voda")]
)
async def test_selection_labels(
    hass: HomeAssistant,
    responses: AiohttpClientMocker,
    discovery: dict,
    language: str,
    label: str,
) -> None:
    """Translate meter types and distinguish physical meters by serial and ID."""
    hass.config.language = language
    mock_login(responses)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=CREDENTIALS
    )
    selector = next(iter(result["data_schema"].schema.values()))
    options = selector.config["options"]
    assert len(options) == 3
    assert options[0]["label"] == f"{label} - Test manufacturer - TEST-101 - #101"


async def test_options_for_existing_login(
    hass: HomeAssistant, responses: AiohttpClientMocker, discovery: dict
) -> None:
    """Let a login-only entry select meters and change the saved selection."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="42", data=CREDENTIALS, options={"unrelated": True}
    )
    entry.add_to_hass(hass)
    mock_login(responses)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["data_schema"]({}) == {CONF_METERS: []}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_METERS: []}
    )
    assert result["errors"] == {"base": "select_meters"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_METERS: ["102"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_METERS: ["102"], "unrelated": True}
    assert entry.data == CREDENTIALS

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["data_schema"]({}) == {CONF_METERS: ["102"]}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_METERS: ["101", "103"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_METERS] == ["101", "103"]


@pytest.mark.parametrize(
    ("status", "error"),
    [(429, "rate_limited"), (503, "cannot_connect"), (400, "invalid_response")],
)
async def test_options_retry(
    hass: HomeAssistant, responses: AiohttpClientMocker, status: int, error: str
) -> None:
    """A failed discovery can be retried without losing the saved selection."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        data=CREDENTIALS,
        options={CONF_METERS: ["101"]},
    )
    entry.add_to_hass(hass)
    mock_login(responses)
    responses.get(f"{API_BASE_URL}/api/structure/user-buildings", status=status)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["errors"] == {"base": error}
    assert entry.options == {CONF_METERS: ["101"]}
    responses.clear_requests()
    mock_login(responses)
    responses.get(
        f"{API_BASE_URL}/api/structure/user-buildings",
        json={"buildings": [{"structure": {"id": 10}}]},
    )
    responses.get(
        f"{API_BASE_URL}/api/structure/sub-structures/10",
        json={
            "structures": [
                {
                    "structure": {
                        "devices": [
                            {
                                "id": 101,
                                "deviceId": "TEST-101",
                                "measurementTypeString": "cold",
                                "deinstalled": None,
                                "manufacturer": {"name": "Test"},
                            }
                        ]
                    }
                }
            ]
        },
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["errors"] == {}
    assert result["data_schema"]({}) == {CONF_METERS: ["101"]}


@pytest.mark.parametrize("status", [401, 403])
async def test_options_auth_failure(
    hass: HomeAssistant, responses: AiohttpClientMocker, status: int
) -> None:
    """Start reauthentication rather than saving choices under invalid credentials."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        data=CREDENTIALS,
        options={CONF_METERS: ["101"]},
    )
    entry.add_to_hass(hass)
    responses.post(f"{API_BASE_URL}/api/login_check", status=status)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_required"
    assert entry.options == {CONF_METERS: ["101"]}
    assert (
        hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]["context"][
            "source"
        ]
        == SOURCE_REAUTH
    )


async def test_options_account_mismatch(
    hass: HomeAssistant, responses: AiohttpClientMocker
) -> None:
    """Never discover or save meters for a different account."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id="42", data=CREDENTIALS)
    entry.add_to_hass(hass)
    mock_login(responses, 99)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["reason"] == "unique_id_mismatch"
    assert not entry.options


async def test_cancel_selection(
    hass: HomeAssistant, responses: AiohttpClientMocker, discovery: dict
) -> None:
    """Canceling setup creates nothing, and canceling options preserves choices."""
    mock_login(responses)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=CREDENTIALS
    )
    hass.config_entries.flow.async_abort(result["flow_id"])
    assert not hass.config_entries.async_entries(DOMAIN)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        data=CREDENTIALS,
        options={CONF_METERS: ["103"]},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    hass.config_entries.options.async_abort(result["flow_id"])
    assert entry.options == {CONF_METERS: ["103"]}


@pytest.mark.parametrize("selection", [["999"], ["101", "999"], "101", [101]])
async def test_unknown_selection(
    hass: HomeAssistant,
    responses: AiohttpClientMocker,
    discovery: dict,
    selection: object,
) -> None:
    """The selector schema rejects unknown IDs and non-string/list inputs."""
    mock_login(responses)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=CREDENTIALS
    )
    with pytest.raises(probatio.Invalid):
        result["data_schema"]({CONF_METERS: selection})
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_options_drop_unavailable_defaults(
    hass: HomeAssistant, responses: AiohttpClientMocker, discovery: dict
) -> None:
    """Absent meters are not offered, but old choices are untouched until saving."""
    mock_login(responses)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        data=CREDENTIALS,
        options={CONF_METERS: ["101", "999"]},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["data_schema"]({}) == {CONF_METERS: ["101"]}
    assert entry.options[CONF_METERS] == ["101", "999"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_METERS: ["101"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_METERS] == ["101"]


async def test_options_no_meters(
    hass: HomeAssistant, responses: AiohttpClientMocker
) -> None:
    """An empty discovery leaves existing choices intact and allows retry."""
    mock_login(responses)
    responses.get(
        f"{API_BASE_URL}/api/structure/user-buildings", json={"buildings": []}
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        data=CREDENTIALS,
        options={CONF_METERS: ["101"]},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_meters"}
    assert entry.options[CONF_METERS] == ["101"]
