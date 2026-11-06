"""The Odecty vodomeru integration."""

import logging

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.translation import async_get_translations

from .api import (
    PortalAuthenticationError,
    PortalClient,
    PortalConnectionError,
    PortalResponseError,
)
from .const import CONF_METERS, DOMAIN, NAME
from .coordinator import MeterConfigEntry, MeterCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]
type OdectyVodomeruConfigEntry = MeterConfigEntry


async def async_setup_entry(
    hass: HomeAssistant, entry: OdectyVodomeruConfigEntry
) -> bool:
    """Authenticate and register only the explicitly selected meters."""
    client = PortalClient(async_get_clientsession(hass))
    try:
        account_id = await client.async_authenticate(
            entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
        )
    except PortalAuthenticationError as err:
        raise ConfigEntryAuthFailed("Portal login was rejected") from err
    except (PortalConnectionError, PortalResponseError) as err:
        raise ConfigEntryNotReady(str(err)) from err

    if account_id != entry.unique_id:
        raise ConfigEntryAuthFailed("The portal returned a different account")

    if entry.title == NAME:
        hass.config_entries.async_update_entry(
            entry, title=f"{NAME} ({entry.data[CONF_USERNAME].strip()})"
        )

    selected = set(entry.options.get(CONF_METERS, []))
    coordinator = MeterCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    meters = coordinator.data

    translations = await async_get_translations(
        hass, hass.config.language, "selector", {DOMAIN}
    )
    registry = dr.async_get(hass)
    selected_identifiers = {
        (DOMAIN, f"{account_id}_{meter_id}") for meter_id in selected
    }
    for meter_id in selected:
        if (meter := meters.get(meter_id)) is None:
            _LOGGER.warning(
                "A selected meter is no longer returned by the portal; "
                "its existing device is retained. Review the selection in Configure"
            )
            continue
        meter_type = translations[
            f"component.{DOMAIN}.selector.meter_type.options.{meter.measurement_type}"
        ]
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{account_id}_{meter.id}")},
            manufacturer=meter.manufacturer,
            model=meter_type,
            name=f"{meter_type} {meter.serial_number}",
            serial_number=meter.serial_number,
            configuration_url="https://odectyvodomeru.cz",
        )

    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not device.identifiers.intersection(selected_identifiers):
            registry.async_remove_device(device.id)

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    return True


async def _async_update_options(
    hass: HomeAssistant, entry: OdectyVodomeruConfigEntry
) -> None:
    """Apply a changed meter selection immediately."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: OdectyVodomeruConfigEntry
) -> bool:
    """Unload the entry; the HTTP session is owned by Home Assistant."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
