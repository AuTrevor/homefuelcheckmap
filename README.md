# NSW Fuel Map

A Home Assistant custom integration that plots NSW fuel prices on the map. Every
petrol station within a chosen radius of your home becomes a marker labelled with
its current price, refreshed daily from the NSW Government
[FuelCheck API](https://apinsw.onegov.nsw.gov.au).

Also creates a **cheapest nearby** sensor you can use in automations and history
graphs.

## Getting API credentials

1. Register at <https://apinsw.onegov.nsw.gov.au>.
2. Create an application and subscribe it to the **Fuel API**.
3. Copy the **API key** and **API secret** from your application.

## Installation

### HACS (recommended)

Add this repository as a custom repository of type *Integration*, install
**NSW Fuel Map**, then restart Home Assistant.

### Manual

Copy `custom_components/nsw_fuel_map/` into your Home Assistant `config/custom_components/`
directory and restart.

## Setup

Go to **Settings → Devices & Services → Add Integration → NSW Fuel Map** and enter:

| Field | Notes |
| --- | --- |
| API key / secret | From the NSW API portal |
| Fuel type | One type per entry (U91, P98, DL, …) |
| Search radius | Kilometres from your HA home location. Default 10 |
| Update interval | Hours between refreshes. Default 24 |

The search is centred on the home coordinates in **Settings → System → General**, so
make sure those are correct.

Prices come from FuelCheck's statewide feed and the radius is applied locally, so it
means exactly what it says. The API's own `/prices/nearby` endpoint is not used: for
some locations it returns only the single closest station regardless of the radius
asked for, and it under-reports everywhere (at Sydney CBD it returns 31 stations
within 10km where the statewide feed has 428).

To track more than one fuel type, add the integration again and pick a different type.

Radius, interval, and fuel type can all be changed later via **Configure** on the entry.
Switching fuel type renames the entry and re-points it at the new type; if another entry
already tracks that type, the form says so rather than creating a clash.

## Adding the map card

```yaml
type: map
geo_location_sources:
  - nsw_fuel_map
auto_fit: true
hours_to_show: 0
```

Each marker is named `<station> <price>`, e.g. `Costco Auburn 172.9`.

## Entities

- `geo_location.*` — one per station in range, for the map. State is distance from
  home in km (Home Assistant fixes this for geo_location entities and it can't be
  changed); attributes include `price`, `station_name`, `brand`, `address`,
  `station_code`, `fuel_type`, and `last_updated`.
- `sensor.*` — one per station, state is **that station's price**. Use these for
  history graphs and long-term statistics; the marker's history would only ever
  show distance. Attributes include `distance`, `latitude`, and `longitude`.
- `sensor.*_cheapest_*` — lowest price in range, with the winning station's details
  as attributes plus `stations_in_range`.

### Price history

```yaml
type: history-graph
hours_to_show: 336
entities:
  - sensor.nsw_fuel_map_unleaded_91_costco_auburn
  - sensor.nsw_fuel_map_unleaded_91_cheapest_unleaded_91
```

Stations that leave the radius become `unavailable` rather than being deleted, so a
station near the edge of your radius doesn't churn the entity registry.

## Services

`nsw_fuel_map.refresh` — fetch prices immediately, without waiting for the schedule.

Example automation, refreshing every morning at 6am:

```yaml
automation:
  - alias: Refresh fuel prices
    triggers:
      - trigger: time
        at: "06:00:00"
    actions:
      - action: nsw_fuel_map.refresh
```

## Example automation

Notify when fuel drops below a threshold:

```yaml
automation:
  - alias: Cheap fuel alert
    triggers:
      - trigger: numeric_state
        entity_id: sensor.nsw_fuel_map_unleaded_91_cheapest_unleaded_91
        below: 175
    actions:
      - action: notify.mobile_app
        data:
          message: >-
            {{ state_attr(trigger.entity_id, 'station_name') }} has U91 at
            {{ states(trigger.entity_id) }}c/L,
            {{ state_attr(trigger.entity_id, 'distance') }}km away.
```

## Development

Verify the API end to end against your real credentials:

```bash
cp .env.example .env   # then fill in your key and secret
python scripts/smoke_test.py
```

Run the test suite (needs Python 3.13+, which is what current Home Assistant targets):

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
```

**Never commit `.env`** — it is gitignored. If your key is ever exposed, rotate it
in the NSW API portal.

## Notes

- FuelCheck price data updates through the day; a 24-hour poll is plenty for a
  "where should I fill up" map. Shorten the interval if you want fresher data — the
  API's rate limits are generous.
- Only NSW (and Tasmanian) stations are covered; this is what the API publishes.
