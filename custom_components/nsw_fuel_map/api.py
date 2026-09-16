"""Async client for the NSW FuelCheck API."""

from __future__ import annotations

import asyncio
import base64
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import aiohttp

from .const import (
    ALL_PRICES_PATH,
    BASE_URL,
    DEFAULT_PRICE_UNIT,
    EARTH_RADIUS_KM,
    FUEL_TYPE_UNITS,
    PRICE_UNITS,
    TIMESTAMP_FORMAT,
    TOKEN_EXPIRY_MARGIN,
    TOKEN_PATH,
)

_LOGGER = logging.getLogger(__name__)

# The statewide price list is ~1.7 MiB, so allow more headroom than a token call.
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)

# The API sits behind Cloudflare, which rejects aiohttp's default user agent with
# "error code: 1010" before the request ever reaches the gateway.
USER_AGENT = "HomeAssistant/nsw_fuel_map"


class FuelCheckError(Exception):
    """Base error for the FuelCheck API."""


class FuelCheckAuthError(FuelCheckError):
    """Credentials were rejected. Surfaced to HA as a reauth trigger."""


class FuelCheckApiError(FuelCheckError):
    """Transient API or connection failure. The coordinator will retry."""


@dataclass(frozen=True, slots=True)
class Station:
    """A fuel station and its current price for the configured fuel type."""

    code: str
    name: str
    brand: str
    address: str
    latitude: float
    longitude: float
    distance: float | None
    price: float | None
    price_unit: str
    last_updated: str | None


