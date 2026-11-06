"""Tests for sanitized household and meter discovery."""

import asyncio
from copy import deepcopy

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.odectyvodomeru.api import (
    Meter,
    PortalAuthenticationError,
    PortalClient,
    PortalResponseError,
)
from custom_components.odectyvodomeru.const import API_BASE_URL


def register_discovery(
    responses: AiohttpClientMocker, payload: dict, buildings: dict | None = None
) -> None:
    """Register synthetic authentication and discovery responses."""
    responses.clear_requests()
    responses.post(f"{API_BASE_URL}/api/login_check", json={"token": "synthetic-token"})
    responses.get(f"{API_BASE_URL}/api/user/profile", json={"user": {"id": 42}})
    responses.get(
        f"{API_BASE_URL}/api/structure/user-buildings",
        json=(
            buildings
            if buildings is not None
            else {"buildings": [{"structure": {"id": 10}}]}
        ),
    )
    responses.get(f"{API_BASE_URL}/api/structure/sub-structures/10", json=payload)


async def test_discovery(responses: AiohttpClientMocker, discovery: dict) -> None:
    """Discover the three supported meter types using metadata only."""
    register_discovery(responses, discovery)
    async with responses.create_session(asyncio.get_running_loop()) as session:
        client = PortalClient(session)
        await client.async_authenticate("resident", "synthetic-password")
        meters = await client.async_get_meters()
    assert meters == {
        str(index): Meter(str(index), f"TEST-{index}", kind, "Test manufacturer")
        for index, kind in enumerate(("cold", "hot", "cal"), start=101)
    }
    assert len(responses.mock_calls) == 4
    assert not any("/measurements/" in str(call[1]) for call in responses.mock_calls)


async def test_meter_details(responses: AiohttpClientMocker, discovery: dict) -> None:
    """Keep location fields scoped to each household without account permissions."""
    first = discovery["structures"][0]["structure"]
    second = deepcopy(first)
    second.update(id=30, flatNumber="B-12", floorNumber=-1, customName="")
    second["devices"] = [deepcopy(first["devices"][0])]
    second["devices"][0].update(id=201, deviceId="TEST-201")
    discovery["structures"].append({"structure": second, "canEditAccesses": True})
    register_discovery(
        responses,
        discovery,
        {"buildings": [{"structure": {"id": 10, "customName": "Example building"}}]},
    )
    async with responses.create_session(asyncio.get_running_loop()) as session:
        client = PortalClient(session)
        await client.async_authenticate("resident", "synthetic-password")
        meters = await client.async_get_meters()
    assert meters["101"].details == {
        "building_id": "10",
        "household_id": "20",
        "address": "Example Street 12, 12345 Test City",
        "flat_number": 7,
        "floor_number": 0,
        "building_name": "Example building",
        "household_name": "Example flat",
        "description": "Synthetic household",
        "rssi": "-71",
    }
    assert meters["201"].details["flat_number"] == "B-12"
    assert meters["201"].details["floor_number"] == -1
    assert meters["201"].details["household_id"] == "30"
    assert "household_name" not in meters["201"].details
    assert "canEditAccesses" not in meters["201"].details


@pytest.mark.parametrize("address", [None, {"addressLine": "Example Street 12"}])
async def test_building_address_fallback(
    responses: AiohttpClientMocker,
    discovery: dict,
    address: dict | None,
) -> None:
    """Use the building's address if the household has no address of its own."""
    discovery["address"] = address
    register_discovery(
        responses,
        discovery,
        {
            "buildings": [
                {
                    "structure": {
                        "id": 10,
                        "address": {
                            "addressLine": "Example Street 12",
                            "cityLine": "12345 Test City",
                        },
                    }
                }
            ]
        },
    )
    async with responses.create_session(asyncio.get_running_loop()) as session:
        client = PortalClient(session)
        await client.async_authenticate("resident", "synthetic-password")
        meter = (await client.async_get_meters())["101"]
    assert meter.details["address"] == "Example Street 12, 12345 Test City"


async def test_missing_optional_details(
    responses: AiohttpClientMocker,
    discovery: dict,
) -> None:
    """Absent location fields must not prevent existing meters from working."""
    discovery.pop("address")
    flat = discovery["structures"][0]["structure"]
    for key in ("id", "flatNumber", "floorNumber", "customName", "description"):
        flat.pop(key)
    for device in flat["devices"]:
        device["lastState"].pop("data")
    register_discovery(responses, discovery)
    async with responses.create_session(asyncio.get_running_loop()) as session:
        client = PortalClient(session)
        await client.async_authenticate("resident", "synthetic-password")
        meters = await client.async_get_meters()
    assert meters["101"].details == {"building_id": "10"}
    assert meters["101"].reading.value == 10.1


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("household", "address", []),
        ("flat", "id", "invalid"),
        ("flat", "flatNumber", True),
        ("flat", "floorNumber", 1.5),
        ("flat", "description", {}),
    ],
)
async def test_invalid_details(
    responses: AiohttpClientMocker,
    discovery: dict,
    section: str,
    key: str,
    value: object,
) -> None:
    """Report changed schemas explicitly rather than displaying object strings."""
    source = (
        discovery if section == "household" else discovery["structures"][0]["structure"]
    )
    source[key] = value
    register_discovery(responses, discovery)
    async with responses.create_session(asyncio.get_running_loop()) as session:
        client = PortalClient(session)
        await client.async_authenticate("resident", "synthetic-password")
        with pytest.raises(PortalResponseError):
            await client.async_get_meters()


