# Wildfire Sentinel

Pulls live NASA FIRMS satellite fire detections for the whole world, cleans and
validates them, and stores them in PostgreSQL. A FastAPI REST API serves the
detections and groups hot pixels into fire events with DBSCAN (scikit-learn)
on each request.

**Stack:** Python · Pandas · NumPy · scikit-learn · PostgreSQL · FastAPI · Plotly · pytest

**Status: in development.** Ingestion, validation, database, fire-event
clustering, REST API and a world fire map are working. Hosting is next.

## How it works

```
NASA FIRMS API ──> fetch ──> parse CSV ──> validate ──> PostgreSQL
                                                          │
                          fire map (Plotly) <── cluster into events (DBSCAN)
                                                          │
                                                   FastAPI REST API
```

1. **Fetch** — `app/ingestion/firms_client.py` downloads VIIRS fire detections as CSV.
2. **Parse + validate** — `parser.py` and `validation.py` turn rows into typed records.
   Bad rows are rejected with a reason, never silently fixed.
3. **Store** — `app/database/loader.py` inserts in batches; re-running the same day
   does not create duplicates.
4. **Cluster** — `app/analysis/clustering.py` groups nearby pixels into fire events.
5. **Serve** — `app/api/main.py` exposes the data as JSON endpoints.

## Live map

A GitHub Actions workflow (`.github/workflows/fire-map.yml`) runs `quick_map.py`
every day on GitHub's servers and publishes the result with GitHub Pages.
It needs the NASA key stored as a repository secret named `NASA_FIRMS_MAP_KEY`.

## Quick start: world fire map (no database)

Needs Python 3.11+ and a free NASA FIRMS MAP_KEY
(https://firms.modaps.eosdis.nasa.gov/api/area/).

```bash
pip install -r requirements.txt
cp .env.example .env          # put your key after NASA_FIRMS_MAP_KEY=
python scripts/quick_map.py   # whole world, last 24 hours
```

Open `output/fire_map.html` in a browser. `output/fire_events.csv` has one row per fire event.

## Full pipeline: database + API

Needs a PostgreSQL database — either installed locally or a free cloud one
(Supabase or Neon). Put its connection string in `.env` as `DATABASE_URL`.

```bash
pip install -r requirements-dev.txt
python scripts/migrate.py                                   # create the tables
python scripts/run_ingest.py --area world --day-range 1     # load NASA data
uvicorn app.api.main:app --reload                           # API on http://localhost:8000
```

Interactive API docs: http://localhost:8000/docs

| Endpoint | Returns |
|---|---|
| `GET /health` | API and database status |
| `GET /fires?days=1` | Detections grouped into fire events, largest total FRP first |
| `GET /fires/top?n=10` | The n biggest fire events |
| `GET /detections?days=1&bbox=...` | Raw satellite detections, newest first |
| `GET /stats/daily?days=7` | Detections and total FRP per day and satellite (SQL aggregate) |
| `GET /ingest-runs` | Recent ingest runs, to check data freshness |

`bbox` is `lon_min,lat_min,lon_max,lat_max`.

A world VIIRS query returns tens of thousands of rows per day and uses part of
the FIRMS daily quota, so start with a small bounding box while testing.

## Project layout

- `migrations/001_init.sql` — database schema, applied by `scripts/migrate.py`.
- `app/ingestion/` — FIRMS client, CSV parser, validator, pipeline.
- `app/database/` — batched loader and connection helper.
- `app/analysis/` — SQL queries and fire-event clustering.
- `app/api/` — FastAPI app and pydantic response models.
- `scripts/` — command-line entry points (ingest, migrate, cluster, quick map).
- `tests/` — 75 tests (unit + database). No test contacts NASA.

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

## Tests

```bash
python -m pytest                                  # unit tests only
TEST_DATABASE_URL=postgresql://... python -m pytest   # all 75, needs a test database
```

Database tests skip cleanly when `TEST_DATABASE_URL` is unset. Synthetic
fixtures live only under `tests/fixtures/` and are labelled there.

## Known limitations

- The FIRMS client has never been run against the live NASA endpoint; it is
  covered by mocked HTTP tests only.
- MODIS is not supported yet. The schema accommodates it; the product registry
  does not list it.
- The live map is a static page rebuilt once a day; the API itself is not hosted yet.
- `/fires` clusters on every request. Fine for a few days of world data; a longer
  history would need events precomputed and stored.
