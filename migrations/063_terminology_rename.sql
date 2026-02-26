-- Migration 063: Terminology standardization — rename AI Stack tables
-- inference_results  -> contact_observations  (Layer 1: Observer agents output)
-- decision_recommendations -> action_plans    (Layer 2: Advisor agents output)
-- Also renames the FK column inference_result_id -> observation_id in action_plans

-- -------------------------------------------------------------------------
-- Step 1: Rename inference_results -> contact_observations
-- -------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'inference_results') THEN
        ALTER TABLE inference_results RENAME TO contact_observations;
        RAISE NOTICE 'Renamed inference_results -> contact_observations';
    ELSE
        RAISE NOTICE 'Table inference_results not found (already renamed or never existed)';
    END IF;
END $$;

-- -------------------------------------------------------------------------
-- Step 2: Rename decision_recommendations -> action_plans
-- -------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'decision_recommendations') THEN
        ALTER TABLE decision_recommendations RENAME TO action_plans;
        RAISE NOTICE 'Renamed decision_recommendations -> action_plans';
    ELSE
        RAISE NOTICE 'Table decision_recommendations not found (already renamed or never existed)';
    END IF;
END $$;

-- -------------------------------------------------------------------------
-- Step 3: Rename FK column inference_result_id -> observation_id in action_plans
-- -------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'action_plans' AND column_name = 'inference_result_id'
    ) THEN
        ALTER TABLE action_plans RENAME COLUMN inference_result_id TO observation_id;
        RAISE NOTICE 'Renamed action_plans.inference_result_id -> observation_id';
    ELSE
        RAISE NOTICE 'Column inference_result_id not found in action_plans (already renamed or never existed)';
    END IF;
END $$;

-- -------------------------------------------------------------------------
-- Step 4: Recreate indexes under new names (idempotent)
-- -------------------------------------------------------------------------
DROP INDEX IF EXISTS idx_inference_contact;
DROP INDEX IF EXISTS idx_inference_run_at;
DROP INDEX IF EXISTS idx_decision_contact;
DROP INDEX IF EXISTS idx_decision_run_at;

CREATE INDEX IF NOT EXISTS idx_observations_contact ON contact_observations(contact_id);
CREATE INDEX IF NOT EXISTS idx_observations_run_at   ON contact_observations(run_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_plans_contact  ON action_plans(contact_id);
CREATE INDEX IF NOT EXISTS idx_action_plans_run_at   ON action_plans(run_at DESC);
