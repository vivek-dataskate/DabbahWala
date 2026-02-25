-- Migration 057: Add source column to contacts
-- Allows tagging contacts by origin (e.g., 'test_harness', 'shipday', 'import')

SET search_path TO dabbahwala;

ALTER TABLE contacts ADD COLUMN IF NOT EXISTS source TEXT;

CREATE INDEX IF NOT EXISTS idx_contacts_source ON contacts(source) WHERE source IS NOT NULL;
