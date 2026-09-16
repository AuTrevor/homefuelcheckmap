"""Tests for the FuelCheck API client."""

from __future__ import annotations

import pytest

from custom_components.nsw_fuel_map.api import (
    FuelCheckApiError,
    FuelCheckAuthError,
    FuelCheckClient,
)
from custom_components.nsw_fuel_map.const import ALL_PRICES_PATH, BASE_URL, TOKEN_PATH

TOKEN_URL = f"{BASE_URL}{TOKEN_PATH}"
PRICES_URL = f"{BASE_URL}{ALL_PRICES_PATH}"

# Sydney CBD, close to the two metro stations in the fixture.
HOME = (-33.8688, 151.2093)


def test_parse_joins_stations_to_prices(prices_payload) -> None:
    """Stations are matched to prices on station code, filtered by fuel type."""
    stations = FuelCheckClient._parse_stations(prices_payload, "U91", *HOME, 20)

    by_code = {s.code: s for s in stations}
    assert set(by_code) == {"16924", "20202"}
    assert by_code["16924"].price == 195.7  # not the 210.9 P98 price
    assert by_code["16924"].name == "Budget Petrol Chippendale"
    assert by_code["16924"].latitude == -33.8878
    assert by_code["20202"].price == 189.9


def test_parse_computes_distance_from_home(prices_payload) -> None:
    """The statewide feed carries no distance, so it is calculated here."""
    stations = FuelCheckClient._parse_stations(prices_payload, "U91", *HOME, 20)

    by_code = {s.code: s for s in stations}
    # Chippendale is ~2.1km from the CBD, Homebush ~12km.
    assert 1.8 < by_code["16924"].distance < 2.6
    assert 11.0 < by_code["20202"].distance < 13.0


def test_parse_excludes_stations_beyond_the_radius(prices_payload) -> None:
    """The radius is enforced locally, which is the whole point of this endpoint."""
    near = FuelCheckClient._parse_stations(prices_payload, "U91", *HOME, 5)
    assert [s.code for s in near] == ["16924"]

    # Broken Hill is in the feed but ~900km away, so no radius we allow reaches it.
    far = FuelCheckClient._parse_stations(prices_payload, "U91", *HOME, 50)
    assert "30001" not in {s.code for s in far}


def test_parse_maps_price_unit(prices_payload) -> None:
    """The API's "litre" denominator becomes a unit that names the value."""
    stations = FuelCheckClient._parse_stations(prices_payload, "U91", *HOME, 20)
    assert stations and all(s.price_unit == "¢/L" for s in stations)


def test_parse_unit_falls_back_to_the_fuel_type() -> None:
    """The statewide feed sends no priceunit, so the fuel type has to supply it."""
    payload = {
        "stations": [
            {
                "code": "1",
                "name": "Charger",
                "location": {"latitude": -33.87, "longitude": 151.21},
            }
        ],
        "prices": [{"stationcode": 1, "fueltype": "EV", "price": 60.0}],
    }
    stations = FuelCheckClient._parse_stations(payload, "EV", *HOME, 20)
    assert [s.price_unit for s in stations] == ["¢/kWh"]


def test_parse_joins_int_codes_to_str_codes() -> None:
    """`prices` sends stationcode as an int while `stations` sends code as a str."""
    payload = {
        "stations": [
            {
                "code": "972",
                "name": "United Umina",
                "location": {"latitude": -33.87, "longitude": 151.21},
            }
        ],
        "prices": [{"stationcode": 972, "fueltype": "U91", "price": 199.9}],
    }
    stations = FuelCheckClient._parse_stations(payload, "U91", *HOME, 20)
    assert [(s.code, s.price) for s in stations] == [("972", 199.9)]


def test_parse_skips_stations_without_coordinates() -> None:
    """A station with no location can't be mapped or measured, so it is dropped."""
    payload = {
        "stations": [
            {"code": "1", "name": "No Location", "location": {}},
            {
                "code": "2",
                "name": "Fine",
                "location": {"latitude": -33.87, "longitude": 151.21},
            },
        ],
        "prices": [
            {"stationcode": "1", "fueltype": "U91", "price": 100.0},
            {"stationcode": "2", "fueltype": "U91", "price": 100.0},
        ],
    }
    stations = FuelCheckClient._parse_stations(payload, "U91", *HOME, 20)
    assert [s.code for s in stations] == ["2"]


def test_parse_drops_stations_without_a_price_for_the_fuel_type(prices_payload) -> None:
    """The statewide feed lists EV/LPG-only sites that would be blank markers."""
    payload = {"stations": prices_payload["stations"], "prices": []}
    assert FuelCheckClient._parse_stations(payload, "U91", *HOME, 20) == []


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


async def test_prices_retries_once_on_401(hass, aioclient_mock) -> None:
    """A stale token gets refreshed and the request retried exactly once."""
    aioclient_mock.get(TOKEN_URL, json={"access_token": "tok", "expires_in": "43199"})
    aioclient_mock.get(PRICES_URL, status=401, text="expired")
    client = FuelCheckClient(_session(hass), "key", "secret")

    # Both attempts 401 -> gives up with an auth error, having refreshed once.
    with pytest.raises(FuelCheckAuthError):
        await client.async_get_stations_in_radius(*HOME, 10, "U91")

    token_calls = [c for c in aioclient_mock.mock_calls if str(c[1]).startswith(TOKEN_URL)]
    assert len(token_calls) == 2


async def test_returns_stations_in_radius(hass, aioclient_mock, prices_payload) -> None:
    """A successful round trip yields the stations inside the radius."""
    aioclient_mock.get(TOKEN_URL, json={"access_token": "tok", "expires_in": "43199"})
    aioclient_mock.get(PRICES_URL, json=prices_payload)
    client = FuelCheckClient(_session(hass), "key", "secret")

    stations = await client.async_get_stations_in_radius(*HOME, 20, "U91")
    assert {s.code for s in stations} == {"16924", "20202"}


def _session(hass):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    return async_get_clientsession(hass)
