"""Test entry setup, reload, and authentication failures."""

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.odectyvodomeru.const import API_BASE_URL, CONF_METERS, DOMAIN
from custom_components.odectyvodomeru.coordinator import MeterCoordinator

CREDENTIALS = {"username": "resident@example.test", "password": "synthetic-password"}


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Odecty vodomeru", "Odecty vodomeru (resident@example.test)"),
        (
            "Odecty vodomeru (resident@example.test)",
            "Odecty vodomeru (resident@example.test)",
        ),
        ("My meters", "My meters"),
    ],
)
async def test_service_title(
    hass: HomeAssistant,
    responses: AiohttpClientMocker,
    title: str,
    expected: str,
) -> None:
    """Upgrade the original service name once without overwriting custom titles."""
    responses.post(f"{API_BASE_URL}/api/login_check", json={"token": "synthetic-token"})
    responses.get(f"{API_BASE_URL}/api/user/profile", json={"user": {"id": 42}})
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        title=title,
        data={**CREDENTIALS, "username": " resident@example.test "},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.title == expected
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.title == expected


@pytest.mark.parametrize(
    ("language", "cold_name", "heat_name"),
    [("en", "Cold water", "Heat meter"), ("cs", "Studená voda", "Měřič tepla")],
)
async def test_setup_reload_unload(
    hass: HomeAssistant,
    responses: AiohttpClientMocker,
    discovery: dict,
    language: str,
    cold_name: str,
    heat_name: str,
) -> None:
    """Register selected devices with stable IDs across reloads."""
    hass.config.language = language
    responses.post(
        f"{API_BASE_URL}/api/login_check",
        json={"token": "synthetic-token"},
    )
    responses.get(
        f"{API_BASE_URL}/api/user/profile",
        json={"user": {"id": 42}},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        data=CREDENTIALS,
        options={CONF_METERS: ["101", "103"]},
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.options == {CONF_METERS: ["101", "103"]}
    assert isinstance(entry.runtime_data, MeterCoordinator)
    assert entry.data == CREDENTIALS
    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(devices) == 2
    original_ids = {device.id for device in devices}
    assert {next(iter(device.identifiers)) for device in devices} == {
        (DOMAIN, "42_101"),
        (DOMAIN, "42_103"),
    }
    for device in devices:
        assert device.manufacturer == "Test manufacturer"
        assert device.serial_number in {"TEST-101", "TEST-103"}
        assert device.name in {f"{cold_name} TEST-101", f"{heat_name} TEST-103"}
        assert device.configuration_url == "https://odectyvodomeru.cz"
    assert (
        len(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)) == 6
    )

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert {
        device.id
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
    } == original_ids
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert entry.options == {CONF_METERS: ["101", "103"]}


@pytest.mark.parametrize(
    ("status", "state"),
    [
        (401, ConfigEntryState.SETUP_ERROR),
        (403, ConfigEntryState.SETUP_ERROR),
        (429, ConfigEntryState.SETUP_RETRY),
        (503, ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_setup_errors(
    hass: HomeAssistant,
    responses: AiohttpClientMocker,
    status: int,
    state: ConfigEntryState,
) -> None:
    """Let Home Assistant distinguish reauthentication from retryable failures."""
    responses.post(f"{API_BASE_URL}/api/login_check", status=status)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="42", data=CREDENTIALS)
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is state
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    if status in (401, 403):
        assert len(flows) == 1
        assert flows[0]["context"]["source"] == "reauth"
    else:
        assert not flows


async def test_setup_rejects_account_change(
    hass: HomeAssistant, responses: AiohttpClientMocker
) -> None:
    """Do not silently load a different account after a restart."""
    responses.post(f"{API_BASE_URL}/api/login_check", json={"token": "synthetic-token"})
    responses.get(f"{API_BASE_URL}/api/user/profile", json={"user": {"id": 99}})
    entry = MockConfigEntry(domain=DOMAIN, unique_id="42", data=CREDENTIALS)
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_selection_changes_devices(
    hass: HomeAssistant, responses: AiohttpClientMocker, discovery: dict
) -> None:
    """Apply options through the real flow and retain names of unchanged devices."""
    responses.post(f"{API_BASE_URL}/api/login_check", json={"token": "synthetic-token"})
    responses.get(f"{API_BASE_URL}/api/user/profile", json={"user": {"id": 42}})
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        data=CREDENTIALS,
        options={CONF_METERS: ["101", "103"]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    registry = dr.async_get(hass)
    retained = registry.async_get_device_by_identifier(
        (DOMAIN, "42_101"), entry.entry_id
    )
    registry.async_update_device(retained.id, name_by_user="Kitchen water")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_METERS: ["101", "102"]}
    )
    await hass.async_block_till_done()
    devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(devices) == 2
    assert (
        registry.async_get_device_by_identifier((DOMAIN, "42_103"), entry.entry_id)
        is None
    )
    assert (
        registry.async_get_device_by_identifier((DOMAIN, "42_102"), entry.entry_id)
        is not None
    )
    retained = registry.async_get_device_by_identifier(
        (DOMAIN, "42_101"), entry.entry_id
    )
    assert retained.name_by_user == "Kitchen water"
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize("language", ["en", "cs"])
async def test_no_selection(
    hass: HomeAssistant, responses: AiohttpClientMocker, language: str
) -> None:
    """An upgraded login-only entry does not automatically add any device."""
    hass.config.language = language
    responses.post(f"{API_BASE_URL}/api/login_check", json={"token": "synthetic-token"})
    responses.get(f"{API_BASE_URL}/api/user/profile", json={"user": {"id": 42}})
    entry = MockConfigEntry(domain=DOMAIN, unique_id="42", data=CREDENTIALS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert not dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert responses.call_count == 2


async def test_missing_selected_meter_is_retained(
    hass: HomeAssistant,
    responses: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing portal reading must not delete a still-selected device."""
    responses.post(f"{API_BASE_URL}/api/login_check", json={"token": "synthetic-token"})
    responses.get(f"{API_BASE_URL}/api/user/profile", json={"user": {"id": 42}})
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
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "42_101")},
        name="Existing meter",
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert registry.async_get(device.id) is not None
    assert "no longer returned" in caplog.text


@pytest.mark.parametrize(
    ("status", "state"),
    [(401, ConfigEntryState.SETUP_ERROR), (503, ConfigEntryState.SETUP_RETRY)],
)
async def test_discovery_failure_keeps_devices(
    hass: HomeAssistant,
    responses: AiohttpClientMocker,
    status: int,
    state: ConfigEntryState,
) -> None:
    """Discovery failures never erase previously registered devices."""
    responses.post(f"{API_BASE_URL}/api/login_check", json={"token": "synthetic-token"})
    responses.get(f"{API_BASE_URL}/api/user/profile", json={"user": {"id": 42}})
    responses.get(f"{API_BASE_URL}/api/structure/user-buildings", status=status)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        data=CREDENTIALS,
        options={CONF_METERS: ["101"]},
    )
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "42_101")},
        name="Existing meter",
    )
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is state
    assert registry.async_get(device.id) is not None
