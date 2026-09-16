"""Constants for the NSW Fuel Map integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "nsw_fuel_map"

BASE_URL: Final = "https://api.onegov.nsw.gov.au"
TOKEN_PATH: Final = "/oauth/client_credential/accesstoken"
# Every current price in NSW (~3,300 stations, ~1.7 MiB, under a second). The
# /prices/nearby endpoint is not trustworthy: for some locations it silently
# returns only the single closest station no matter the radius asked for —
# verified at -35.422,149.236, where it reports 1 station while this endpoint
# shows 19 within 10km. Fetching everything and filtering here also means the
# radius means exactly what it says.
ALL_PRICES_PATH: Final = "/FuelPriceCheck/v2/fuel/prices"

# Mean Earth radius, for the great-circle distance from home to each station.
EARTH_RADIUS_KM: Final = 6371.0088

# The API gateway rejects requests whose `requesttimestamp` is not in this exact
# shape. Verified against the live API by scripts/smoke_test.py.
TIMESTAMP_FORMAT: Final = "%d/%m/%Y %I:%M:%S %p"

# Refresh the bearer token this many seconds before the API says it expires, so a
# long-running request can't have the token die underneath it.
TOKEN_EXPIRY_MARGIN: Final = 300

CONF_API_KEY: Final = "api_key"
CONF_API_SECRET: Final = "api_secret"
CONF_FUEL_TYPE: Final = "fuel_type"
CONF_RADIUS: Final = "radius"
CONF_UPDATE_INTERVAL: Final = "update_interval"

DEFAULT_RADIUS: Final = 10
DEFAULT_UPDATE_INTERVAL: Final = 24
MIN_RADIUS: Final = 1
MAX_RADIUS: Final = 50
MIN_UPDATE_INTERVAL: Final = 1
MAX_UPDATE_INTERVAL: Final = 168

SERVICE_REFRESH: Final = "refresh"

ATTR_STATION_CODE: Final = "station_code"
ATTR_STATION_NAME: Final = "station_name"
ATTR_BRAND: Final = "brand"
ATTR_ADDRESS: Final = "address"
ATTR_FUEL_TYPE: Final = "fuel_type"
ATTR_PRICE: Final = "price"
ATTR_LAST_UPDATED: Final = "last_updated"
ATTR_DISTANCE: Final = "distance"
ATTR_LATITUDE: Final = "latitude"
ATTR_LONGITUDE: Final = "longitude"

# Fuel type codes and names taken verbatim from /FuelCheckRefData/v2/fuel/lovs.
# The API also publishes combined codes (E10-U91, P95-P98, DL-PDL) for "either"
# searches; they are deliberately excluded, because prices come back tagged with
# the individual code and would not match the combined one we asked for.
FUEL_TYPES: Final[dict[str, str]] = {
    "E10": "Ethanol 94",
    "U91": "Unleaded 91",
    "E85": "Ethanol 105",
    "P95": "Premium 95",
    "P98": "Premium 98",
    "DL": "Diesel",
    "PDL": "Premium Diesel",
    "B20": "Biodiesel 20",
    "LPG": "LPG",
    "CNG": "CNG/NGV",
    "LNG": "LNG",
    "EV": "EV charge",
    "H2": "Hydrogen",
}

DEFAULT_FUEL_TYPE: Final = "U91"

# Where the API reports a denominator ("litre"), it says only that, while prices
# are in cents. Map to a display unit that says what the number actually is.
PRICE_UNITS: Final[dict[str, str]] = {
    "litre": "¢/L",
    "kg": "¢/kg",
    "kwh": "¢/kWh",
}

DEFAULT_PRICE_UNIT: Final = "¢/L"

# The statewide /fuel/prices feed omits `priceunit` entirely — it is absent from
# all ~10,500 price entries — so the unit has to come from the fuel type. Only
# the gases and electricity differ; road fuels and automotive LPG sell by the
# litre.
FUEL_TYPE_UNITS: Final[dict[str, str]] = {
    "EV": "¢/kWh",
    "H2": "¢/kg",
    "CNG": "¢/kg",
    "LNG": "¢/kg",
}
