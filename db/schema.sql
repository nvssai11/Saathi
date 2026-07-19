CREATE EXTENSION IF NOT EXISTS pgcrypto;

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
    'NEEDS_HUMAN_REVIEW',
    'CANCELLED'
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

CREATE TABLE workshops (
    workshop_id      SERIAL PRIMARY KEY,
    name             TEXT        NOT NULL,
    quality_tier     SMALLINT    NOT NULL CHECK (quality_tier BETWEEN 1 AND 5),
    is_factory       BOOLEAN     NOT NULL DEFAULT FALSE,
    spec_disputes    INTEGER     NOT NULL DEFAULT 0 CHECK (spec_disputes >= 0),
    phone_number     TEXT        UNIQUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE login_otps (
    otp_id       SERIAL      PRIMARY KEY,
    phone_number TEXT        NOT NULL,
    code_hash    TEXT        NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL,
    attempts     SMALLINT    NOT NULL DEFAULT 0,
    consumed_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_login_otps_phone_active
    ON login_otps (phone_number, created_at DESC)
    WHERE consumed_at IS NULL;

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

CREATE TABLE trust_events (
    trust_event_id SERIAL      PRIMARY KEY,
    workshop_id    INTEGER     NOT NULL REFERENCES workshops(workshop_id),
    sublot_id      INTEGER     NOT NULL,
    event_type     trust_event_type NOT NULL,
    on_time        BOOLEAN     NOT NULL,
    defect_found   BOOLEAN     NOT NULL,
    fault_party    fault_party NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_trust_events_sublot_id ON trust_events(sublot_id);

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
    delivered_at   TIMESTAMPTZ,
    capacity_released_at TIMESTAMPTZ,
    CONSTRAINT delivered_lte_assigned CHECK (delivered_qty IS NULL OR delivered_qty <= qty_assigned)
);

CREATE INDEX idx_sublots_order_id ON sublots(order_id);

ALTER TABLE trust_events
    ADD CONSTRAINT fk_trust_sublot FOREIGN KEY (sublot_id) REFERENCES sublots(sublot_id);

CREATE TABLE verification_results (
    verification_id SERIAL               PRIMARY KEY,
    sublot_id       INTEGER              NOT NULL REFERENCES sublots(sublot_id),
    verdict         verification_verdict NOT NULL,
    fault_party     fault_party          NOT NULL,
    confidence      NUMERIC(4, 3)        NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    explanation     TEXT                 NOT NULL,
    explanations    JSONB                NOT NULL DEFAULT '{}'::jsonb,
    photo_path      TEXT,
    created_at      TIMESTAMPTZ          NOT NULL DEFAULT now()
);
CREATE INDEX idx_verification_results_sublot_id ON verification_results(sublot_id);

CREATE TABLE payments (
    payment_id   SERIAL        PRIMARY KEY,
    order_id     INTEGER       NOT NULL REFERENCES orders(order_id),
    workshop_id  INTEGER       NOT NULL REFERENCES workshops(workshop_id),
    sublot_id    INTEGER       NOT NULL UNIQUE REFERENCES sublots(sublot_id),
    base_amount  NUMERIC(12, 2) NOT NULL,
    penalty      NUMERIC(12, 2) NOT NULL DEFAULT 0,
    net_amount   NUMERIC(12, 2) NOT NULL,
    buyer_billable_amount NUMERIC(12, 2) NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX idx_payments_order_id ON payments(order_id);

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

CREATE TABLE trust_score_cache (
    workshop_id  INTEGER     PRIMARY KEY REFERENCES workshops(workshop_id),
    score        NUMERIC(6, 4) NOT NULL,
    grade        CHAR(1)     NOT NULL,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
