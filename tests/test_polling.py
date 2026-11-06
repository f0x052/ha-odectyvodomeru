"""Verify cumulative sensor semantics and coordinated polling."""

from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.odectyvodomeru.api import (
    Meter,
    PortalAccessRestrictedError,
    PortalAuthenticationError,
    PortalConnectionError,
    PortalResponseError,
    Reading,
    _reading,
)
from custom_components.odectyvodomeru.const import API_BASE_URL, CONF_METERS, DOMAIN
from custom_components.odectyvodomeru.coordinator import MeterCoordinator

CREDENTIALS = {"username": "resident@example.test", "password": "synthetic-password"}


@pytest.fixture
def mock_recorder_before_hass(recorder_db_url: str) -> None:
    """Prepare Recorder's test database before Home Assistant is constructed."""


async def setup(
    hass: HomeAssistant, responses: AiohttpClientMocker, selected: list[str]
) -> MockConfigEntry:
    """Load a real sensor platform with synthetic authenticated responses."""
    responses.post(f"{API_BASE_URL}/api/login_check", json={"token": "synthetic-token"})
    responses.get(f"{API_BASE_URL}/api/user/profile", json={"user": {"id": 42}})
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        data=CREDENTIALS,
        options={CONF_METERS: selected},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def entity_id(hass: HomeAssistant, meter_id: str, suffix: str = "reading") -> str:
    """Find a sensor by stable registry identity, not its localized display name."""
    found = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"42_{meter_id}_{suffix}"
    )
    assert found is not None
    return found


async def test_sensor_values(
    hass: HomeAssistant, responses: AiohttpClientMocker, discovery: dict
) -> None:
    """Expose native units, total statistics, timestamps, and correct device links."""
    entry = await setup(hass, responses, ["101", "102", "103"])
    for meter_id in ("101", "102", "103"):
        state = hass.states.get(entity_id(hass, meter_id))
        assert state.state == str(int(meter_id) / 10)
        assert state.attributes["state_class"] == "total"
        assert state.attributes["unit_of_measurement"] == (
            "MWh" if meter_id == "103" else "m³"
        )
        assert state.attributes["device_class"] == (
            "energy" if meter_id == "103" else "water"
        )
        timestamp = hass.states.get(entity_id(hass, meter_id, "last_reading"))
        assert datetime.fromisoformat(timestamp.state) == datetime.fromisoformat(
            "2026-11-06T03:00:00+01:00"
        )
        assert timestamp.attributes["device_class"] == "timestamp"
        sensor = er.async_get(hass).async_get(entity_id(hass, meter_id))
        device = dr.async_get(hass).async_get(sensor.device_id)
        assert (DOMAIN, f"42_{meter_id}") in device.identifiers
    assert entry.runtime_data.update_interval == timedelta(hours=1)
    assert responses.call_count == 4


async def test_information_sensor(
    hass: HomeAssistant,
    responses: AiohttpClientMocker,
    discovery: dict,
) -> None:
    """Show location on the device's diagnostic entity and refresh metadata alone."""
    entry = await setup(hass, responses, ["101", "103"])
    identifier = entity_id(hass, "101", "information")
    state = hass.states.get(identifier)
    assert state.state == "TEST-101"
    assert state.attributes["address"] == "Example Street 12, 12345 Test City"
    assert state.attributes["flat_number"] == 7
    assert state.attributes["floor_number"] == 0
    assert state.attributes["household_id"] == "20"
    assert state.attributes["rssi"] == "-71"
    assert state.attributes["portal_meter_id"] == "101"
    assert state.attributes["meter_type"] == "cold"
    assert state.attributes["meter_unit"] == "m³"
    assert "state_class" not in state.attributes
    assert "address" not in hass.states.get(entity_id(hass, "101")).attributes
    assert (
        hass.states.get(entity_id(hass, "103", "information")).attributes["meter_unit"]
        == "MWh"
    )
    registry = er.async_get(hass)
    information = registry.async_get(identifier)
    assert information.entity_category.value == "diagnostic"
    assert information.device_id == registry.async_get(entity_id(hass, "101")).device_id
    meter = entry.runtime_data.data["101"]
    updated = {
        **entry.runtime_data.data,
        "101": replace(
            meter,
            details={**meter.details, "flat_number": 8, "rssi": "-80"},
        ),
    }
    with patch.object(
        entry.runtime_data.client, "async_get_meters", return_value=updated
    ):
        await entry.runtime_data.async_refresh()
    assert hass.states.get(identifier).attributes["flat_number"] == 8
    assert hass.states.get(identifier).attributes["rssi"] == "-80"
    assert hass.states.get(entity_id(hass, "101")).state == "10.1"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entity_id(hass, "101", "information") == identifier
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 6


