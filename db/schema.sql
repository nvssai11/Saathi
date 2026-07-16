-- Saathi v0 schema — PostgreSQL 15+
-- Append-only tables use INSERT-only grants; UPDATE/DELETE are never issued.
-- All monetary columns are NUMERIC(12,2) — no FLOAT.

-- ── Extensions ────────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- for gen_random_uuid() if needed

-- ── Enums ─────────────────────────────────────────────────────────────────────

CREATE TYPE order_status AS ENUM (
    'PENDING',
    'ALLOCATING',
    'ALLOCATED',
    'IN_PRODUCTION',
    'VERIFYING',
    'FACTORY_FALLBACK',
    'SETTLING',
    'CLOSED',
    'FAILED',
    'CANCELLED'
);

CREATE TYPE sublot_status AS ENUM (
    'ASSIGNED',
    'IN_PRODUCTION',
    'DELIVERED',
    'VERIFYING',
    'VERIFIED',
    'FAILED',
    'NEEDS_HUMAN_REVIEW'
);

CREATE TYPE verification_verdict AS ENUM (
    'OK',
    'DEFECT',
    'SPEC_AMBIGUITY'
);

CREATE TYPE fault_party AS ENUM (
    'workshop',
    'buyer',
    'none'
);

CREATE TYPE trust_event_type AS ENUM (
    'DELIVERY_ON_TIME',
    'DELIVERY_LATE',
    'DEFECT_WORKSHOP',
    'DEFECT_BUYER',
    'SPEC_AMBIGUITY'
);

-- ── Workshops ─────────────────────────────────────────────────────────────────

CREATE TABLE workshops (
    workshop_id      SERIAL PRIMARY KEY,
    name             TEXT        NOT NULL,
    quality_tier     SMALLINT    NOT NULL CHECK (quality_tier BETWEEN 1 AND 5),
    is_factory       BOOLEAN     NOT NULL DEFAULT FALSE,
    spec_disputes    INTEGER     NOT NULL DEFAULT 0 CHECK (spec_disputes >= 0),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE workshop_capacity (
    workshop_id    INTEGER     NOT NULL REFERENCES workshops(workshop_id),
    product_type   TEXT        NOT NULL,
    available_qty  INTEGER     NOT NULL CHECK (available_qty >= 0),
    reserved_qty   INTEGER     NOT NULL DEFAULT 0 CHECK (reserved_qty >= 0),
    cost_per_unit  NUMERIC(12, 2) NOT NULL CHECK (cost_per_unit > 0),
    lead_time_days SMALLINT    NOT NULL CHECK (lead_time_days > 0),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workshop_id, product_type),
    CONSTRAINT reserved_lte_available CHECK (reserved_qty <= available_qty)
);

-- ── Trust ─────────────────────────────────────────────────────────────────────
-- Append-only. No UPDATE or DELETE grants should be given to the app role.

CREATE TABLE trust_events (
    trust_event_id SERIAL      PRIMARY KEY,
    workshop_id    INTEGER     NOT NULL REFERENCES workshops(workshop_id),
    sublot_id      INTEGER     NOT NULL UNIQUE,   -- FK set after sublots table created
    event_type     trust_event_type NOT NULL,
    on_time        BOOLEAN     NOT NULL,
    defect_found   BOOLEAN     NOT NULL,
    fault_party    fault_party NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- UNIQUE(sublot_id): each sublot resolves to exactly one verification
-- outcome (auto-OK or agent-verified), so it must produce exactly one trust
-- event — same idempotency idiom as verification_results/payments below.
-- Without it, a Kafka redelivery after a crash between "sublot -> VERIFIED"
-- and "trust event recorded" permanently orphans the trust event: the
-- redelivered on_sublot_delivered call sees the sublot already VERIFIED and
-- never re-attempts recording it (TrustRepository.append_event mirrors this
-- with ON CONFLICT (sublot_id) DO NOTHING).

-- ── Orders ────────────────────────────────────────────────────────────────────

CREATE TABLE orders (
    order_id               SERIAL       PRIMARY KEY,
    correlation_id         UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    buyer_ref              TEXT         NOT NULL,
    product_type           TEXT         NOT NULL,
    total_qty              INTEGER      NOT NULL CHECK (total_qty > 0),
    quality_min            SMALLINT     NOT NULL CHECK (quality_min BETWEEN 1 AND 5),
    deadline               DATE         NOT NULL,
    factory_fallback_cost  NUMERIC(12, 2) NOT NULL CHECK (factory_fallback_cost > 0),
    factory_workshop_id    INTEGER      NOT NULL REFERENCES workshops(workshop_id),
    status                 order_status NOT NULL DEFAULT 'PENDING',
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_orders_status     ON orders(status);
CREATE INDEX idx_orders_correlation ON orders(correlation_id);

-- ── Sub-lots ──────────────────────────────────────────────────────────────────

CREATE TABLE sublots (
    sublot_id      SERIAL        PRIMARY KEY,
    order_id       INTEGER       NOT NULL REFERENCES orders(order_id),
    workshop_id    INTEGER       NOT NULL REFERENCES workshops(workshop_id),
    qty_assigned   INTEGER       NOT NULL CHECK (qty_assigned > 0),
    delivered_qty  INTEGER                CHECK (delivered_qty >= 0),
    cost_per_unit  NUMERIC(12, 2) NOT NULL CHECK (cost_per_unit > 0),
    status         sublot_status NOT NULL DEFAULT 'ASSIGNED',
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),
    -- Tracks whether workshop_capacity.reserved_qty has been released for
    -- this sublot, independent of `status`. `status` is not a safe signal
    -- for this: the delivery API route writes status='DELIVERED' directly
    -- and synchronously (api/routes/workshop.py, "DB first, then Kafka"),
    -- ahead of the Kafka event that triggers OrderCoordinator.on_sublot_delivered.
    -- By the time that handler runs, status already reads DELIVERED, so a
    -- guard of the form `status IN ('ASSIGNED','IN_PRODUCTION')` is always
    -- false — indistinguishable from a genuine Kafka replay — and capacity
    -- release/shortfall-backfill would silently never fire. This column is
    -- the coordinator's own idempotency marker (same UNIQUE-guard idiom as
    -- verification_results/payments/trust_events), set exactly once via an
    -- atomic UPDATE ... WHERE capacity_released_at IS NULL.
    capacity_released_at TIMESTAMPTZ,
    CONSTRAINT delivered_lte_assigned CHECK (delivered_qty IS NULL OR delivered_qty <= qty_assigned)
);

