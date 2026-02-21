-- Migration 040: Expand action_queue to support new action types and nullable contact_id
--
-- Background: All external API calls (Airtable, Instantly, Google Drive, SMTP) are being
-- moved out of Python and into n8n via the action_queue pattern. This migration adds the
-- new action types and makes contact_id nullable to support system-level actions that
-- are not tied to a specific contact (e.g. push_instantly_sequences, upload_google_drive).

-- 1. Drop old CHECK constraint on action_type
ALTER TABLE action_queue DROP CONSTRAINT IF EXISTS action_queue_action_type_check;

-- 2. Add expanded CHECK constraint with all supported action types
ALTER TABLE action_queue
    ADD CONSTRAINT action_queue_action_type_check CHECK (
        action_type IN (
            -- Existing
            'send_sms',
            'move_campaign',
            'escalate_airtable',
            'none',
            -- New: Airtable
            'sync_airtable_task',
            'log_query_to_airtable',
            -- New: Instantly
            'push_instantly_lead',
            'push_instantly_sequences',
            -- New: Google Drive
            'upload_google_drive',
            -- New: Email reports (sent by n8n email node)
            'send_email_report'
        )
    );

-- 3. Make contact_id nullable to support system-level actions (no specific contact)
ALTER TABLE action_queue ALTER COLUMN contact_id DROP NOT NULL;

-- 4. Drop old FK so it tolerates NULL
ALTER TABLE action_queue DROP CONSTRAINT IF EXISTS action_queue_contact_id_fkey;

-- 5. Re-add FK with ON DELETE CASCADE but allow NULL
ALTER TABLE action_queue
    ADD CONSTRAINT action_queue_contact_id_fkey
    FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE;
