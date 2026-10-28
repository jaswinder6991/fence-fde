-- Version installment schedules so corrections can keep prior plans for audit.
-- Existing rows become version 1; live book pointer stays current_schedule_version=1
-- until a later change bumps it and inserts a new version.

ALTER TABLE installments
    ADD COLUMN IF NOT EXISTS schedule_version INTEGER NOT NULL DEFAULT 1;

ALTER TABLE payment_allocations
    ADD COLUMN IF NOT EXISTS schedule_version INTEGER NOT NULL DEFAULT 1;

-- Recreate FK/PK only when still on the pre-version shape.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.table_constraints
        WHERE table_schema = 'public'
          AND table_name = 'installments'
          AND constraint_type = 'PRIMARY KEY'
          AND constraint_name = 'installments_pkey'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.key_column_usage
        WHERE table_schema = 'public'
          AND table_name = 'installments'
          AND constraint_name = 'installments_pkey'
          AND column_name = 'schedule_version'
    ) THEN
        ALTER TABLE payment_allocations
            DROP CONSTRAINT IF EXISTS payment_allocations_receivable_external_id_installment_seq_fkey;
        ALTER TABLE payment_allocations
            DROP CONSTRAINT IF EXISTS payment_allocations_pkey;
        ALTER TABLE installments
            DROP CONSTRAINT IF EXISTS installments_pkey;

        ALTER TABLE installments
            ADD PRIMARY KEY (receivable_external_id, schedule_version, seq);

        ALTER TABLE payment_allocations
            ADD PRIMARY KEY (payment_id, schedule_version, installment_seq);

        ALTER TABLE payment_allocations
            ADD CONSTRAINT payment_allocations_installment_fkey
            FOREIGN KEY (receivable_external_id, schedule_version, installment_seq)
            REFERENCES installments (receivable_external_id, schedule_version, seq);
    END IF;
END $$;

DROP INDEX IF EXISTS idx_payment_allocations_recv;
CREATE INDEX IF NOT EXISTS idx_payment_allocations_recv
    ON payment_allocations (receivable_external_id, schedule_version, installment_seq);