class FuelCheckClient:
    """Talks to the FuelCheck API, holding a cached bearer token."""

    def __init__(
        self, session: aiohttp.ClientSession, api_key: str, api_secret: str
    ) -> None:
        self._session = session
        self._api_key = api_key
        self._api_secret = api_secret
        self._token: str | None = None
        self._token_expires: datetime | None = None
        # Serialises token fetches so concurrent callers don't stampede the OAuth
        # endpoint on startup or after an expiry.
        self._token_lock = asyncio.Lock()

    @property
    def _basic_auth(self) -> str:
        raw = f"{self._api_key}:{self._api_secret}".encode()
        return f"Basic {base64.b64encode(raw).decode()}"

    async def async_get_token(self, *, force: bool = False) -> str:
        """Return a valid bearer token, fetching a new one when needed."""
        async with self._token_lock:
            if not force and self._token and self._token_expires:
                if datetime.now() < self._token_expires:
                    return self._token

            try:
                async with self._session.get(
                    f"{BASE_URL}{TOKEN_PATH}",
                    params={"grant_type": "client_credentials"},
                    headers={
                        "Authorization": self._basic_auth,
                        "User-Agent": USER_AGENT,
                    },
                    timeout=REQUEST_TIMEOUT,
                ) as resp:
                    if resp.status in (401, 403):
                        raise FuelCheckAuthError(
                            f"FuelCheck rejected the API key or secret (HTTP {resp.status})"
                        )
                    if resp.status != 200:
                        body = await resp.text()
                        raise FuelCheckApiError(
                            f"Token request failed (HTTP {resp.status}): {body[:200]}"
                        )
                    payload = await resp.json(content_type=None)
            except aiohttp.ClientError as err:
                raise FuelCheckApiError(f"Could not reach FuelCheck: {err}") from err
            except asyncio.TimeoutError as err:
                raise FuelCheckApiError("Timed out requesting a FuelCheck token") from err

            token = payload.get("access_token")
            if not token:
                raise FuelCheckApiError("Token response contained no access_token")

            # expires_in comes back as a string of seconds.
            try:
                expires_in = int(payload.get("expires_in", 43199))
            except (TypeError, ValueError):
                expires_in = 43199

            self._token = token
            self._token_expires = datetime.now() + timedelta(
                seconds=max(expires_in - TOKEN_EXPIRY_MARGIN, 60)
            )
            _LOGGER.debug("Obtained FuelCheck token, valid for %ss", expires_in)
            return token

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": USER_AGENT,
            "apikey": self._api_key,
            "transactionid": str(uuid.uuid4()),
            "requesttimestamp": datetime.now().strftime(TIMESTAMP_FORMAT),
        }

    async def async_validate_credentials(self) -> None:
        """Raise if the configured credentials cannot obtain a token."""
        await self.async_get_token(force=True)

    async def async_get_stations_in_radius(
        self, latitude: float, longitude: float, radius: int, fuel_type: str
    ) -> list[Station]:
        """Return stations within `radius` km of the point, with their prices.

        Fetches every price in NSW and filters here rather than asking the API for
        nearby stations; see ALL_PRICES_PATH in const.py for why.
        """
        payload = await self._get(ALL_PRICES_PATH)
        return self._parse_stations(payload, fuel_type, latitude, longitude, radius)

    async def _get(self, path: str) -> dict:
        """GET with a valid token, retrying once against a stale-token 401."""
        token = await self.async_get_token()

        for attempt in (1, 2):
            try:
                async with self._session.get(
                    f"{BASE_URL}{path}",
                    headers=self._headers(token),
                    timeout=REQUEST_TIMEOUT,
                ) as resp:
                    if resp.status == 401 and attempt == 1:
                        # The cached token may have been revoked early; get a fresh
                        # one and try the request one more time.
                        _LOGGER.debug("FuelCheck returned 401, refreshing token")
                        token = await self.async_get_token(force=True)
                        continue
                    if resp.status in (401, 403):
                        raise FuelCheckAuthError(
                            f"FuelCheck rejected the request (HTTP {resp.status})"
                        )
                    if resp.status != 200:
                        text = await resp.text()
                        raise FuelCheckApiError(
                            f"FuelCheck request to {path} failed "
                            f"(HTTP {resp.status}): {text[:200]}"
                        )
                    return await resp.json(content_type=None)
            except aiohttp.ClientError as err:
                raise FuelCheckApiError(f"Could not reach FuelCheck: {err}") from err
            except asyncio.TimeoutError as err:
                raise FuelCheckApiError(f"Timed out calling {path}") from err

        raise FuelCheckApiError(f"FuelCheck request to {path} could not be completed")

    @staticmethod
    def _parse_stations(
        payload: dict,
        fuel_type: str,
        latitude: float,
        longitude: float,
        radius: float,
    ) -> list[Station]:
        """Join `stations` to `prices` on code, keeping those within the radius.

        Stations with no price for `fuel_type` are dropped: the statewide feed
        includes EV chargers, LPG-only sites and motels that would otherwise
        become blank markers.
        """
        prices_by_code: dict[str, dict] = {}
        for price in payload.get("prices") or []:
            # The feed carries every fuel type for every station.
            if price.get("fueltype") != fuel_type:
                continue
            # Station codes come back as ints on some endpoints and strs on
            # others; normalise so the join is type-safe either way.
            code = str(price.get("stationcode") or "")
            if code:
                prices_by_code[code] = price

        stations: list[Station] = []
        for raw in payload.get("stations") or []:
            code = str(raw.get("code") or "")
            price_entry = prices_by_code.get(code)
            if not code or price_entry is None:
                continue

            location = raw.get("location") or {}
            station_lat = _as_float(location.get("latitude"))
            station_lon = _as_float(location.get("longitude"))
            if station_lat is None or station_lon is None:
                # Without coordinates there is nothing to put on the map.
                continue

            distance = _haversine_km(latitude, longitude, station_lat, station_lon)
            if distance > radius:
                continue

            try:
                price = float(price_entry["price"])
            except (KeyError, TypeError, ValueError):
                continue

            stations.append(
                Station(
                    code=code,
                    name=raw.get("name") or code,
                    brand=raw.get("brand") or "",
                    address=raw.get("address") or "",
                    latitude=station_lat,
                    longitude=station_lon,
                    distance=round(distance, 2),
                    price=price,
                    # Prefer the denominator the API sends, but this feed omits
                    # it, so fall back to what the fuel type implies.
                    price_unit=PRICE_UNITS.get(
                        str(price_entry.get("priceunit", "")).lower(),
                        FUEL_TYPE_UNITS.get(fuel_type, DEFAULT_PRICE_UNIT),
                    ),
                    last_updated=price_entry.get("lastupdated"),
                )
            )

        return stations


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
