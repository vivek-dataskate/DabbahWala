-- 001_schema_types.sql
-- Schema, extensions, and all enum types — final consolidated state.
-- All enums use ADD VALUE IF NOT EXISTS so this is safe to re-run.

CREATE SCHEMA IF NOT EXISTS dabbahwala;
SET search_path TO dabbahwala;

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------

DO $$ BEGIN
    CREATE TYPE lifecycle_segment AS ENUM (
        'cold', 'engaged', 'active_customer', 'new_customer',
        'lapsed_customer', 'reactivation_candidate', 'cooling', 'optout'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE campaign_name AS ENUM (
        'NURTURE_SLOW', 'PROMO_STANDARD', 'PROMO_AGGRESSIVE',
        'NEW_CUSTOMER_ONBOARDING', 'REACTIVATION', 'ACTIVE_CUSTOMER', 'APP_TO_DIRECT'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

ALTER TYPE campaign_name ADD VALUE IF NOT EXISTS 'ACTIVE_CUSTOMER';
ALTER TYPE campaign_name ADD VALUE IF NOT EXISTS 'APP_TO_DIRECT';

DO $$ BEGIN
    CREATE TYPE event_type AS ENUM (
        'email_open', 'email_click', 'sms_sent', 'sms_received', 'sms_click',
        'call_completed', 'order_placed', 'unsubscribe', 'sms_stop', 'delivery_update'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE delivery_status_type AS ENUM (
        'assigned', 'picked_up', 'in_transit', 'delivered', 'failed'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE opportunity_action AS ENUM (
        'send_sms', 'field_sales_call', 'send_email'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE opportunity_status AS ENUM (
        'pending', 'dispatched', 'completed', 'expired', 'declined'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