async def test_hourly_poll_and_unload(
    hass: HomeAssistant, responses: AiohttpClientMocker, discovery: dict
) -> None:
    """One shared household poll updates all sensors and stops on unload."""
    entry = await setup(hass, responses, ["101", "102", "103"])
    meters = entry.runtime_data.data.copy()
    meters["101"] = Meter(
        "101",
        "TEST-101",
        "cold",
        "Test manufacturer",
        Reading(11.25, datetime.fromisoformat("2026-11-07T03:00:00+01:00")),
    )
    with patch.object(
        entry.runtime_data.client, "async_get_meters", return_value=meters
    ) as poll:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=1, seconds=2))
        await hass.async_block_till_done()
        poll.assert_awaited_once()
        assert hass.states.get(entity_id(hass, "101")).state == "11.25"
        await hass.config_entries.async_unload(entry.entry_id)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=3))
        await hass.async_block_till_done()
        poll.assert_awaited_once()


async def test_failures_and_recovery(
    hass: HomeAssistant, responses: AiohttpClientMocker, discovery: dict
) -> None:
    """Never publish zero for outages; out-of-order data affects only one meter."""
    entry = await setup(hass, responses, ["101", "103"])
    coordinator = entry.runtime_data
    original = coordinator.data.copy()
    with patch.object(coordinator.client, "async_get_meters") as poll:
        poll.side_effect = PortalConnectionError("offline")
        await coordinator.async_refresh()
        assert hass.states.get(entity_id(hass, "101")).state == STATE_UNAVAILABLE
        assert hass.states.get(entity_id(hass, "103")).state == STATE_UNAVAILABLE
        poll.side_effect = None
        poll.return_value = original
        await coordinator.async_refresh()
        assert hass.states.get(entity_id(hass, "101")).state == "10.1"

        invalid = original.copy()
        invalid["101"] = Meter(
            "101",
            "TEST-101",
            "cold",
            "Test manufacturer",
            Reading(9, datetime.fromisoformat("2026-11-05T03:00:00+01:00")),
        )
        poll.return_value = invalid
        await coordinator.async_refresh()
        assert hass.states.get(entity_id(hass, "101")).state == STATE_UNAVAILABLE
        assert hass.states.get(entity_id(hass, "103")).state == "10.3"

        poll.return_value = original
        await coordinator.async_refresh()
        assert hass.states.get(entity_id(hass, "101")).state == "10.1"


async def test_correction_is_not_reset(
    hass: HomeAssistant, responses: AiohttpClientMocker, discovery: dict
) -> None:
    """Accept a newer lower register without total_increasing's reset heuristic."""
    entry = await setup(hass, responses, ["101"])
    corrected = {
        "101": Meter(
            "101",
            "TEST-101",
            "cold",
            "Test manufacturer",
            Reading(10.0, datetime.fromisoformat("2026-11-07T03:00:00+01:00")),
        )
    }
    with patch.object(
        entry.runtime_data.client, "async_get_meters", return_value=corrected
    ):
        await entry.runtime_data.async_refresh()
    state = hass.states.get(entity_id(hass, "101"))
    assert state.state == "10.0"
    assert state.attributes["state_class"] == "total"
    assert "last_reset" not in state.attributes


async def test_missing_and_reappearing_meter(
    hass: HomeAssistant, responses: AiohttpClientMocker, discovery: dict
) -> None:
    """Keep disappeared devices and recover sensor values without duplicates."""
    entry = await setup(hass, responses, ["101"])
    original = entry.runtime_data.data.copy()
    identifier = entity_id(hass, "101")
    with patch.object(entry.runtime_data.client, "async_get_meters") as poll:
        poll.return_value = {}
        await entry.runtime_data.async_refresh()
        assert hass.states.get(identifier).state == STATE_UNAVAILABLE
        assert (
            hass.states.get(entity_id(hass, "101", "information")).state
            == STATE_UNAVAILABLE
        )
        poll.return_value = original
        await entry.runtime_data.async_refresh()
        assert hass.states.get(identifier).state == "10.1"
    assert (
        len(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)) == 3
    )


