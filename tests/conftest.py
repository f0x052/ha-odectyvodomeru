"""Fixtures for the custom integration."""

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.odectyvodomeru.const import API_BASE_URL

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load the integration from this repository."""


@pytest.fixture
def responses(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Prevent real API calls and provide synthetic responses."""
    return aioclient_mock


@pytest.fixture
def discovery(responses: AiohttpClientMocker) -> dict:
    """Provide synthetic household meters without copying private HAR data."""
    devices = [
        {
            "id": index,
            "deviceId": f"TEST-{index}",
            "measurementTypeString": kind,
            "deinstalled": None,
            "manufacturer": {"name": "Test manufacturer"},
            "lastState": {
                "value": index / 10,
                "lastMeasurement": "2026-11-06T03:00:00+01:00",
                "data": {"rssi": "-71"},
            },
        }
        for index, kind in enumerate(("cold", "hot", "cal"), start=101)
    ]
    payload = {
        "address": {"addressLine": "Example Street 12", "cityLine": "12345 Test City"},
        "structures": [
            {
                "structure": {
                    "id": 20,
                    "flatNumber": 7,
                    "floorNumber": 0,
                    "customName": "Example flat",
                    "description": "Synthetic household",
                    "devices": devices,
                },
            }
        ],
    }
    responses.get(
        f"{API_BASE_URL}/api/structure/user-buildings",
        json={"buildings": [{"structure": {"id": 10}, "accessRestricted": False}]},
    )
    responses.get(f"{API_BASE_URL}/api/structure/sub-structures/10", json=payload)
    return payload
