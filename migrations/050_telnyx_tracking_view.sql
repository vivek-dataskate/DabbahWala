-- 043_telnyx_tracking_view.sql
-- Create a telnyx_tracking view over telnyx_messages so that existing
-- queries referencing telnyx_tracking.created_at and telnyx_tracking.direction
-- continue to work.  The underlying table uses `sent_at`; this view exposes
-- it as `created_at` for compatibility.

SET search_path TO dabbahwala;

DROP VIEW IF EXISTS telnyx_tracking;
CREATE VIEW telnyx_tracking AS
SELECT
    id,
    contact_id,
    direction,
    from_number,
    to_number,
    body,
    telnyx_msg_id,
    status,
    is_delivery_staff,
    metadata,
    sent_at,
    sent_at AS created_at
FROM telnyx_messages;
