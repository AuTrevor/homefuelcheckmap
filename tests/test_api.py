"""Tests for the FuelCheck API client."""

from __future__ import annotations

import pytest

from custom_components.nsw_fuel_map.api import (
    FuelCheckApiError,
    FuelCheckAuthError,
    FuelCheckClient,
)
from custom_components.nsw_fuel_map.const import BASE_URL, NEARBY_PATH, TOKEN_PATH

TOKEN_URL = f"{BASE_URL}{TOKEN_PATH}"
NEARBY_URL = f"{BASE_URL}{NEARBY_PATH}"


def test_parse_joins_stations_to_prices(nearby_payload) -> None:
    """Stations are matched to prices on station code, filtered by fuel type.

    The API sends station codes as integers; the join must still work.
    """
    stations = FuelCheckClient._parse_stations(nearby_payload, "U91")

    assert len(stations) == 2
    by_code = {s.code: s for s in stations}
    assert by_code["16924"].price == 195.7  # not the 210.9 P98 price
    assert by_code["16924"].name == "Budget Petrol Chippendale"
    assert by_code["16924"].latitude == -33.8878
    assert by_code["16924"].distance == 2.22
    assert by_code["20202"].price == 189.9


def test_parse_maps_price_unit(nearby_payload) -> None:
    """The API's "litre" denominator becomes a unit that names the value."""
    stations = FuelCheckClient._parse_stations(nearby_payload, "U91")
    assert all(s.price_unit == "¢/L" for s in stations)


def test_parse_skips_stations_without_coordinates() -> None:
    """A station with no location can't be mapped, so it is dropped."""
    payload = {
        "stations": [
            {"code": "1", "name": "No Location", "location": {}},
            {
                "code": "2",
                "name": "Fine",
                "location": {"latitude": -33.0, "longitude": 151.0},
            },
        ],
        "prices": [{"stationcode": "2", "fueltype": "U91", "price": 100.0}],
    }
    stations = FuelCheckClient._parse_stations(payload, "U91")
    assert [s.code for s in stations] == ["2"]


def test_parse_station_without_price(nearby_payload) -> None:
    """A station with no matching price still appears, with price None."""
    payload = {"stations": nearby_payload["stations"], "prices": []}
    stations = FuelCheckClient._parse_stations(payload, "U91")
    assert len(stations) == 2
    assert all(s.price is None for s in stations)


async def test_token_is_cached(hass, aioclient_mock) -> None:
    """A second call reuses the cached token rather than re-authenticating."""
    aioclient_mock.get(TOKEN_URL, json={"access_token": "tok", "expires_in": "43199"})
    client = FuelCheckClient(_session(hass), "key", "secret")

    assert await client.async_get_token() == "tok"
    assert await client.async_get_token() == "tok"
    assert aioclient_mock.call_count == 1


async def test_token_refreshed_when_forced(hass, aioclient_mock) -> None:
    """force=True bypasses the cache."""
    aioclient_mock.get(TOKEN_URL, json={"access_token": "tok", "expires_in": "43199"})
    client = FuelCheckClient(_session(hass), "key", "secret")

    await client.async_get_token()
    await client.async_get_token(force=True)
    assert aioclient_mock.call_count == 2


async def test_bad_credentials_raise_auth_error(hass, aioclient_mock) -> None:
    """A 401 from the token endpoint is an auth error, not a transient one."""
    aioclient_mock.get(TOKEN_URL, status=401, text="unauthorized")
    client = FuelCheckClient(_session(hass), "key", "bad")

    with pytest.raises(FuelCheckAuthError):
        await client.async_get_token()


async def test_server_error_raises_api_error(hass, aioclient_mock) -> None:
    """A 500 is transient and should be retried by the coordinator."""
    aioclient_mock.get(TOKEN_URL, status=500, text="boom")
    client = FuelCheckClient(_session(hass), "key", "secret")

    with pytest.raises(FuelCheckApiError):
        await client.async_get_token()


async def test_nearby_retries_once_on_401(hass, aioclient_mock, nearby_payload) -> None:
    """A stale token gets refreshed and the request retried exactly once."""
    aioclient_mock.get(TOKEN_URL, json={"access_token": "tok", "expires_in": "43199"})
    aioclient_mock.post(NEARBY_URL, status=401, text="expired")
    client = FuelCheckClient(_session(hass), "key", "secret")

    # Both attempts 401 -> gives up with an auth error, having refreshed once.
    with pytest.raises(FuelCheckAuthError):
        await client.async_get_nearby_stations(-33.86, 151.2, 10, "U91")

    token_calls = [c for c in aioclient_mock.mock_calls if str(c[1]).startswith(TOKEN_URL)]
    assert len(token_calls) == 2


async def test_nearby_returns_stations(hass, aioclient_mock, nearby_payload) -> None:
    """A successful round trip yields parsed stations."""
    aioclient_mock.get(TOKEN_URL, json={"access_token": "tok", "expires_in": "43199"})
    aioclient_mock.post(NEARBY_URL, json=nearby_payload)
    client = FuelCheckClient(_session(hass), "key", "secret")

    stations = await client.async_get_nearby_stations(-33.86, 151.2, 10, "U91")
    assert {s.code for s in stations} == {"16924", "20202"}


def _session(hass):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    return async_get_clientsession(hass)
