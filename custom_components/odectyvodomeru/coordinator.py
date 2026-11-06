"""Shared hourly polling for the selected meters."""

import logging
from dataclasses import replace
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    Meter,
    PortalAccessRestrictedError,
    PortalAuthenticationError,
    PortalClient,
    PortalError,
    Reading,
)
from .const import CONF_METERS, DOMAIN

_LOGGER = logging.getLogger(__name__)

type MeterConfigEntry = ConfigEntry[MeterCoordinator]


class MeterCoordinator(DataUpdateCoordinator[dict[str, Meter]]):
    """Fetch one household snapshot for all sensors, not per entity."""

    def __init__(
        self, hass: HomeAssistant, entry: MeterConfigEntry, client: PortalClient
    ) -> None:
        """Initialize polling and per-meter validation state."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(hours=1),
        )
        self.client = client
        self.selected = set(entry.options.get(CONF_METERS, []))
        self._last_readings: dict[str, Reading] = {}
        self._problems: dict[str, str] = {}

    async def _async_update_data(self) -> dict[str, Meter]:
        """Retry expired authentication once, and isolate invalid meter readings."""
        if not self.selected:
            return {}
        try:
            try:
                meters = await self.client.async_get_meters()
            except PortalAccessRestrictedError:
                raise
            except PortalAuthenticationError:
                account_id = await self.client.async_authenticate(
                    self.config_entry.data[CONF_USERNAME],
                    self.config_entry.data[CONF_PASSWORD],
                )
                if account_id != self.config_entry.unique_id:
                    raise PortalAuthenticationError(
                        "The portal returned another account"
                    )
                meters = await self.client.async_get_meters()
        except PortalAuthenticationError as err:
            raise ConfigEntryAuthFailed("Portal authentication was rejected") from err
        except PortalError as err:
            raise UpdateFailed(str(err)) from err

        result: dict[str, Meter] = {}
        for meter_id in self.selected:
            meter = meters.get(meter_id)
            problem = None
            if meter is None:
                problem = "Selected meter is no longer returned by the portal"
            elif meter.reading_error:
                problem = meter.reading_error
            elif meter.reading is None:
                problem = "Selected meter has no reading"
            elif (previous := self._last_readings.get(meter_id)) is not None:
                if meter.reading.timestamp < previous.timestamp:
                    problem = "Out-of-order meter reading"
                elif (
                    meter.reading.timestamp == previous.timestamp
                    and meter.reading.value != previous.value
                ):
                    problem = "Conflicting readings for the same source timestamp"

            if problem is not None:
                if self._problems.get(meter_id) != problem:
                    _LOGGER.warning("Selected meter %s: %s", meter_id, problem)
                self._problems[meter_id] = problem
                if meter is not None:
                    result[meter_id] = replace(
                        meter, reading=None, reading_error=problem
                    )
            else:
                if meter_id in self._problems:
                    _LOGGER.info("Selected meter %s has recovered", meter_id)
                    del self._problems[meter_id]
                if meter is not None and meter.reading is not None:
                    self._last_readings[meter_id] = meter.reading
                    result[meter_id] = meter
        return result
