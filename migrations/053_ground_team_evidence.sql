-- 053_ground_team_evidence.sql
-- Closes three ground-team evidence gaps so AI agents see the full picture:
--   A. outcome_notes on opportunities — field team notes on each outcome
--   B. priority_override on contacts  — do_not_contact / high-priority flags
--   C. sales_notes on contacts        — per-customer pinned observations
--
-- Also updates update_opportunity_outcome() to accept and store outcome_notes.

SET search_path TO dabbahwala;

-- ─── A. Outcome notes on opportunities ────────────────────────────────────────
ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS outcome_notes TEXT;

-- ─── B. Per-customer priority override ───────────────────────────────────────
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS
    priority_override TEXT NOT NULL DEFAULT 'none'
    CHECK (priority_override IN ('none', 'high', 'do_not_contact'));

-- ─── C. Per-customer ground team sales notes ──────────────────────────────────
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS sales_notes TEXT;

-- ─── D. Update DB function to accept and store outcome_notes ──────────────────
CREATE OR REPLACE FUNCTION update_opportunity_outcome(
    p_opportunity_id BIGINT,
    p_status         opportunity_status,
    p_outcome        TEXT,
    p_outcome_notes  TEXT DEFAULT NULL
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SET search_path TO dabbahwala
AS $$
BEGIN
    UPDATE opportunities
    SET status        = p_status,
        outcome       = p_outcome,
        outcome_notes = COALESCE(p_outcome_notes, outcome_notes),
        completed_at  = now()
    WHERE id = p_opportunity_id;
    RETURN FOUND;
END;
$$;
