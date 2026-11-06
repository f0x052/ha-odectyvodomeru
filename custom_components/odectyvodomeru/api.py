"""Asynchronous authentication client for odectyvodomeru.cz."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout, ContentTypeError

from .const import API_BASE_URL, REQUEST_TIMEOUT


class PortalError(Exception):
    """Base error for the portal API."""


class PortalAuthenticationError(PortalError):
    """The portal rejected the credentials."""


class PortalAccessRestrictedError(PortalAuthenticationError):
    """The portal restricted access to the account."""


class PortalConnectionError(PortalError):
    """The portal could not be reached or is temporarily unavailable."""


class PortalRateLimitError(PortalConnectionError):
    """The portal is limiting requests."""


class PortalResponseError(PortalError):
    """The portal returned an unsupported response."""


@dataclass(frozen=True)
class Reading:
    """A cumulative register value and its timezone-aware source timestamp."""

    value: float
    timestamp: datetime


def _reading(device: dict[str, Any]) -> Reading | None:
    """Validate register data without mistaking missing data for zero."""
    state = device.get("lastState")
    if state is None:
        return None
    if not isinstance(state, dict):
        raise PortalResponseError("Invalid meter state")
    value = state.get("value")
    if type(value) not in (int, float) or not isfinite(value) or value < 0:
        raise PortalResponseError("Invalid cumulative reading")
    timestamp = state.get("lastMeasurement")
    if not isinstance(timestamp, str):
        raise PortalResponseError("Invalid reading timestamp")
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError as err:
        raise PortalResponseError("Invalid reading timestamp") from err
    if parsed.utcoffset() is None:
        raise PortalResponseError("Reading timestamp has no timezone")
    unit = device.get("units")
    expected = ("MWh",) if device["measurementTypeString"] == "cal" else ("m3", "m³")
    if unit is not None and unit not in expected:
        raise PortalResponseError("Unsupported meter unit")
    return Reading(float(value), parsed)


@dataclass(frozen=True)
class Meter:
    """Identity and display metadata for an active supported meter."""

    id: str
    serial_number: str
    measurement_type: str
    manufacturer: str
    reading: Reading | None = field(default=None, compare=False)
    reading_error: str | None = field(default=None, compare=False)
    details: Mapping[str, str | int] = field(default_factory=dict, compare=False)


def _optional_text(source: dict[str, Any], key: str) -> str | None:
    """Read optional display text without coercing malformed objects."""
    value = source.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise PortalResponseError("Invalid meter location or details")
    return value.strip() or None


def _meter_details(
    building: dict[str, Any],
    household: dict[str, Any],
    flat: dict[str, Any],
    device: dict[str, Any],
) -> dict[str, str | int]:
    """Keep useful location metadata, not permissions or unrelated account data."""
    details: dict[str, str | int] = {
        "building_id": str(_structure_id(building.get("id"))),
    }
    if flat.get("id") is not None:
        details["household_id"] = str(_structure_id(flat["id"]))
    addresses = []
    for source in (household, building):
        address = source.get("address")
        if address is None:
            continue
        if not isinstance(address, dict):
            raise PortalResponseError("Invalid building address")
        addresses.append(address)
    lines = []
    for key in ("addressLine", "cityLine"):
        for address in addresses:
            if (line := _optional_text(address, key)) is not None:
                lines.append(line)
                break
    if lines:
        details["address"] = ", ".join(lines)
    for api_key, key in (
        ("flatNumber", "flat_number"),
        ("floorNumber", "floor_number"),
    ):
        value = flat.get(api_key)
        if value is not None:
            if type(value) is int:
                details[key] = value
            elif (text := _optional_text(flat, api_key)) is not None:
                details[key] = text
    for source, api_key, key in (
        (building, "customName", "building_name"),
        (flat, "customName", "household_name"),
        (flat, "description", "description"),
    ):
        if (text := _optional_text(source, api_key)) is not None:
            details[key] = text
    state = device.get("lastState")
    if isinstance(state, dict) and isinstance(data := state.get("data"), dict):
        if (rssi := _optional_text(data, "rssi")) is not None:
            details["rssi"] = rssi
    return details


def _objects(value: Any) -> list[dict[str, Any]]:
    """Validate lists before interpreting an undocumented API response."""
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise PortalResponseError("The portal returned an invalid discovery list")
    return value


def _structure_id(value: Any) -> int:
    """Accept only valid identifiers before using them in request paths."""
    if type(value) is not int or value <= 0:
        raise PortalResponseError("The portal returned an invalid identifier")
    return value


class PortalClient:
    """Use Home Assistant's shared session without modifying its headers."""

    def __init__(self, session: ClientSession) -> None:
        """Initialize the client."""
        self._session = session
        self._access_token: str | None = None

    async def async_authenticate(self, username: str, password: str) -> str:
        """Log in and return the stable account ID from the protected profile."""
        self._access_token = None
        if not username.strip() or not password:
            raise PortalAuthenticationError("Username and password are required")

        payload = await self._async_request(
            "POST",
            "/api/login_check",
            data={"username": username.strip(), "password": password},
        )
        token = payload.get("token")
        if not isinstance(token, str) or not token.strip():
            raise PortalResponseError("Login response has no access token")

        self._access_token = token
        profile = await self._async_request("GET", "/api/user/profile")
        user = profile.get("user")
        account_id = user.get("id") if isinstance(user, dict) else None
        if type(account_id) is not int or account_id <= 0:
            raise PortalResponseError("Profile response has no valid account ID")
        return str(account_id)

    async def async_get_meters(self) -> dict[str, Meter]:
        """Discover active water and heat meters visible to this account."""
        if self._access_token is None:
            raise PortalAuthenticationError("Sign in before discovering meters")

        payload = await self._async_request("GET", "/api/structure/user-buildings")
        meters: dict[str, Meter] = {}
        for building in _objects(payload.get("buildings")):
            if building.get("accessRestricted") is True:
                continue
            structure = building.get("structure")
            if not isinstance(structure, dict):
                raise PortalResponseError("The portal returned an invalid building")
            building_id = _structure_id(structure.get("id"))
            household = await self._async_request(
                "GET", f"/api/structure/sub-structures/{building_id}"
            )
            for item in _objects(household.get("structures")):
                flat = item.get("structure")
                if not isinstance(flat, dict):
                    raise PortalResponseError(
                        "The portal returned an invalid household"
                    )
                for device in _objects(flat.get("devices")):
                    if device.get("deinstalled") is not None:
                        continue
                    measurement_type = device.get("measurementTypeString")
                    if measurement_type not in ("cold", "hot", "cal"):
                        continue
                    meter_id = str(_structure_id(device.get("id")))
                    serial = device.get("deviceId")
                    manufacturer = device.get("manufacturer")
                    if not isinstance(serial, str) or not serial.strip():
                        raise PortalResponseError(
                            "The portal returned an invalid meter"
                        )
                    if not isinstance(manufacturer, dict) or not isinstance(
                        manufacturer.get("name"), str
                    ):
                        raise PortalResponseError(
                            "The portal returned an invalid manufacturer"
                        )
                    reading = None
                    reading_error = None
                    try:
                        reading = _reading(device)
                    except PortalResponseError as err:
                        reading_error = str(err)
                    meter = Meter(
                        meter_id,
                        serial,
                        measurement_type,
                        manufacturer["name"],
                        reading,
                        reading_error,
                        _meter_details(structure, household, flat, device),
                    )
                    if meter_id in meters and meters[meter_id] != meter:
                        raise PortalResponseError(
                            "The portal returned conflicting meter identifiers"
                        )
                    meters[meter_id] = meter
        return meters

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Request JSON without including credentials or response bodies in errors."""
        headers = {}
        if self._access_token is not None:
            headers["Authorization"] = f"Bearer {self._access_token}"

        try:
            async with self._session.request(
                method,
                f"{API_BASE_URL}{path}",
                json=data,
                headers=headers,
                timeout=ClientTimeout(total=REQUEST_TIMEOUT),
                allow_redirects=False,
            ) as response:
                if response.status == 401:
                    raise PortalAuthenticationError("Authentication was rejected")
                if response.status == 403:
                    raise PortalAccessRestrictedError("Account access was denied")
                if response.status == 429:
                    raise PortalRateLimitError("Too many requests; try again later")
                if response.status >= 500:
                    raise PortalConnectionError("The portal is temporarily unavailable")
                if response.status != 200:
                    raise PortalResponseError(
                        f"Unexpected HTTP status: {response.status}"
                    )

                try:
                    payload = await response.json()
                except (ValueError, ContentTypeError) as err:
                    raise PortalResponseError(
                        "The portal did not return valid JSON"
                    ) from err
        except (ClientError, TimeoutError) as err:
            raise PortalConnectionError(
                "Unable to communicate with the portal"
            ) from err

        if not isinstance(payload, dict):
            raise PortalResponseError("The portal response is not a JSON object")
        return payload
