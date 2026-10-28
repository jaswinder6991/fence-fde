-- Corrections prep: persistent pointer to the live schedule version per receivable.
-- See 003_installment_schedule_version.sql for versioned installment/allocation rows.
-- Allocate/schedule/report join installments where schedule_version = this pointer.

ALTER TABLE receivables_current
    ADD COLUMN IF NOT EXISTS current_schedule_version INTEGER NOT NULL DEFAULT 1;