async def test_skip_inactive_unsupported_and_former(
    responses: AiohttpClientMocker, discovery: dict
) -> None:
    """Former, removed, and unrelated meters must not appear in the selector."""
    flat = discovery["structures"][0]["structure"]
    removed = deepcopy(flat["devices"][0])
    removed.update(id=201, deinstalled="2026-01-01")
    unsupported = deepcopy(flat["devices"][0])
    unsupported.update(id=202, measurementTypeString="itn")
    flat["devices"].extend([removed, unsupported])
    flat["formerDevices"] = [removed]
    register_discovery(responses, discovery)
    async with responses.create_session(asyncio.get_running_loop()) as session:
        client = PortalClient(session)
        await client.async_authenticate("resident", "synthetic-password")
        assert list(await client.async_get_meters()) == ["101", "102", "103"]


async def test_restricted_building(responses: AiohttpClientMocker) -> None:
    """Do not request structures for an account-restricted building."""
    register_discovery(
        responses,
        {},
        {"buildings": [{"structure": {"id": 10}, "accessRestricted": True}]},
    )
    async with responses.create_session(asyncio.get_running_loop()) as session:
        client = PortalClient(session)
        await client.async_authenticate("resident", "synthetic-password")
        assert await client.async_get_meters() == {}
    assert len(responses.mock_calls) == 3


@pytest.mark.parametrize(
    "buildings",
    [
        {},
        {"buildings": None},
        {"buildings": {}},
        {"buildings": [None]},
        {"buildings": [{"structure": None}]},
        {"buildings": [{"structure": {"id": True}}]},
        {"buildings": [{"structure": {"id": "../other"}}]},
    ],
)
async def test_invalid_buildings(
    responses: AiohttpClientMocker, buildings: dict
) -> None:
    """Reject malformed discovery without constructing invalid request paths."""
    register_discovery(responses, {}, buildings)
    async with responses.create_session(asyncio.get_running_loop()) as session:
        client = PortalClient(session)
        await client.async_authenticate("resident", "synthetic-password")
        with pytest.raises(PortalResponseError):
            await client.async_get_meters()
    assert len(responses.mock_calls) == 3


@pytest.mark.parametrize(
    "household",
    [
        {},
        {"structures": None},
        {"structures": [None]},
        {"structures": [{"structure": None}]},
        {"structures": [{"structure": {"devices": None}}]},
        {"structures": [{"structure": {"devices": [None]}}]},
    ],
)
async def test_invalid_household(
    responses: AiohttpClientMocker, household: dict
) -> None:
    """Unexpected structures are errors, not an apparently empty account."""
    register_discovery(responses, household)
    async with responses.create_session(asyncio.get_running_loop()) as session:
        client = PortalClient(session)
        await client.async_authenticate("resident", "synthetic-password")
        with pytest.raises(PortalResponseError):
            await client.async_get_meters()


@pytest.mark.parametrize(
    "update",
    [
        {"id": 0},
        {"id": False},
        {"id": "101"},
        {"deviceId": None},
        {"deviceId": " "},
        {"manufacturer": None},
        {"manufacturer": {"name": None}},
    ],
)
async def test_invalid_meter(
    responses: AiohttpClientMocker, discovery: dict, update: dict
) -> None:
    """Do not silently omit malformed supported meters."""
    discovery["structures"][0]["structure"]["devices"][0].update(update)
    register_discovery(responses, discovery)
    async with responses.create_session(asyncio.get_running_loop()) as session:
        client = PortalClient(session)
        await client.async_authenticate("resident", "synthetic-password")
        with pytest.raises(PortalResponseError):
            await client.async_get_meters()


@pytest.mark.parametrize("conflict", [False, True])
async def test_duplicate_ids(
    responses: AiohttpClientMocker, discovery: dict, conflict: bool
) -> None:
    """Deduplicate identical metadata but reject conflicting physical identities."""
    devices = discovery["structures"][0]["structure"]["devices"]
    duplicate = deepcopy(devices[0])
    if conflict:
        duplicate["deviceId"] = "DIFFERENT-SERIAL"
    devices.append(duplicate)
    register_discovery(responses, discovery)
    async with responses.create_session(asyncio.get_running_loop()) as session:
        client = PortalClient(session)
        await client.async_authenticate("resident", "synthetic-password")
        if conflict:
            with pytest.raises(PortalResponseError):
                await client.async_get_meters()
        else:
            assert len(await client.async_get_meters()) == 3


async def test_discovery_requires_login(responses: AiohttpClientMocker) -> None:
    """Do not attempt unauthenticated discovery."""
    async with responses.create_session(asyncio.get_running_loop()) as session:
        with pytest.raises(PortalAuthenticationError):
            await PortalClient(session).async_get_meters()
    assert responses.call_count == 0
