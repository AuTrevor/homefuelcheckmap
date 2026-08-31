"""Tests for the map markers and the cheapest-price sensor."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from custom_components.nsw_fuel_map.api import Station
from custom_components.nsw_fuel_map.const import DOMAIN, SERVICE_REFRESH

CLIENT = "custom_components.nsw_fuel_map.coordinator.FuelCheckClient"


def _station(code: str, price: float | None, **kwargs) -> Station:
    defaults = dict(
        code=code,
        name=f"Station {code}",
        brand="Brand",
        address="1 Test St",
        latitude=-33.86,
        longitude=151.2,
        distance=2.0,
        price=price,
        price_unit="litre",
        last_updated="01/08/2026 06:00:00",
    )
    defaults.update(kwargs)
    return Station(**defaults)


async def _setup(hass, entry, stations: list[Station]):
    entry.add_to_hass(hass)
    with patch(f"{CLIENT}.async_get_nearby_stations", new=AsyncMock(return_value=stations)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_markers_created_per_station(hass, mock_config_entry) -> None:
    """Every station in range gets a geo_location entity with coordinates."""
    await _setup(
        hass,
        mock_config_entry,
        [
            _station("101", 172.9, latitude=-33.85, longitude=151.03),
            _station("202", 189.9, latitude=-33.87, longitude=151.08),
        ],
    )

    markers = [s for s in hass.states.async_all("geo_location")]
    assert len(markers) == 2

    marker = next(m for m in markers if m.attributes["station_code"] == "101")
    assert marker.attributes["latitude"] == -33.85
    assert marker.attributes["longitude"] == 151.03
    assert marker.attributes["price"] == 172.9
    assert marker.attributes["source"] == "nsw_fuel_map"
    assert marker.attributes["fuel_type"] == "Unleaded 91"


async def test_cheapest_sensor_picks_lowest_price(hass, mock_config_entry) -> None:
    """The sensor state is the minimum price, with that station's details."""
    await _setup(
        hass,
        mock_config_entry,
        [_station("101", 189.9), _station("202", 172.9), _station("303", 181.5)],
    )

    state = hass.states.get("sensor.nsw_fuel_map_unleaded_91_cheapest_unleaded_91")
    if state is None:
        # Entity id depends on device naming; find it by unique attributes instead.
        state = next(
            s
            for s in hass.states.async_all("sensor")
            if s.attributes.get("station_code") is not None
        )

    assert float(state.state) == 172.9
    assert state.attributes["station_code"] == "202"
    assert state.attributes["stations_in_range"] == 3


async def test_cheapest_sensor_ignores_priceless_stations(hass, mock_config_entry) -> None:
    """Stations with no price for the fuel type don't win the minimum."""
    await _setup(hass, mock_config_entry, [_station("101", None), _station("202", 199.9)])

    state = next(
        s
        for s in hass.states.async_all("sensor")
        if s.attributes.get("station_code") is not None
    )
    assert float(state.state) == 199.9
    assert state.attributes["station_code"] == "202"


async def test_cheapest_sensor_unavailable_when_no_prices(hass, mock_config_entry) -> None:
    """No priced stations in range means unavailable rather than an error."""
    await _setup(hass, mock_config_entry, [_station("101", None)])

    sensors = [s for s in hass.states.async_all("sensor")]
    assert sensors
    assert all(s.state == "unavailable" for s in sensors)


async def test_refresh_service_removed_with_last_entry(hass, mock_config_entry) -> None:
    """Unloading the only entry takes the shared refresh service with it."""
    await _setup(hass, mock_config_entry, [_station("101", 172.9)])
    assert hass.services.has_service(DOMAIN, SERVICE_REFRESH)

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.services.has_service(DOMAIN, SERVICE_REFRESH)


async def test_search_uses_current_home_location(hass, mock_config_entry) -> None:
    """Moving the home location is picked up on the next poll, without a reload."""
    await hass.config.async_update(latitude=-33.8688, longitude=151.2093)

    mock_config_entry.add_to_hass(hass)
    fetch = AsyncMock(return_value=[_station("101", 172.9)])
    with patch(f"{CLIENT}.async_get_nearby_stations", new=fetch):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert fetch.await_args.args[:2] == (-33.8688, 151.2093)

        await hass.config.async_update(latitude=-32.9283, longitude=151.7817)
        await hass.config_entries.async_get_entry(
            mock_config_entry.entry_id
        ).runtime_data.async_refresh()
        await hass.async_block_till_done()

    assert fetch.await_args.args[:2] == (-32.9283, 151.7817)
