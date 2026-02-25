-- Migration 059: Drop the old 9-parameter overload of store_telnyx_message
-- Migration 033 added a new 12-parameter version via CREATE OR REPLACE, but
-- since the parameter list changed, PostgreSQL created a second overload rather
-- than replacing the original. With two overloads whose defaults make them
-- ambiguous for a 9-argument call, every call to store_telnyx_message raises
-- "function … is not unique". This migration drops the old overload so only the
-- 12-parameter version (from migration 033) remains.

SET search_path TO dabbahwala;

DROP FUNCTION IF EXISTS store_telnyx_message(
    TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, TEXT, BOOLEAN, JSONB
);