CREATE INDEX idx_sublots_order_id ON sublots(order_id);

ALTER TABLE trust_events
    ADD CONSTRAINT fk_trust_sublot FOREIGN KEY (sublot_id) REFERENCES sublots(sublot_id);

-- ── Verification results ──────────────────────────────────────────────────────
-- Append-only. One row per verified sublot.

CREATE TABLE verification_results (
    verification_id SERIAL               PRIMARY KEY,
    sublot_id       INTEGER              NOT NULL UNIQUE REFERENCES sublots(sublot_id),
    verdict         verification_verdict NOT NULL,
    fault_party     fault_party          NOT NULL,
    confidence      NUMERIC(4, 3)        NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    explanation     TEXT                 NOT NULL,
    photo_path      TEXT,
    created_at      TIMESTAMPTZ          NOT NULL DEFAULT now()
);

-- ── Payments ─────────────────────────────────────────────────────────────────
-- Written once per sublot after settlement runs.

CREATE TABLE payments (
    payment_id   SERIAL        PRIMARY KEY,
    order_id     INTEGER       NOT NULL REFERENCES orders(order_id),
    workshop_id  INTEGER       NOT NULL REFERENCES workshops(workshop_id),
    sublot_id    INTEGER       NOT NULL UNIQUE REFERENCES sublots(sublot_id),
    base_amount  NUMERIC(12, 2) NOT NULL,
    penalty      NUMERIC(12, 2) NOT NULL DEFAULT 0,
    net_amount   NUMERIC(12, 2) NOT NULL,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX idx_payments_order_id ON payments(order_id);

-- ── Notifications ────────────────────────────────────────────────────────────
-- Populated by NotificationWorker consuming saathi.sublot.assigned. One row
-- per sub-lot assignment (never for the factory workshop — it's the trusted
-- backstop, not a member workshop that needs to be told). UNIQUE(sublot_id)
-- makes the consumer idempotent under Kafka at-least-once redelivery, same
-- pattern as trust_events/verification_results.

CREATE TABLE notifications (
    notification_id SERIAL        PRIMARY KEY,
    workshop_id      INTEGER      NOT NULL REFERENCES workshops(workshop_id),
    order_id         INTEGER      NOT NULL REFERENCES orders(order_id),
    sublot_id        INTEGER      NOT NULL UNIQUE REFERENCES sublots(sublot_id),
    product_type     TEXT         NOT NULL,
    qty_assigned     INTEGER      NOT NULL CHECK (qty_assigned > 0),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_notifications_workshop ON notifications(workshop_id, created_at DESC);

-- ── Trust score cache (derived, recomputable) ─────────────────────────────────
-- Materialised view updated nightly or after each trust event write.
-- Never the source of truth — trust_events are.

CREATE TABLE trust_score_cache (
    workshop_id  INTEGER     PRIMARY KEY REFERENCES workshops(workshop_id),
    score        NUMERIC(6, 4) NOT NULL,
    grade        CHAR(1)     NOT NULL,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Row-level security (app role: saathi_app) ─────────────────────────────────
-- The app role gets SELECT/INSERT on most tables but never UPDATE/DELETE on
-- trust_events, verification_results, payments (append-only guarantee).

-- CREATE ROLE saathi_app LOGIN PASSWORD 'changeme';
-- GRANT SELECT, INSERT, UPDATE ON orders, sublots, workshop_capacity, trust_score_cache TO saathi_app;
-- GRANT SELECT, INSERT ON trust_events, verification_results, payments TO saathi_app;
-- GRANT SELECT ON workshops TO saathi_app;
-- GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO saathi_app;
