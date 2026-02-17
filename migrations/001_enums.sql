-- 001_enums.sql
-- Create dedicated schema and type definitions for the DabbahWala marketing system

CREATE SCHEMA IF NOT EXISTS dabbahwala;
SET search_path TO dabbahwala;

CREATE TYPE dabbahwala.lifecycle_segment AS ENUM (
    'cold',
    'engaged',
    'active_customer',
    'new_customer',
    'lapsed_customer',
    'reactivation_candidate',
    'cooling',
    'optout'
);

CREATE TYPE dabbahwala.campaign_name AS ENUM (
    'NURTURE_SLOW',
    'PROMO_STANDARD',
    'PROMO_AGGRESSIVE',
    'NEW_CUSTOMER_ONBOARDING',
    'REACTIVATION'
);

CREATE TYPE dabbahwala.event_type AS ENUM (
    'email_open',
    'email_click',
    'sms_sent',
    'sms_received',
    'sms_click',
    'call_completed',
    'order_placed',
    'unsubscribe',
    'sms_stop',
    'delivery_update'
);

CREATE TYPE dabbahwala.delivery_status_type AS ENUM (
    'assigned',
    'picked_up',
    'in_transit',
    'delivered',
    'failed'
);

CREATE TYPE dabbahwala.opportunity_action AS ENUM (
    'send_sms',
    'field_sales_call',
    'send_email'
);

CREATE TYPE dabbahwala.opportunity_status AS ENUM (
    'pending',
    'dispatched',
    'completed',
    'expired',
    'declined'
);
