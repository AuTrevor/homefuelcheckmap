"""Price sensors for NSW Fuel Map: one per station, plus the cheapest in range."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
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
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
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
    """Set up the cheapest-price sensor and a price sensor per station."""
    coordinator = entry.runtime_data
    async_add_entities([NswFuelCheapestSensor(coordinator)])

    known: set[str] = set()

    @callback
    def async_sync_stations() -> None:
        """Add a price sensor for each newly seen station.

        Mirrors the geo_location platform: stations that leave the radius keep
        their sensor and report unavailable, so history isn't orphaned when a
        station on the edge of the radius flickers in and out.
        """
        new = set(coordinator.data or {}) - known
        if not new:
            return
        known.update(new)
        async_add_entities(
            NswFuelStationPriceSensor(coordinator, code) for code in sorted(new)
        )

    async_sync_stations()
    entry.async_on_unload(coordinator.async_add_listener(async_sync_stations))


class NswFuelStationPriceSensor(CoordinatorEntity[NswFuelCoordinator], SensorEntity):
    """Current price at one station. The state is the price, so history is price.

    The map marker for the same station is a geo_location entity, whose state is
    fixed by Home Assistant to distance from home; plotting price over time needs
    a sensor whose state is the price itself.
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:gas-station"

    def __init__(self, coordinator: NswFuelCoordinator, station_code: str) -> None:
        super().__init__(coordinator)
        self._station_code = station_code
        entry = coordinator.config_entry
        label = FUEL_TYPES.get(coordinator.fuel_type, coordinator.fuel_type)
        # Named from the station, not the price, so the entity_id stays stable as
        # prices move.
        station = (coordinator.data or {}).get(station_code)
        self._attr_name = station.name if station else station_code
        self._attr_unique_id = f"{entry.entry_id}_price_{station_code}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"NSW Fuel Map ({label})",
            manufacturer="NSW Government FuelCheck",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _station(self) -> Station | None:
        return (self.coordinator.data or {}).get(self._station_code)

    @property
    def available(self) -> bool:
        """Unavailable once the station leaves the radius or drops its price."""
        station = self._station
        return super().available and station is not None and station.price is not None

    @property
    def native_value(self) -> float | None:
        station = self._station
        return station.price if station else None

    @property
    def native_unit_of_measurement(self) -> str:
        station = self._station
        return station.price_unit if station else DEFAULT_PRICE_UNIT

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        station = self._station
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
            # Lets these sensors be dropped straight onto a map card's `entities`
            # list, as an alternative to the geo_location markers.
            ATTR_LATITUDE: station.latitude,
            ATTR_LONGITUDE: station.longitude,
        }


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
