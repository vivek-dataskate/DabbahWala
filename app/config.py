import os

from dotenv import load_dotenv

load_dotenv()

_raw_db_url = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/dabbahwala")
# Render uses postgres:// but psycopg2 needs postgresql://
DATABASE_URL = _raw_db_url.replace("postgres://", "postgresql://", 1) if _raw_db_url.startswith("postgres://") else _raw_db_url
# Supabase: switch session-mode pooler (5432) → transaction-mode pooler (6543).
# Transaction mode multiplexes server connections across many concurrent clients,
# eliminating "MaxClientsInSessionMode" errors under parallel n8n workflow execution.
# Also embed search_path as a startup option so every connection has it without
# needing a separate SET command (which pgBouncer may not preserve across transactions).
if "pooler.supabase.com:5432/" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace(
        "pooler.supabase.com:5432/",
        "pooler.supabase.com:6543/",
    )
    sep = "&" if "?" in DATABASE_URL else "?"
    DATABASE_URL += f"{sep}options=-csearch_path%3Ddabbahwala"
API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "8000"))

AIRTABLE_API_KEY = os.environ.get("AIRTABLE_API_KEY", "")
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "")
AIRTABLE_FIELD_SALES_TABLE = os.environ.get("AIRTABLE_FIELD_SALES_TABLE", "Field Sales Tasks")
AIRTABLE_QUERY_LOG_TABLE = os.environ.get("AIRTABLE_QUERY_LOG_TABLE", "Query Log")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# SMTP for report emails (Gmail, Outlook, or any SMTP relay)
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
REPORT_EMAIL_TO = os.environ.get("REPORT_EMAIL_TO", "core@dabbahwala.com")
REPORT_EMAIL_FROM = os.environ.get("REPORT_EMAIL_FROM", "")
# Shipday delivery management
SHIPDAY_API_KEY = os.environ.get("SHIPDAY_API_KEY", "")
INSTANTLY_API_KEY = os.environ.get("INSTANTLY_API_KEY", "")
TELNYX_API_KEY = os.environ.get("TELNYX_API_KEY", "")
TELNYX_FROM_NUMBER = os.environ.get("TELNYX_FROM_NUMBER", "+18444322224")
