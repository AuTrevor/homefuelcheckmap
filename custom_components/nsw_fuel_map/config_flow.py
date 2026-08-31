"""Config and options flows for NSW Fuel Map."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    SelectOptionDict,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import FuelCheckApiError, FuelCheckAuthError, FuelCheckClient
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
    FUEL_TYPES,
    MAX_RADIUS,
    MAX_UPDATE_INTERVAL,
    MIN_RADIUS,
    MIN_UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


def _fuel_type_selector() -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=code, label=label)
                for code, label in FUEL_TYPES.items()
            ],
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def _radius_selector() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=MIN_RADIUS,
            max=MAX_RADIUS,
            step=1,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="km",
        )
    )


def _interval_selector() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=MIN_UPDATE_INTERVAL,
            max=MAX_UPDATE_INTERVAL,
            step=1,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="hours",
        )
    )


async def _async_validate(hass, api_key: str, api_secret: str) -> str | None:
    """Return an error key if the credentials don't work, else None."""
    client = FuelCheckClient(async_get_clientsession(hass), api_key, api_secret)
    try:
        await client.async_validate_credentials()
    except FuelCheckAuthError:
        return "invalid_auth"
    except FuelCheckApiError as err:
        _LOGGER.debug("FuelCheck validation failed: %s", err)
        return "cannot_connect"
    except Exception:  # noqa: BLE001 - surface anything unexpected as a form error
        _LOGGER.exception("Unexpected error validating FuelCheck credentials")
        return "unknown"
    return None


class NswFuelMapConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup and reauth."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials and search parameters."""
        errors: dict[str, str] = {}

        if user_input is not None:
            fuel_type = user_input[CONF_FUEL_TYPE]
            # One entry per fuel type; a second U91 entry would duplicate markers.
            await self.async_set_unique_id(f"{DOMAIN}_{fuel_type}")
            self._abort_if_unique_id_configured()

            error = await _async_validate(
                self.hass, user_input[CONF_API_KEY], user_input[CONF_API_SECRET]
            )
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=f"NSW Fuel Map ({FUEL_TYPES.get(fuel_type, fuel_type)})",
                    data={
                        CONF_API_KEY: user_input[CONF_API_KEY],
                        CONF_API_SECRET: user_input[CONF_API_SECRET],
                        CONF_FUEL_TYPE: fuel_type,
                        CONF_RADIUS: int(user_input[CONF_RADIUS]),
                        CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
                    },
                )

        suggested = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_API_KEY, default=suggested.get(CONF_API_KEY, "")
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Required(
                    CONF_API_SECRET, default=suggested.get(CONF_API_SECRET, "")
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                vol.Required(
                    CONF_FUEL_TYPE,
                    default=suggested.get(CONF_FUEL_TYPE, DEFAULT_FUEL_TYPE),
                ): _fuel_type_selector(),
                vol.Required(
                    CONF_RADIUS, default=suggested.get(CONF_RADIUS, DEFAULT_RADIUS)
                ): _radius_selector(),
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=suggested.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): _interval_selector(),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "latitude": str(self.hass.config.latitude),
                "longitude": str(self.hass.config.longitude),
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth when the stored credentials stop working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect replacement credentials."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            error = await _async_validate(
                self.hass, user_input[CONF_API_KEY], user_input[CONF_API_SECRET]
            )
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_API_KEY: user_input[CONF_API_KEY],
                        CONF_API_SECRET: user_input[CONF_API_SECRET],
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_API_KEY, default=entry.data.get(CONF_API_KEY, "")
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Required(CONF_API_SECRET): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> NswFuelMapOptionsFlow:
        """Return the options flow."""
        return NswFuelMapOptionsFlow()


class NswFuelMapOptionsFlow(OptionsFlow):
    """Allow radius, fuel type, and interval to be changed after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        entry = self.config_entry

        if user_input is not None:
            fuel_type = user_input[CONF_FUEL_TYPE]
            unique_id = f"{DOMAIN}_{fuel_type}"

            # The unique ID encodes the fuel type, so switching it here has to move
            # the ID too — otherwise the entry keeps claiming its old type and a
            # second entry for the new one would not be caught as a duplicate.
            if any(
                other.unique_id == unique_id
                for other in self.hass.config_entries.async_entries(DOMAIN)
                if other.entry_id != entry.entry_id
            ):
                errors[CONF_FUEL_TYPE] = "already_configured"
            else:
                if unique_id != entry.unique_id:
                    label = FUEL_TYPES.get(fuel_type, fuel_type)
                    self.hass.config_entries.async_update_entry(
                        entry,
                        unique_id=unique_id,
                        title=f"NSW Fuel Map ({label})",
                    )
                return self.async_create_entry(
                    data={
                        CONF_FUEL_TYPE: fuel_type,
                        CONF_RADIUS: int(user_input[CONF_RADIUS]),
                        CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
                    }
                )

        current = {**entry.data, **entry.options, **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_FUEL_TYPE,
                    default=current.get(CONF_FUEL_TYPE, DEFAULT_FUEL_TYPE),
                ): _fuel_type_selector(),
                vol.Required(
                    CONF_RADIUS, default=current.get(CONF_RADIUS, DEFAULT_RADIUS)
                ): _radius_selector(),
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=current.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): _interval_selector(),
            }
        )
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )
