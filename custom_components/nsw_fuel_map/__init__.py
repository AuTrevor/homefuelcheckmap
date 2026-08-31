"""The NSW Fuel Map integration."""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, SERVICE_REFRESH
from .coordinator import NswFuelConfigEntry, NswFuelCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.GEO_LOCATION, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: NswFuelConfigEntry) -> bool:
    """Set up NSW Fuel Map from a config entry."""
    coordinator = NswFuelCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NswFuelConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # HA marks this entry UNLOAD_IN_PROGRESS before calling us, and
    # async_loaded_entries only returns LOADED ones, so this entry is already
    # excluded — an empty result means it was the last one.
    if unloaded and not hass.config_entries.async_loaded_entries(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_REFRESH)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: NswFuelConfigEntry) -> None:
    """Reload when options change, so radius/fuel type/interval take effect."""
    await hass.config_entries.async_reload(entry.entry_id)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the manual refresh service once, shared by all entries."""
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        return

    async def async_handle_refresh(call: ServiceCall) -> None:
        """Force an immediate price refresh for every loaded entry."""
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            await entry.runtime_data.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, async_handle_refresh)
