-- Migration 058: Add hypothesis_hash to goal_experiments for deduplication
-- Allows INSERT ON CONFLICT to skip hypotheses already seen in any prior run.

ALTER TABLE goal_experiments
    ADD COLUMN IF NOT EXISTS hypothesis_hash VARCHAR(64);

CREATE UNIQUE INDEX IF NOT EXISTS goal_experiments_hypothesis_hash_key
    ON goal_experiments (hypothesis_hash)
    WHERE hypothesis_hash IS NOT NULL;

-- Backfill existing rows so old records don't block future ON CONFLICT logic.
UPDATE goal_experiments
SET hypothesis_hash = LEFT(MD5(LOWER(TRIM(experiment_type || '|' || hypothesis))), 16)
WHERE hypothesis_hash IS NULL;
