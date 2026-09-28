# Wildfire Sentinel 2.0

Ingests NASA FIRMS satellite fire detections into PostgreSQL, groups them into
fire events with DBSCAN, and serves them through a FastAPI REST API. The whole
stack runs with Docker Compose.

**Status: in development.** Ingestion, database, fire-event clustering, REST API
and Docker are complete. A web dashboard and hosting are next. Nothing here is
production-hardened and it is not described as such.

## Run with Docker (recommended)

Needs Docker Desktop and a free NASA FIRMS MAP_KEY.

```bash
cp .env.example .env               # put your key after NASA_FIRMS_MAP_KEY=
docker compose up -d --build       # starts PostgreSQL + the API
docker compose run --rm ingest     # fetches the last day of world fire data
```



| Endpoint | Returns |
|---|---|
| `GET /health` | API and database status |
| `GET /fires?days=1` | Detections grouped into fire events, largest total FRP first |
| `GET /fires/top?n=10` | The n biggest fire events |
| `GET /detections?days=1&bbox=...` | Raw satellite detections, newest first |
| `GET /stats/daily?days=7` | Detections and total FRP per day and satellite (SQL aggregate) |
| `GET /ingest-runs` | Recent ingest runs, to check data freshness |

`bbox` is `lon_min,lat_min,lon_max,lat_max`. Stop with `docker compose down`
(`-v` also deletes the stored data).

## Quick map without a database

```bash
pip install -r requirements.txt
cp .env.example .env          # then put your key after NASA_FIRMS_MAP_KEY=
python scripts/quick_map.py   # whole world, last 24 hours
```

Open `output/fire_map.html` in a browser. `output/fire_events.csv` has one row per fire event.

## What exists today

- `migrations/001_init.sql` — the v2 schema, applied by `scripts/migrate.py`.
- `app/ingestion/` — FIRMS client, CSV parser, validator, pipeline.
- `app/database/` — batched loader and connection helper.
- `scripts/run_ingest.py` — the only production ingestion entry point.
- `app/analysis/clustering.py` — groups detections into fire events (DBSCAN, scikit-learn).
- `scripts/cluster_fires.py` — prints the largest fire events from stored data.
- `scripts/quick_map.py` — fetches live NASA data and draws a world fire map (no database).
- `app/api/` — FastAPI app and pydantic response models.
- `Dockerfile`, `docker-compose.yml` — PostgreSQL + API containers and a one-off ingest job.
- `tests/` — 75 tests. No test contacts NASA or writes to a production database.

## Data source and semantics

Data comes from the NASA FIRMS area/csv API. Supported products:
`VIIRS_NOAA20_NRT`, `VIIRS_SNPP_NRT`, `VIIRS_NOAA21_NRT`.

Fields are stored as FIRMS supplies them. Missing values are stored as NULL and
are never defaulted or interpolated; a row with a missing or unparseable
required field is rejected and recorded in `ingest_rejects` with the reason.

**NASA-provided fields:** latitude, longitude, brightness (`bright_ti4`),
secondary brightness (`bright_ti5`), scan, track, acquisition date/time (UTC),
satellite, instrument, confidence, version, FRP, day/night flag.

**Fields derived by this project:** `product` (the FIRMS product key requested),
`source`, `ingest_run_id`, `ingested_at`, `brightness_channel` and
`brightness_secondary_channel` (the FIRMS column each brightness value came
from), and `acquired_at` (a generated column combining `acq_date` and
`acq_time_utc`). No risk, severity or confidence score is derived — see
`docs/DISCARDED_V1_CONFIDENCE.md`.

## Deduplication is observation-level, not fire-level

The `uq_observation` constraint covers
`(product, satellite, acq_date, acq_time_utc, latitude, longitude)` and inserts
use `ON CONFLICT DO NOTHING`. This makes re-fetching an overlapping day range
idempotent.

It does **not** cluster fires. Two adjacent pixels burning in the same fire are
two distinct observations and both are kept; a later overpass of the same fire
is a new observation and is kept.

## Fire-event clustering

FIRMS gives one row per hot pixel per satellite pass, so row counts over-count fires.
`cluster_detections()` groups rows into fire events in two steps:

1. **Space:** DBSCAN with haversine distance; detections within `eps_km` (default 1 km)
   chain into one cluster. `min_samples=1`, so a lone pixel is a small event, not noise.
2. **Time:** within a cluster, a gap longer than `max_gap_hours` (default 24 h) starts a
   new event.

Both defaults are tunable assumptions. Events are estimates, not official fire perimeters;
`pixel_footprint_km2` is summed pixel area, not burned area. A world-scale day
(~75k synthetic detections) is loaded, clustered and returned by `/fires` in about 2 seconds.

```bash
python scripts/cluster_fires.py --days 1 --csv events.csv
```

## Setup without Docker

```bash
cp .env.example .env       # then fill in DATABASE_URL, NASA_FIRMS_MAP_KEY, FIRMS_AREA
pip install -r requirements-dev.txt
python scripts/migrate.py
python scripts/run_ingest.py --area <bbox-or-world> --day-range 1 --json
uvicorn app.api.main:app --reload     # API on http://localhost:8000
```

`FIRMS_AREA` has no default. A world VIIRS query returns tens of thousands of
rows per day and consumes a meaningful share of the FIRMS transaction quota.

## Tests

```bash
python -m pytest                                  # unit tests only
TEST_DATABASE_URL=postgresql://... python -m pytest   # includes database tests
```

Database tests skip cleanly when `TEST_DATABASE_URL` is unset. Synthetic
fixtures live only under `tests/fixtures/` and are labelled there.

## Known limitations

- The FIRMS client has never been run against the live NASA endpoint; it is
  covered by mocked HTTP tests only.
- MODIS is not supported yet. The schema accommodates it; the product registry
  does not list it.
- No web dashboard, hosting or CI yet.
- `/fires` clusters on every request. Fine for a few days of world data; a longer
  history would need events precomputed and stored.
