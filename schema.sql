-- =============================================================================
-- LEGO Deals FR - database schema v1
--
-- Target: SQLite (local dev) and libSQL / Turso (production).
-- Reference document, kept in sync with the Alembic migrations by hand.
--
-- Six tables. If this ever grows past eight, something went wrong in the
-- design. Each table is described in one sentence below.
--
--   sets           The LEGO catalogue. Reference data, no current prices.
--   offers         A set on sale somewhere, right now.
--   price_points   Every price we have ever observed. Append-only.
--   alerts         Every deal message we have actually sent.
--   health_alerts  Every "the pipeline looks dead" warning we have sent.
--   runs           Every pipeline execution, successful or not.
--
-- Conventions:
--   - All timestamps are UTC, stored as ISO 8601 text.
--   - All prices are in euros, stored as REAL.
--   - Booleans are INTEGER 0/1 (SQLite has no native boolean).
--   - Every constraint is named, following the SQLAlchemy naming convention
--     (pk_, uq_, fk_). SQLite would happily leave them anonymous, but then
--     Alembic cannot alter one by name and has to guess. Primary keys are
--     spelled NOT NULL explicitly: SQLite lets a NULL into a TEXT PRIMARY KEY
--     otherwise, which is a long-standing quirk we do not want to inherit.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- sets: the LEGO catalogue.
--
-- Populated from Rebrickable CSV dumps (identity, theme, piece count) enriched
-- with Brickset data (recommended retail price). Refreshed weekly, never by the
-- ingestion pipeline. A row here is a fact about a product, not about a price
-- on a given day.
-- -----------------------------------------------------------------------------
CREATE TABLE sets (
    -- Official LEGO set number including variant suffix, e.g. "10497-1".
    set_num          TEXT NOT NULL,

    name             TEXT NOT NULL,

    -- Lowercased, accent-stripped, punctuation-free version of `name`.
    -- Precomputed at import time because fuzzy resolution runs against it on
    -- every single offer.
    name_normalized  TEXT NOT NULL,

    theme            TEXT,
    year             INTEGER,
    pieces           INTEGER,

    -- Recommended retail price in euros. NULL when unknown, which means this
    -- set can never trigger a discount-threshold alert (it can still trigger
    -- an all-time-low alert).
    rrp_eur          REAL,

    image_url        TEXT,
    updated_at       TEXT NOT NULL,

    CONSTRAINT pk_sets PRIMARY KEY (set_num)
);

CREATE INDEX idx_sets_name_normalized ON sets (name_normalized);
CREATE INDEX idx_sets_theme           ON sets (theme);


-- -----------------------------------------------------------------------------
-- offers: a set on sale, at a merchant, at a URL.
--
-- One row per deal seen at a source. The current price lives here for
-- convenience; the full history lives in price_points.
--
-- `set_num` is nullable on purpose: an offer we could not resolve confidently
-- is still worth storing. It just never produces an alert.
-- -----------------------------------------------------------------------------
CREATE TABLE offers (
    id                     INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,

    set_num                TEXT,

    -- How sure we are about set_num, between 0 and 1. NULL when unresolved.
    resolution_score       REAL,

    -- 'set_number' | 'fuzzy_name' | 'manual' | NULL
    resolution_method      TEXT,

    -- Which Source produced this, e.g. 'dealabs'.
    source                 TEXT NOT NULL,

    -- The source's own identifier for this deal. Together with `source` this
    -- is what makes deduplication possible across runs.
    external_id            TEXT NOT NULL,

    merchant               TEXT,

    -- The untouched title as published. Never overwrite it: when resolution
    -- misbehaves, this is the evidence.
    title_raw              TEXT NOT NULL,

    url                    TEXT NOT NULL,

    current_price_eur      REAL,

    first_seen_at          TEXT NOT NULL,
    last_seen_at           TEXT NOT NULL,

    -- 0 once the deal is gone or expired at the source.
    is_active              INTEGER NOT NULL DEFAULT 1,

    CONSTRAINT uq_offers_source_external_id UNIQUE (source, external_id),
    CONSTRAINT fk_offers_set_num_sets FOREIGN KEY (set_num) REFERENCES sets (set_num)
);

