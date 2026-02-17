-- 015_daily_reports.sql
-- Daily report storage for agent-generated summaries

SET search_path TO dabbahwala;

CREATE TABLE daily_reports (
    id              BIGSERIAL PRIMARY KEY,
    report_date     DATE NOT NULL UNIQUE,
    report_data     JSONB NOT NULL,
    summary         TEXT,
    net_new_orders  INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
