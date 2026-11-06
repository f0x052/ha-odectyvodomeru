"""Cumulative readings, source timestamps, and meter information."""

from datetime import datetime
from typing import Literal

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Meter
from .const import DOMAIN
from .coordinator import MeterConfigEntry, MeterCoordinator

type SensorKind = Literal["reading", "last_reading", "information"]
SENSOR_KINDS: tuple[SensorKind, ...] = ("reading", "last_reading", "information")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MeterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add entities for selected meters, including meters that reappear later."""
    coordinator = entry.runtime_data
    translations = await async_get_translations(
        hass, hass.config.language, "selector", {DOMAIN}
    )
    added: set[str] = set()

    @callback
    def add_meters() -> None:
        entities: list[MeterSensor] = []
        for meter_id, meter in coordinator.data.items():
            if meter_id not in added:
                meter_type = translations[
                    f"component.{DOMAIN}.selector.meter_type.options.{meter.measurement_type}"
                ]
                entities.extend(
                    [
                        MeterSensor(coordinator, meter, meter_type, kind=kind)
                        for kind in SENSOR_KINDS
                    ]
                )
                added.add(meter_id)
        async_add_entities(entities)

    add_meters()
    entry.async_on_unload(coordinator.async_add_listener(add_meters))


class MeterSensor(CoordinatorEntity[MeterCoordinator], SensorEntity):
    """A meter's cumulative reading, source timestamp, or diagnostic details."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MeterCoordinator,
        meter: Meter,
        meter_type: str,
        *,
        kind: SensorKind,
    ) -> None:
        """Attach stable sensor IDs to the already registered meter device."""
        super().__init__(coordinator)
        self._meter_id = meter.id
        self._kind = kind
        identity = f"{coordinator.config_entry.unique_id}_{meter.id}"
        self._attr_unique_id = f"{identity}_{kind}"
        self._attr_translation_key = kind
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identity)},
            manufacturer=meter.manufacturer,
            model=meter_type,
            name=f"{meter_type} {meter.serial_number}",
            serial_number=meter.serial_number,
            configuration_url="https://odectyvodomeru.cz",
        )
        if kind == "last_reading":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        elif kind == "information":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        else:
            self._attr_device_class = (
                SensorDeviceClass.ENERGY
                if meter.measurement_type == "cal"
                else SensorDeviceClass.WATER
            )
            self._attr_native_unit_of_measurement = (
                UnitOfEnergy.MEGA_WATT_HOUR
                if meter.measurement_type == "cal"
                else UnitOfVolume.CUBIC_METERS
            )
            # Corrections/backflow are not resets; replacement meters have new IDs.
            self._attr_state_class = SensorStateClass.TOTAL
            self._attr_suggested_display_precision = 3

    @property
    def available(self) -> bool:
        """A missing or invalid reading is unavailable, never zero."""
        meter = self.coordinator.data.get(self._meter_id)
        return (
            super().available
            and meter is not None
            and (self._kind == "information" or meter.reading is not None)
        )

    @property
    def native_value(self) -> float | datetime | str | None:
        """Return the register, source timestamp, or identifying serial number."""
        meter = self.coordinator.data.get(self._meter_id)
        if meter is None:
            return None
        if self._kind == "information":
            return meter.serial_number
        if meter.reading is None:
            return None
        return (
            meter.reading.timestamp
            if self._kind == "last_reading"
            else meter.reading.value
        )

    @property
    def extra_state_attributes(self) -> dict[str, str | int] | None:
        """Show location and portal details on the diagnostic entity only."""
        if self._kind != "information":
            return None
        meter = self.coordinator.data.get(self._meter_id)
        if meter is None:
            return None
        return {
            "portal_meter_id": meter.id,
            "meter_type": meter.measurement_type,
            "meter_unit": "MWh" if meter.measurement_type == "cal" else "m³",
            **meter.details,
        }