CREATE INDEX idx_offers_set_num ON offers (set_num);
CREATE INDEX idx_offers_active  ON offers (is_active, last_seen_at);


-- -----------------------------------------------------------------------------
-- price_points: append-only price history.
--
-- The most valuable table in the database and the only one nobody else could
-- rebuild after the fact. Never UPDATE, never DELETE. One row per observation,
-- even when the price has not moved.
-- -----------------------------------------------------------------------------
CREATE TABLE price_points (
    id           INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    offer_id     INTEGER NOT NULL,
    price_eur    REAL NOT NULL,
    observed_at  TEXT NOT NULL,

    CONSTRAINT fk_price_points_offer_id_offers FOREIGN KEY (offer_id) REFERENCES offers (id)
);

CREATE INDEX idx_price_points_offer ON price_points (offer_id, observed_at);


-- -----------------------------------------------------------------------------
-- alerts: Discord messages actually sent.
--
-- Exists so the anti-spam rules have something to read: no second alert for the
-- same offer within 24h, and no new alert unless the price dropped at least 5%
-- since the last one.
-- -----------------------------------------------------------------------------
CREATE TABLE alerts (
    id            INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    offer_id      INTEGER NOT NULL,

    -- Unused in v1 (single server). Present now because adding it later would
    -- mean a migration on a table that will already be large.
    guild_id      TEXT,

    channel_id    TEXT NOT NULL,

    -- Snapshot of the price at send time. Denormalised on purpose: what we
    -- announced must stay readable even if the offer changes afterwards.
    price_eur     REAL NOT NULL,
    discount_pct  REAL,

    -- 'discount_threshold' | 'all_time_low'
    reason        TEXT NOT NULL,

    sent_at       TEXT NOT NULL,

    CONSTRAINT fk_alerts_offer_id_offers FOREIGN KEY (offer_id) REFERENCES offers (id)
);

CREATE INDEX idx_alerts_offer ON alerts (offer_id, sent_at);


-- -----------------------------------------------------------------------------
-- health_alerts: warnings sent because a source stopped producing.
--
-- Kept apart from `alerts` rather than sharing it: an alert is about an offer
-- and its offer_id is NOT NULL, whereas this is about a source that has gone
-- quiet and refers to no offer at all. Forcing them into one table would mean
-- a nullable foreign key and a reason column meaning two different things.
--
-- Exists for one purpose: the "no more than one warning per 24h" rule needs
-- to remember when the last one went out.
-- -----------------------------------------------------------------------------
CREATE TABLE health_alerts (
    id          INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,

    -- Which source went quiet, e.g. 'dealabs'.
    source      TEXT NOT NULL,

    -- 'no_items' | 'failing' | 'stale'
    --
    -- 'stale' is the one nobody observed: a run cancelled before it started
    -- writes no row to `runs`, so no streak can count it. It is inferred from
    -- how long ago the last success was.
    reason      TEXT NOT NULL,

    sent_at     TEXT NOT NULL
);

CREATE INDEX idx_health_alerts_source ON health_alerts (source, sent_at);


-- -----------------------------------------------------------------------------
-- runs: one row per pipeline execution.
--
-- This is the observability table. Without it, a parser can break and stay
-- broken for weeks without anyone noticing. A run that crashes still writes
-- its row, with status 'error' and the message.
-- -----------------------------------------------------------------------------
CREATE TABLE runs (
    id              INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,

    started_at      TEXT NOT NULL,
    finished_at     TEXT,

    items_found     INTEGER NOT NULL DEFAULT 0,
    items_new       INTEGER NOT NULL DEFAULT 0,
    items_resolved  INTEGER NOT NULL DEFAULT 0,
    alerts_sent     INTEGER NOT NULL DEFAULT 0,

    -- 'running' | 'ok' | 'error'
    status          TEXT NOT NULL,
    error           TEXT
);

CREATE INDEX idx_runs_source ON runs (source, started_at);
