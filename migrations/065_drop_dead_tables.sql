-- Migration 065: Drop dead tables
-- Tables identified as never referenced in Python code or n8n workflows.
-- All other formerly-unused tables were already dropped in migrations 061-063.

DROP TABLE IF EXISTS dabbahwala.campaign_push_log;
DROP TABLE IF EXISTS dabbahwala.intent_phrases;
