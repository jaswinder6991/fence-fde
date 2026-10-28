-- Starfleet facility pipeline schema (v1)

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id              BIGSERIAL PRIMARY KEY,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,
    receivables_path    TEXT NOT NULL,
    collections_path    TEXT NOT NULL,
    output_dir          TEXT,
    status              TEXT NOT NULL DEFAULT 'running',
    current_stage       TEXT NOT NULL DEFAULT 'start',
    error               TEXT,
    timings             JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS receivable_events (
    event_id                BIGSERIAL PRIMARY KEY,
    run_id                  BIGINT NOT NULL REFERENCES pipeline_runs(run_id),
    ingested_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_number              INTEGER,
    receivable_external_id  TEXT,
    merchant_name           TEXT,
    obligor_name            TEXT,
    sector_raw              TEXT,
    sector                  TEXT,
    purchase_date           DATE,
    face_value              NUMERIC(18, 2),
    purchase_price          NUMERIC(18, 2),
    number_of_installments  INTEGER,
    currency                TEXT,
    obligor_risk_score      INTEGER,
    outstanding_balance     NUMERIC(18, 2),
    days_past_due           INTEGER,
    parse_ok                BOOLEAN NOT NULL,
    parse_error             TEXT
);

CREATE INDEX IF NOT EXISTS idx_receivable_events_run
    ON receivable_events (run_id);
CREATE INDEX IF NOT EXISTS idx_receivable_events_ext
    ON receivable_events (run_id, receivable_external_id);

CREATE TABLE IF NOT EXISTS payment_events (
    payment_id              TEXT PRIMARY KEY,
    run_id_first_seen       BIGINT NOT NULL REFERENCES pipeline_runs(run_id),
    receivable_external_id  TEXT NOT NULL,
    payment_date            DATE NOT NULL,
    amount                  NUMERIC(18, 2) NOT NULL,
    ingested_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_payment_events_recv
    ON payment_events (receivable_external_id);

CREATE TABLE IF NOT EXISTS receivables_current (
    receivable_external_id  TEXT PRIMARY KEY,
    active                  BOOLEAN NOT NULL DEFAULT TRUE,
    last_seen_run_id        BIGINT NOT NULL REFERENCES pipeline_runs(run_id),
    merchant_name           TEXT NOT NULL,
    obligor_name            TEXT NOT NULL,
    sector                  TEXT NOT NULL,
    purchase_date           DATE NOT NULL,
    face_value              NUMERIC(18, 2) NOT NULL,
    purchase_price          NUMERIC(18, 2) NOT NULL,
    number_of_installments  INTEGER NOT NULL,
    currency                TEXT NOT NULL,
    obligor_risk_score      INTEGER NOT NULL,
    outstanding_balance     NUMERIC(18, 2) NOT NULL,
    days_past_due           INTEGER NOT NULL,
    -- Pointer to the live installment plan; bumped when a correction rebuilds the schedule.
    current_schedule_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_receivables_current_active
    ON receivables_current (active)
    WHERE active;

CREATE TABLE IF NOT EXISTS installments (
    receivable_external_id  TEXT NOT NULL,
    schedule_version        INTEGER NOT NULL DEFAULT 1,
    seq                     INTEGER NOT NULL,
    due_date                DATE NOT NULL,
    amount_due              NUMERIC(18, 2) NOT NULL,
    amount_applied          NUMERIC(18, 2) NOT NULL DEFAULT 0,
    PRIMARY KEY (receivable_external_id, schedule_version, seq)
);

CREATE TABLE IF NOT EXISTS payment_allocations (
    payment_id              TEXT NOT NULL REFERENCES payment_events(payment_id),
    schedule_version        INTEGER NOT NULL DEFAULT 1,
    receivable_external_id  TEXT NOT NULL,
    installment_seq         INTEGER NOT NULL,
    amount                  NUMERIC(18, 2) NOT NULL,
    allocated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (payment_id, schedule_version, installment_seq),
    FOREIGN KEY (receivable_external_id, schedule_version, installment_seq)
        REFERENCES installments (receivable_external_id, schedule_version, seq)
);

CREATE INDEX IF NOT EXISTS idx_payment_allocations_recv
    ON payment_allocations (receivable_external_id, schedule_version, installment_seq);

CREATE TABLE IF NOT EXISTS payments_applied (
    payment_id      TEXT PRIMARY KEY REFERENCES payment_events(payment_id),
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    run_id          BIGINT NOT NULL REFERENCES pipeline_runs(run_id)
);

CREATE TABLE IF NOT EXISTS eligibility_results (
    run_id                      BIGINT NOT NULL REFERENCES pipeline_runs(run_id),
    receivable_external_id      TEXT NOT NULL,
    status                      TEXT NOT NULL,
    eligible                    BOOLEAN NOT NULL,
    reasons                     TEXT NOT NULL DEFAULT '',
    export_outstanding          NUMERIC(18, 2) NOT NULL,
    computed_outstanding        NUMERIC(18, 2) NOT NULL,
    outstanding_mismatch        BOOLEAN NOT NULL DEFAULT FALSE,
    sector                      TEXT NOT NULL,
    PRIMARY KEY (run_id, receivable_external_id)
);

CREATE TABLE IF NOT EXISTS pool_snapshots (
    run_id                  BIGINT PRIMARY KEY REFERENCES pipeline_runs(run_id),
    total_processed         INTEGER NOT NULL,
    eligible_count          INTEGER NOT NULL,
    ineligible_count        INTEGER NOT NULL,
    exception_count         INTEGER NOT NULL,
    aeb                     NUMERIC(18, 2) NOT NULL,
    excess_concentration    NUMERIC(18, 2) NOT NULL,
    borrowing_base          NUMERIC(18, 2) NOT NULL,
    facility_limit          NUMERIC(18, 2) NOT NULL DEFAULT 250000000,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sector_concentration (
    run_id                  BIGINT NOT NULL REFERENCES pipeline_runs(run_id),
    sector                  TEXT NOT NULL,
    eligible_outstanding    NUMERIC(18, 2) NOT NULL,
    share                   NUMERIC(18, 8) NOT NULL,
    excess                  NUMERIC(18, 2) NOT NULL,
    within_cap              BOOLEAN NOT NULL,
    PRIMARY KEY (run_id, sector)
);

CREATE TABLE IF NOT EXISTS row_exceptions (
    exception_id    BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES pipeline_runs(run_id),
    source          TEXT NOT NULL,
    row_key         TEXT,
    reason          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_row_exceptions_run
    ON row_exceptions (run_id);
