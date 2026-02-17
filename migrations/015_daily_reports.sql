-- 015_daily_reports.sql
-- Daily report storage for agent-generated summaries
-- Core metric: net new orders attributed to marketing

CREATE TABLE daily_reports (
    id              BIGSERIAL PRIMARY KEY,
    report_date     DATE NOT NULL UNIQUE,
    report_data     JSONB NOT NULL,             -- structured: campaigns, transitions, orders, pipeline
    summary         TEXT,                        -- agent-generated human-readable summary
    net_new_orders  INT NOT NULL DEFAULT 0,      -- headline metric
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
