"""Polling coordinator for NSW Fuel Map."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FuelCheckApiError, FuelCheckAuthError, FuelCheckClient, Station
from .const import (
    CONF_API_KEY,
    CONF_API_SECRET,
    CONF_FUEL_TYPE,
    CONF_RADIUS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_FUEL_TYPE,
    DEFAULT_RADIUS,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

type NswFuelConfigEntry = ConfigEntry[NswFuelCoordinator]


class NswFuelCoordinator(DataUpdateCoordinator[dict[str, Station]]):
    """Fetches nearby fuel prices on a schedule, keyed by station code."""

    def __init__(self, hass: HomeAssistant, entry: NswFuelConfigEntry) -> None:
        options = {**entry.data, **entry.options}
        self.fuel_type: str = options.get(CONF_FUEL_TYPE, DEFAULT_FUEL_TYPE)
        self.radius: int = options.get(CONF_RADIUS, DEFAULT_RADIUS)

        hours = options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)

        self.client = FuelCheckClient(
            async_get_clientsession(hass),
            options[CONF_API_KEY],
            options[CONF_API_SECRET],
        )

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({self.fuel_type})",
            update_interval=timedelta(hours=hours),
            config_entry=entry,
        )

    async def _async_update_data(self) -> dict[str, Station]:
        # Read the home location per poll rather than caching it at setup, so
        # moving the home in Settings → System → General takes effect without a
        # reload of the entry.
        latitude = self.hass.config.latitude
        longitude = self.hass.config.longitude

        try:
            stations = await self.client.async_get_nearby_stations(
                latitude, longitude, self.radius, self.fuel_type
            )
        except FuelCheckAuthError as err:
            # Triggers HA's reauth flow rather than retrying with dead credentials.
            raise ConfigEntryAuthFailed(str(err)) from err
        except FuelCheckApiError as err:
            raise UpdateFailed(str(err)) from err

        _LOGGER.debug(
            "Fetched %s stations within %skm for %s",
            len(stations),
            self.radius,
            self.fuel_type,
        )
        return {station.code: station for station in stations}
