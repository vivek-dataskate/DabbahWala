-- 001_enums.sql
-- Type definitions for the DabbahWala marketing system

CREATE TYPE lifecycle_segment AS ENUM (
    'cold',
    'engaged',
    'active_customer',
    'new_customer',
    'lapsed_customer',
    'reactivation_candidate',
    'cooling',
    'optout'
);

CREATE TYPE campaign_name AS ENUM (
    'NURTURE_SLOW',
    'PROMO_STANDARD',
    'PROMO_AGGRESSIVE',
    'NEW_CUSTOMER_ONBOARDING',
    'REACTIVATION'
);

CREATE TYPE event_type AS ENUM (
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

CREATE TYPE delivery_status_type AS ENUM (
    'assigned',
    'picked_up',
    'in_transit',
    'delivered',
    'failed'
);

CREATE TYPE opportunity_action AS ENUM (
    'send_sms',
    'field_sales_call',
    'send_email'
);

CREATE TYPE opportunity_status AS ENUM (
    'pending',
    'dispatched',
    'completed',
    'expired',
    'declined'
);
