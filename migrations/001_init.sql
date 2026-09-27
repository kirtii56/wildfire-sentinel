-- 001_init.sql — Wildfire Sentinel 2.0 initial schema.
--
-- Provenance: every fire_detections row points at the ingest_runs row that
-- produced it. Nothing is written to these tables except data received from
-- NASA FIRMS by scripts/run_ingest.py.
--
-- Deduplication here is OBSERVATION-LEVEL ONLY (see uq_observation below).

BEGIN;

CREATE TABLE ingest_runs (
    id              BIGSERIAL   PRIMARY KEY,
    source          TEXT        NOT NULL DEFAULT 'nasa_firms',
    product         TEXT        NOT NULL,
    area            TEXT        NOT NULL,
    day_range       SMALLINT    NOT NULL CHECK (day_range BETWEEN 1 AND 10),
    request_date    DATE,
    request_url     TEXT        NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT        NOT NULL CHECK (status IN ('running','success','partial','failed')),
    rows_fetched    INTEGER     NOT NULL DEFAULT 0,
    rows_accepted   INTEGER     NOT NULL DEFAULT 0,
    rows_rejected   INTEGER     NOT NULL DEFAULT 0,
    rows_inserted   INTEGER     NOT NULL DEFAULT 0,
    rows_duplicate  INTEGER     NOT NULL DEFAULT 0,
    error_detail    TEXT
);

COMMENT ON COLUMN ingest_runs.request_url IS
    'FIRMS request URL with the MAP_KEY replaced by REDACTED.';

CREATE TABLE fire_detections (
    id                           BIGSERIAL     PRIMARY KEY,
    ingest_run_id                BIGINT        NOT NULL REFERENCES ingest_runs(id),
    source                       TEXT          NOT NULL DEFAULT 'nasa_firms',
    product                      TEXT          NOT NULL,
    instrument                   TEXT          NOT NULL,
    satellite                    TEXT          NOT NULL,
    version                      TEXT,
    latitude                     NUMERIC(9,6)  NOT NULL CHECK (latitude  BETWEEN -90  AND 90),
    longitude                    NUMERIC(9,6)  NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    acq_date                     DATE          NOT NULL,
    acq_time_utc                 TIME          NOT NULL,
    acquired_at                  TIMESTAMPTZ   GENERATED ALWAYS AS
                                     ((acq_date + acq_time_utc) AT TIME ZONE 'UTC') STORED,
    brightness_k                 NUMERIC(6,2),
    brightness_channel           TEXT CHECK (brightness_channel IN ('bright_ti4','brightness')),
    brightness_secondary_k       NUMERIC(6,2),
    brightness_secondary_channel TEXT CHECK (brightness_secondary_channel IN ('bright_ti5','bright_t31')),
    frp_mw                       NUMERIC(10,2) CHECK (frp_mw >= 0),
    scan_km                      NUMERIC(6,3),
    track_km                     NUMERIC(6,3),
    confidence_text              TEXT     CHECK (confidence_text IN ('low','nominal','high')),
    confidence_pct               SMALLINT CHECK (confidence_pct BETWEEN 0 AND 100),
    daynight                     CHAR(1)  CHECK (daynight IN ('D','N')),
    ingested_at                  TIMESTAMPTZ   NOT NULL DEFAULT now(),

    -- NASA attributes confidence differently per product: VIIRS is categorical
    -- (low/nominal/high), MODIS is a 0-100 percentage. Exactly one is populated;
    -- they are never converted into each other or into a single score.
    CONSTRAINT confidence_exactly_one
        CHECK (num_nonnulls(confidence_text, confidence_pct) = 1),

    -- A brightness value is meaningless without knowing which channel produced it.
    CONSTRAINT brightness_channel_present
        CHECK ((brightness_k IS NULL) = (brightness_channel IS NULL)),
    CONSTRAINT brightness_secondary_channel_present
        CHECK ((brightness_secondary_k IS NULL) = (brightness_secondary_channel IS NULL)),

    -- OBSERVATION-LEVEL idempotency: one satellite pixel, one overpass, one
    -- centroid. This makes re-fetching overlapping day ranges safe. It does NOT
    -- cluster fires: adjacent pixels and later overpasses are distinct real
    -- observations and are all kept.
    CONSTRAINT uq_observation
        UNIQUE (product, satellite, acq_date, acq_time_utc, latitude, longitude)
);

CREATE INDEX idx_fd_acquired_at ON fire_detections (acquired_at DESC);
CREATE INDEX idx_fd_bbox        ON fire_detections (latitude, longitude);
CREATE INDEX idx_fd_product     ON fire_detections (product, acq_date);
CREATE INDEX idx_fd_frp         ON fire_detections (frp_mw DESC NULLS LAST);
CREATE INDEX idx_fd_run         ON fire_detections (ingest_run_id);

CREATE TABLE ingest_rejects (
    id             BIGSERIAL PRIMARY KEY,
    ingest_run_id  BIGINT    NOT NULL REFERENCES ingest_runs(id),
    row_number     INTEGER   NOT NULL,
    reason         TEXT      NOT NULL,
    raw_row        JSONB     NOT NULL
);

CREATE INDEX idx_rejects_run ON ingest_rejects (ingest_run_id);

COMMIT;
