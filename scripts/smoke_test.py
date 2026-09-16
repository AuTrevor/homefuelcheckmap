"""Probe the NSW FuelCheck API directly, outside Home Assistant.

Confirms the auth handshake, the exact header formats the gateway accepts, and the
response shapes the integration parses. Run this after changing anything in api.py:

    python scripts/smoke_test.py
"""

from __future__ import annotations

import base64
import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

BASE_URL = "https://api.onegov.nsw.gov.au"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
USER_AGENT = "HomeAssistant/nsw_fuel_map"

# Sydney CBD, just to guarantee the radius search returns something.
TEST_LAT = -33.8688
TEST_LON = 151.2093
TEST_RADIUS = 3
TEST_FUELTYPE = "U91"


def load_env() -> dict[str, str]:
    """Parse .env. Tolerates both `KEY: value` and `KEY=value` styles."""
    values: dict[str, str] = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in (":", "="):
            if sep in line:
                key, _, value = line.partition(sep)
                values[key.strip()] = value.strip()
                break
    return values


def request(url: str, headers: dict[str, str], body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    # The gateway sits behind Cloudflare, which blocks the default urllib/aiohttp
    # user agent with "error code: 1010".
    headers = {"User-Agent": USER_AGENT, **headers}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode()


def get_token(auth_header: str) -> str:
    url = f"{BASE_URL}/oauth/client_credential/accesstoken?grant_type=client_credentials"
    status, payload = request(url, {"Authorization": auth_header})
    print(f"--- token: HTTP {status}")
    if status != 200:
        print(payload)
        sys.exit(1)
    print(json.dumps({k: v for k, v in payload.items() if k != "access_token"}, indent=2))
    print(f"expires_in = {payload.get('expires_in')} seconds")
    return payload["access_token"]


def api_headers(api_key: str, token: str, timestamp_fmt: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
        "apikey": api_key,
        "transactionid": str(uuid.uuid4()),
        "requesttimestamp": datetime.now().strftime(timestamp_fmt),
    }


def probe_timestamp_formats(api_key: str, token: str) -> str | None:
    """The gateway is picky about requesttimestamp; find a format it accepts."""
    candidates = {
        "DD/MM/YYYY hh:mm:ss AM/PM": "%d/%m/%Y %I:%M:%S %p",
        "ISO 8601 Z": "%Y-%m-%dT%H:%M:%SZ",
        "DD/MM/YYYY HH:mm:ss": "%d/%m/%Y %H:%M:%S",
    }
    url = f"{BASE_URL}/FuelPriceCheck/v2/fuel/prices/nearby"
    body = {
        "fueltype": TEST_FUELTYPE,
        "brand": [],
        "namedlocation": "",
        "latitude": str(TEST_LAT),
        "longitude": str(TEST_LON),
        "radius": str(TEST_RADIUS),
        "sortby": "price",
        "sortascending": "true",
    }
    for label, fmt in candidates.items():
        status, _ = request(url, api_headers(api_key, token, fmt), body)
        print(f"--- requesttimestamp {label!r} ({fmt}): HTTP {status}")
        if status == 200:
            return fmt
    return None


def main() -> None:
    env = load_env()
    api_key = env["API_KEY"]
    auth_header = env.get("AUTHORIZATION_HEADER") or "Basic " + base64.b64encode(
        f"{api_key}:{env['API_SECRET']}".encode()
    ).decode()

    token = get_token(auth_header)

    fmt = probe_timestamp_formats(api_key, token)
    if fmt is None:
        print("No candidate requesttimestamp format was accepted.")
        sys.exit(1)
    print(f"\n==> Accepted requesttimestamp format: {fmt}\n")

    # Reference data: authoritative fuel type and brand codes.
    status, lovs = request(
        f"{BASE_URL}/FuelCheckRefData/v2/fuel/lovs?states=NSW",
        {**api_headers(api_key, token, fmt), "if-modified-since": "Sun, 01 Jan 2017 00:00:00 GMT"},
    )
    print(f"--- reference data: HTTP {status}")
    if status == 200:
        fuel_types = lovs.get("fueltypes", [])
        print("fuel types:", json.dumps(fuel_types, indent=2))
        brands = lovs.get("brands", {})
        brand_items = brands.get("items", brands) if isinstance(brands, dict) else brands
        print(f"brand count: {len(brand_items)}")
        print("sample brands:", json.dumps(brand_items[:5], indent=2))
    else:
        print(lovs)

    # Statewide prices: the shape the coordinator parses.
    status, all_prices = request(
        f"{BASE_URL}/FuelPriceCheck/v2/fuel/prices", api_headers(api_key, token, fmt)
    )
    print(f"\n--- all prices: HTTP {status}")
    if status != 200:
        print(all_prices)
        sys.exit(1)
    print("top-level keys:", list(all_prices.keys()))
    stations = all_prices.get("stations", [])
    prices = all_prices.get("prices", [])
    print(f"stations: {len(stations)}  prices: {len(prices)}")
    if stations:
        print("sample station:", json.dumps(stations[0], indent=2))
    if prices:
        print("sample price:", json.dumps(prices[0], indent=2))

    # Why the integration doesn't use /prices/nearby: for some points it returns
    # only the single closest station, whatever radius is asked for. Compare the
    # two endpoints so a future fix to the API is easy to notice.
    print("\n--- /prices/nearby vs local filtering")
    for lat, lon, label in ((TEST_LAT, TEST_LON, "Sydney CBD"), (-35.422, 149.236, "Googong")):
        for radius in (10, 50):
            status, nearby = request(
                f"{BASE_URL}/FuelPriceCheck/v2/fuel/prices/nearby",
                api_headers(api_key, token, fmt),
                {
                    "fueltype": TEST_FUELTYPE,
                    "brand": [],
                    "namedlocation": "",
                    "latitude": str(lat),
                    "longitude": str(lon),
                    "radius": str(radius),
                    "sortby": "price",
                    "sortascending": "true",
                },
            )
            n = len(nearby.get("stations", [])) if status == 200 else f"HTTP {status}"
            local = sum(
                1
                for s in stations
                if (loc := s.get("location"))
                and loc.get("latitude") is not None
                and _distance_km(lat, lon, loc["latitude"], loc["longitude"]) <= radius
            )
            print(f"  {label:<11} {radius:>2}km: nearby={n:<5} local={local}")


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance, mirroring api._haversine_km."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((phi2 - phi1) / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


if __name__ == "__main__":
    main()
