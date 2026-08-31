"""Async client for the NSW FuelCheck API."""

from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import aiohttp

from .const import (
    BASE_URL,
    DEFAULT_PRICE_UNIT,
    NEARBY_PATH,
    PRICE_UNITS,
    TIMESTAMP_FORMAT,
    TOKEN_EXPIRY_MARGIN,
    TOKEN_PATH,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

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

    async def async_get_nearby_stations(
        self, latitude: float, longitude: float, radius: int, fuel_type: str
    ) -> list[Station]:
        """Return stations within `radius` km, each with its current price."""
        body = {
            "fueltype": fuel_type,
            "brand": [],
            "namedlocation": "",
            "latitude": str(latitude),
            "longitude": str(longitude),
            "radius": str(radius),
            "sortby": "price",
            "sortascending": "true",
        }

        payload = await self._post(NEARBY_PATH, body)
        return self._parse_stations(payload, fuel_type)

    async def _post(self, path: str, body: dict) -> dict:
        """POST with a valid token, retrying once against a stale-token 401."""
        token = await self.async_get_token()

        for attempt in (1, 2):
            try:
                async with self._session.post(
                    f"{BASE_URL}{path}",
                    json=body,
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
    def _parse_stations(payload: dict, fuel_type: str) -> list[Station]:
        """Join the response's `stations` and `prices` arrays on station code."""
        prices_by_code: dict[str, dict] = {}
        for price in payload.get("prices") or []:
            # A station can list several fuel types even when we asked for one.
            if price.get("fueltype") and price["fueltype"] != fuel_type:
                continue
            # The API returns station codes as integers here and as integers in
            # `stations` too; normalise to str so the join is type-safe either way.
            code = str(price.get("stationcode") or "")
            if code:
                prices_by_code[code] = price

        stations: list[Station] = []
        for raw in payload.get("stations") or []:
            code = str(raw.get("code") or "")
            location = raw.get("location") or {}
            latitude = location.get("latitude")
            longitude = location.get("longitude")
            if not code or latitude is None or longitude is None:
                # Without coordinates there is nothing to put on the map.
                continue

            price_entry = prices_by_code.get(code, {})
            raw_price = price_entry.get("price")
            try:
                price = float(raw_price) if raw_price is not None else None
            except (TypeError, ValueError):
                price = None

            stations.append(
                Station(
                    code=code,
                    name=raw.get("name") or code,
                    brand=raw.get("brand") or "",
                    address=raw.get("address") or "",
                    latitude=float(latitude),
                    longitude=float(longitude),
                    distance=_as_float(location.get("distance")),
                    price=price,
                    price_unit=PRICE_UNITS.get(
                        str(price_entry.get("priceunit", "")).lower(),
                        DEFAULT_PRICE_UNIT,
                    ),
                    last_updated=price_entry.get("lastupdated"),
                )
            )

        return stations


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
