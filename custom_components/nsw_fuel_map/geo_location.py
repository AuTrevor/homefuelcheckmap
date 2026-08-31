"""Map markers for nearby NSW fuel stations."""

from __future__ import annotations

import logging

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_ADDRESS,
    ATTR_BRAND,
    ATTR_FUEL_TYPE,
    ATTR_LAST_UPDATED,
    ATTR_PRICE,
    ATTR_STATION_CODE,
    ATTR_STATION_NAME,
    DOMAIN,
    FUEL_TYPES,
)
from .coordinator import NswFuelConfigEntry, NswFuelCoordinator

_LOGGER = logging.getLogger(__name__)

SOURCE = DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NswFuelConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up map markers and keep them in sync with the coordinator."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def async_sync_markers() -> None:
        """Add markers for new stations; existing ones update themselves.

        Stations that drop out of the radius are not removed here — the entity
        reports itself unavailable instead, so a station that flickers in and out
        of range doesn't churn the entity registry.
        """
        current = set(coordinator.data or {})
        new = current - known
        if not new:
            return
        known.update(new)
        async_add_entities(
            NswFuelStationMarker(coordinator, code) for code in sorted(new)
        )

    async_sync_markers()
    entry.async_on_unload(coordinator.async_add_listener(async_sync_markers))


class NswFuelStationMarker(CoordinatorEntity[NswFuelCoordinator], GeolocationEvent):
    """A single fuel station plotted on the map, labelled with its price."""

    _attr_should_poll = False
    _attr_source = SOURCE
    _attr_unit_of_measurement = UnitOfLength.KILOMETERS

    def __init__(self, coordinator: NswFuelCoordinator, station_code: str) -> None:
        super().__init__(coordinator)
        self._station_code = station_code
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{station_code}"

    @property
    def _station(self):
        return (self.coordinator.data or {}).get(self._station_code)

    @property
    def available(self) -> bool:
        """Unavailable once the station leaves the search radius."""
        return super().available and self._station is not None

    @property
    def name(self) -> str | None:
        station = self._station
        if station is None:
            return None
        # Price in the name makes the marker readable directly on the map card.
        if station.price is not None:
            return f"{station.name} {station.price:.1f}"
        return station.name

    @property
    def distance(self) -> float | None:
        """Distance from home, in km. This is the entity's state."""
        station = self._station
        return station.distance if station else None

    @property
    def latitude(self) -> float | None:
        station = self._station
        return station.latitude if station else None

    @property
    def longitude(self) -> float | None:
        station = self._station
        return station.longitude if station else None

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
            ATTR_FUEL_TYPE: FUEL_TYPES.get(fuel_type, fuel_type),
            ATTR_PRICE: station.price,
            ATTR_LAST_UPDATED: station.last_updated,
        }
