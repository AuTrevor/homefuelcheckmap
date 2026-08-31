"""Shared fixtures for NSW Fuel Map tests."""

from __future__ import annotations

import pytest

from custom_components.nsw_fuel_map.const import (
    CONF_API_KEY,
    CONF_API_SECRET,
    CONF_FUEL_TYPE,
    CONF_RADIUS,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)

from pytest_homeassistant_custom_component.common import MockConfigEntry

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every test."""
    yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A configured NSW Fuel Map entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="NSW Fuel Map (Unleaded 91)",
        unique_id=f"{DOMAIN}_U91",
        data={
            CONF_API_KEY: "test-key",
            CONF_API_SECRET: "test-secret",
            CONF_FUEL_TYPE: "U91",
            CONF_RADIUS: 10,
            CONF_UPDATE_INTERVAL: 24,
        },
    )


@pytest.fixture
def nearby_payload() -> dict:
    """A representative /prices/nearby response."""
    # Shape and types copied from a real response captured by scripts/smoke_test.py:
    # note that `code`/`stationcode` come back as integers, not strings.
    return {
        "stations": [
            {
                "brandid": "1-GFYV-2",
                "stationid": "1-32D56EQ",
                "brand": "Budget",
                "code": 16924,
                "name": "Budget Petrol Chippendale",
                "address": "66-70 Regent Street, CHIPPENDALE NSW 2008",
                "location": {
                    "distance": 2.22,
                    "latitude": -33.8878,
                    "longitude": 151.201744,
                },
                "state": "NSW",
            },
            {
                "brandid": "1-2Y2-6",
                "stationid": "1-32D56ER",
                "brand": "7-Eleven",
                "code": 20202,
                "name": "7-Eleven Homebush",
                "address": "1 Parramatta Rd, HOMEBUSH NSW 2140",
                "location": {
                    "distance": 4.1,
                    "latitude": -33.8650,
                    "longitude": 151.0800,
                },
                "state": "NSW",
            },
        ],
        "prices": [
            {
                "stationcode": 16924,
                "fueltype": "U91",
                "price": 195.7,
                "lastupdated": "2026-08-02 10:48:51",
                "priceunit": "litre",
                "state": "NSW",
            },
            {
                "stationcode": 20202,
                "fueltype": "U91",
                "price": 189.9,
                "lastupdated": "2026-08-02 07:30:00",
                "priceunit": "litre",
                "state": "NSW",
            },
            {
                # A different fuel type at a station we already have; must be ignored.
                "stationcode": 16924,
                "fueltype": "P98",
                "price": 210.9,
                "lastupdated": "2026-08-02 10:48:51",
                "priceunit": "litre",
                "state": "NSW",
            },
        ],
    }
