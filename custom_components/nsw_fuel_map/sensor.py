"""Cheapest-nearby price sensor for NSW Fuel Map."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Station
from .const import (
    ATTR_ADDRESS,
    ATTR_BRAND,
    ATTR_DISTANCE,
    ATTR_FUEL_TYPE,
    ATTR_LAST_UPDATED,
    ATTR_STATION_CODE,
    ATTR_STATION_NAME,
    DEFAULT_PRICE_UNIT,
    DOMAIN,
    FUEL_TYPES,
)
from .coordinator import NswFuelConfigEntry, NswFuelCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NswFuelConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the cheapest-price sensor."""
    async_add_entities([NswFuelCheapestSensor(entry.runtime_data)])


class NswFuelCheapestSensor(CoordinatorEntity[NswFuelCoordinator], SensorEntity):
    """Lowest price for the configured fuel type within the search radius."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:gas-station"

    def __init__(self, coordinator: NswFuelCoordinator) -> None:
        super().__init__(coordinator)
        fuel_type = coordinator.fuel_type
        label = FUEL_TYPES.get(fuel_type, fuel_type)
        self._attr_name = f"Cheapest {label}"
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_cheapest"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
            name=f"NSW Fuel Map ({label})",
            manufacturer="NSW Government FuelCheck",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _cheapest(self) -> Station | None:
        """The lowest-priced station in range, or None if nothing has a price."""
        priced = [s for s in (self.coordinator.data or {}).values() if s.price is not None]
        if not priced:
            return None
        return min(priced, key=lambda s: s.price)

    @property
    def available(self) -> bool:
        return super().available and self._cheapest is not None

    @property
    def native_value(self) -> float | None:
        station = self._cheapest
        return station.price if station else None

    @property
    def native_unit_of_measurement(self) -> str:
        station = self._cheapest
        return station.price_unit if station else DEFAULT_PRICE_UNIT

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        station = self._cheapest
        if station is None:
            return {}
        fuel_type = self.coordinator.fuel_type
        return {
            ATTR_STATION_CODE: station.code,
            ATTR_STATION_NAME: station.name,
            ATTR_BRAND: station.brand,
            ATTR_ADDRESS: station.address,
            ATTR_DISTANCE: station.distance,
            ATTR_FUEL_TYPE: FUEL_TYPES.get(fuel_type, fuel_type),
            ATTR_LAST_UPDATED: station.last_updated,
            "stations_in_range": len(self.coordinator.data or {}),
        }
