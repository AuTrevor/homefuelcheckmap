"""Tests for the NSW Fuel Map config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nsw_fuel_map.api import FuelCheckApiError, FuelCheckAuthError
from custom_components.nsw_fuel_map.const import (
    CONF_API_KEY,
    CONF_API_SECRET,
    CONF_FUEL_TYPE,
    CONF_RADIUS,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)

USER_INPUT = {
    CONF_API_KEY: "test-key",
    CONF_API_SECRET: "test-secret",
    CONF_FUEL_TYPE: "U91",
    CONF_RADIUS: 10,
    CONF_UPDATE_INTERVAL: 24,
}

VALIDATE = "custom_components.nsw_fuel_map.config_flow.FuelCheckClient.async_validate_credentials"
SETUP = "custom_components.nsw_fuel_map.async_setup_entry"


async def test_user_flow_creates_entry(hass) -> None:
    """Valid credentials produce a config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with (
        patch(VALIDATE, new=AsyncMock(return_value=None)),
        patch(SETUP, return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "NSW Fuel Map (Unleaded 91)"
    assert result["data"][CONF_API_KEY] == "test-key"
    assert result["data"][CONF_FUEL_TYPE] == "U91"
    assert result["data"][CONF_RADIUS] == 10


async def test_invalid_auth_shows_error(hass) -> None:
    """Bad credentials keep the user on the form with an error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(VALIDATE, new=AsyncMock(side_effect=FuelCheckAuthError("nope"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cannot_connect_shows_error(hass) -> None:
    """A network failure is reported as cannot_connect, not invalid_auth."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(VALIDATE, new=AsyncMock(side_effect=FuelCheckApiError("down"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_duplicate_fuel_type_aborts(hass, mock_config_entry) -> None:
    """A second entry for the same fuel type is rejected."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(VALIDATE, new=AsyncMock(return_value=None)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_updates_radius(hass, mock_config_entry) -> None:
    """Changing options writes them back to the entry."""
    mock_config_entry.add_to_hass(hass)

    with patch(SETUP, return_value=True):
        result = await hass.config_entries.options.async_init(
            mock_config_entry.entry_id
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_FUEL_TYPE: "DL", CONF_RADIUS: 25, CONF_UPDATE_INTERVAL: 12},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options[CONF_RADIUS] == 25
    assert mock_config_entry.options[CONF_FUEL_TYPE] == "DL"


async def test_options_flow_moves_unique_id_with_fuel_type(
    hass, mock_config_entry
) -> None:
    """Switching fuel type re-keys the entry, so duplicates stay detectable."""
    mock_config_entry.add_to_hass(hass)

    with patch(SETUP, return_value=True):
        result = await hass.config_entries.options.async_init(
            mock_config_entry.entry_id
        )
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_FUEL_TYPE: "P98", CONF_RADIUS: 10, CONF_UPDATE_INTERVAL: 24},
        )
        await hass.async_block_till_done()

    assert mock_config_entry.unique_id == f"{DOMAIN}_P98"
    assert mock_config_entry.title == "NSW Fuel Map (Premium 98)"

    # The freed-up U91 slot can now be claimed by a new entry...
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(VALIDATE, new=AsyncMock(return_value=None)), patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY

    # ...while P98 is now correctly reported as taken.
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(VALIDATE, new=AsyncMock(return_value=None)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**USER_INPUT, CONF_FUEL_TYPE: "P98"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_rejects_fuel_type_owned_by_another_entry(
    hass, mock_config_entry
) -> None:
    """Options can't move an entry onto a fuel type another entry already has."""
    mock_config_entry.add_to_hass(hass)

    other = MockConfigEntry(
        domain=DOMAIN,
        title="NSW Fuel Map (Diesel)",
        unique_id=f"{DOMAIN}_DL",
        data={**mock_config_entry.data, CONF_FUEL_TYPE: "DL"},
    )
    other.add_to_hass(hass)

    with patch(SETUP, return_value=True):
        result = await hass.config_entries.options.async_init(
            mock_config_entry.entry_id
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_FUEL_TYPE: "DL", CONF_RADIUS: 10, CONF_UPDATE_INTERVAL: 24},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_FUEL_TYPE: "already_configured"}
    # The entry keeps its original identity.
    assert mock_config_entry.unique_id == f"{DOMAIN}_U91"