async def test_meter_first_appears_after_setup(
    hass: HomeAssistant,
    responses: AiohttpClientMocker,
) -> None:
    """Create fully named devices and sensors when a missing meter reappears."""
    with patch(
        "custom_components.odectyvodomeru.api.PortalClient.async_get_meters",
        return_value={},
    ):
        entry = await setup(hass, responses, ["101"])
    assert not er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    meters = {
        "101": Meter(
            "101",
            "TEST-101",
            "cold",
            "Test manufacturer",
            Reading(10.1, datetime.fromisoformat("2026-11-06T03:00:00+01:00")),
        )
    }
    with patch.object(
        entry.runtime_data.client, "async_get_meters", return_value=meters
    ):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
    sensor = er.async_get(hass).async_get(entity_id(hass, "101"))
    device = dr.async_get(hass).async_get(sensor.device_id)
    assert device.name == "Cold water TEST-101"
    assert device.model == "Cold water"
    assert hass.states.get(sensor.entity_id).state == "10.1"
    assert (
        len(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)) == 3
    )


async def test_deselection_removes_sensors(
    hass: HomeAssistant,
    responses: AiohttpClientMocker,
    discovery: dict,
) -> None:
    """Selection changes remove the device and all associated sensor entries."""
    entry = await setup(hass, responses, ["101", "103"])
    removed = [
        entity_id(hass, "101", suffix)
        for suffix in ("reading", "last_reading", "information")
    ]
    retained = entity_id(hass, "103")
    hass.config_entries.async_update_entry(entry, options={CONF_METERS: ["103"]})
    await hass.async_block_till_done()
    for identifier in removed:
        assert er.async_get(hass).async_get(identifier) is None
        assert hass.states.get(identifier) is None
    assert hass.states.get(retained).state == "10.3"


async def test_bad_reading_is_isolated(
    hass: HomeAssistant,
    responses: AiohttpClientMocker,
    discovery: dict,
) -> None:
    """Malformed source data or a timestamp conflict affects only its own meter."""
    entry = await setup(hass, responses, ["101", "103"])
    discovery["structures"][0]["structure"]["devices"][0]["lastState"] = {
        "value": "invalid",
        "lastMeasurement": "2026-11-06T03:00:00+01:00",
    }
    responses.clear_requests()
    responses.get(
        f"{API_BASE_URL}/api/structure/user-buildings",
        json={"buildings": [{"structure": {"id": 10}, "accessRestricted": False}]},
    )
    responses.get(f"{API_BASE_URL}/api/structure/sub-structures/10", json=discovery)
    await entry.runtime_data.async_refresh()
    assert hass.states.get(entity_id(hass, "101")).state == STATE_UNAVAILABLE
    assert hass.states.get(entity_id(hass, "101", "information")).state == "TEST-101"
    assert hass.states.get(entity_id(hass, "103")).state == "10.3"
    conflicting = entry.runtime_data.data.copy()
    conflicting["101"] = Meter(
        "101",
        "TEST-101",
        "cold",
        "Test manufacturer",
        Reading(99, datetime.fromisoformat("2026-11-06T03:00:00+01:00")),
    )
    with patch.object(
        entry.runtime_data.client, "async_get_meters", return_value=conflicting
    ):
        await entry.runtime_data.async_refresh()
    assert hass.states.get(entity_id(hass, "101")).state == STATE_UNAVAILABLE
    assert "Conflicting" in entry.runtime_data.data["101"].reading_error
    assert hass.states.get(entity_id(hass, "103")).state == "10.3"


@pytest.mark.parametrize("account_id", ["42", "99"])
async def test_expired_token(hass: HomeAssistant, account_id: str) -> None:
    """Bound relogin to one attempt and verify the returned account before retry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        data=CREDENTIALS,
        options={CONF_METERS: ["101"]},
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_meters.side_effect = [PortalAuthenticationError("expired"), {}]
    client.async_authenticate.return_value = account_id
    coordinator = MeterCoordinator(hass, entry, client)
    if account_id == "42":
        assert await coordinator._async_update_data() == {}
        assert client.async_get_meters.await_count == 2
    else:
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()
        assert client.async_get_meters.await_count == 1
    client.async_authenticate.assert_awaited_once_with(
        CREDENTIALS["username"], CREDENTIALS["password"]
    )


@pytest.mark.parametrize("login_fails", [False, True])
async def test_expired_token_retry_is_bounded(
    hass: HomeAssistant,
    login_fails: bool,
) -> None:
    """Repeated rejection triggers reauthentication, not an infinite retry."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="42", data=CREDENTIALS, options={CONF_METERS: ["101"]}
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_meters.side_effect = PortalAuthenticationError("expired")
    client.async_authenticate.return_value = "42"
    if login_fails:
        client.async_authenticate.side_effect = PortalAuthenticationError("rejected")
    with pytest.raises(ConfigEntryAuthFailed):
        await MeterCoordinator(hass, entry, client)._async_update_data()
    client.async_authenticate.assert_awaited_once()
    assert client.async_get_meters.await_count == (1 if login_fails else 2)


async def test_access_denied_does_not_retry(hass: HomeAssistant) -> None:
    """A restricted account must prompt reauthentication without repeated login."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="42", data=CREDENTIALS, options={CONF_METERS: ["101"]}
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_meters.side_effect = PortalAccessRestrictedError("restricted")
    with pytest.raises(ConfigEntryAuthFailed):
        await MeterCoordinator(hass, entry, client)._async_update_data()
    client.async_authenticate.assert_not_awaited()


@pytest.mark.parametrize(
    "state",
    [
        [],
        {"value": True},
        {"value": -1},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": "1.5"},
        {"value": 1},
        {"value": 1, "lastMeasurement": "invalid"},
        {"value": 1, "lastMeasurement": "2026-11-06T03:00:00"},
    ],
)
def test_invalid_reading(state: object) -> None:
    """Validate numeric registers and require explicit timezone-aware timestamps."""
    with pytest.raises(PortalResponseError):
        _reading({"lastState": state, "measurementTypeString": "cold"})


@pytest.mark.parametrize(("kind", "unit"), [("cold", "litres"), ("cal", "kWh")])
def test_unexpected_units(kind: str, unit: str) -> None:
    """Do not silently label unexpected API units as m3 or MWh."""
    with pytest.raises(PortalResponseError):
        _reading(
            {
                "measurementTypeString": kind,
                "units": unit,
                "lastState": {
                    "value": 1,
                    "lastMeasurement": "2026-11-06T03:00:00+01:00",
                },
            }
        )


def test_zero_is_valid() -> None:
    """A genuine zero register is distinct from a missing reading."""
    assert (
        _reading(
            {
                "measurementTypeString": "cold",
                "units": None,
                "lastState": {
                    "value": 0,
                    "lastMeasurement": "2026-11-06T03:00:00+01:00",
                },
            }
        ).value
        == 0
    )


@pytest.mark.parametrize(
    ("meter_id", "measurement_type", "initial"),
    [("101", "cold", 10.1), ("103", "cal", 10.3)],
)
async def test_long_term_statistics(
    recorder_mock,
    hass: HomeAssistant,
    responses: AiohttpClientMocker,
    discovery: dict,
    freezer,
    meter_id: str,
    measurement_type: str,
    initial: float,
) -> None:
    """Compile real Recorder sums and retrieve day, month, and year aggregates."""
    from homeassistant.components.recorder.tasks import StatisticsTask
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
        statistics_during_period,
    )

    start = (dt_util.utcnow() + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    freezer.move_to(start)
    entry = await setup(hass, responses, [meter_id])
    identifier = entity_id(hass, meter_id)
    await async_wait_recording_done(hass)
    freezer.move_to(start + timedelta(minutes=5))
    recorder_mock.queue_task(StatisticsTask(start, False))
    await async_wait_recording_done(hass)

    freezer.move_to(start + timedelta(minutes=55))
    updated = {
        meter_id: Meter(
            meter_id,
            f"TEST-{meter_id}",
            measurement_type,
            "Test manufacturer",
            Reading(initial + 1.15, start + timedelta(minutes=55)),
        )
    }
    with patch.object(
        entry.runtime_data.client, "async_get_meters", return_value=updated
    ):
        await entry.runtime_data.async_refresh()
    await async_wait_recording_done(hass)
    freezer.move_to(start + timedelta(hours=1))
    recorder_mock.queue_task(StatisticsTask(start + timedelta(minutes=55), False))
    await async_wait_recording_done(hass)
    for period in ("day", "month", "year"):
        statistics = await hass.async_add_executor_job(
            statistics_during_period,
            hass,
            start,
            start + timedelta(days=1),
            {identifier},
            period,
        )
        assert statistics[identifier][-1]["sum"] == pytest.approx(1.15)
