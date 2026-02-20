-- 044_team_content_types.sql
-- Expand team_content.content_type to include customer_feedback and delivery_issue

SET search_path TO dabbahwala;

ALTER TABLE team_content DROP CONSTRAINT IF EXISTS team_content_content_type_check;
ALTER TABLE team_content ADD CONSTRAINT team_content_content_type_check
    CHECK (content_type IN (
        'ground_note',
        'ad_copy',
        'observation',
        'question',
        'customer_feedback',
        'delivery_issue'
    ));
