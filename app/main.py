import logging
import os
import time
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.routers import agents, agent, campaigns, broadcasts, chatbot, contacts, daily_orders, delivery, events, field_agent, intelligence, lifecycle, opportunities, playbook, prospects, query, reports, shipday_historical, shipday_sync, team_content, telnyx, webhooks

# ---------------------------------------------------------------------------
# Structured logging — INFO by default, DEBUG when LOG_LEVEL=DEBUG in env
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)
logger.info("DabbahWala API starting — log_level=%s", os.environ.get("LOG_LEVEL", "INFO"))

app = FastAPI(
    title="DabbahWala Marketing System",
    description="Lifecycle-driven marketing orchestration API",
    version="0.1.0",
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every incoming request with method, path, status, and duration."""
    start = time.time()
    response = await call_next(request)
    ms = int((time.time() - start) * 1000)
    logger.info(
        "HTTP %s %s → %d (%dms) client=%s",
        request.method,
        request.url.path,
        response.status_code,
        ms,
        request.client.host if request.client else "unknown",
    )
    return response


@app.on_event("startup")
async def startup_ensure_schema():
    """Apply any schema changes that psql migrations may have silently skipped."""
    from app.db import get_cursor
    try:
        with get_cursor(commit=True) as cur:
            cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_date DATE")
            cur.execute("UPDATE orders SET delivery_date = order_date WHERE delivery_date IS NULL")
            cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS notes TEXT")
        logger.info("startup_ensure_schema — orders.delivery_date and notes present")
    except Exception as e:
        logger.error("startup_ensure_schema failed: %s", e)


@app.on_event("startup")
async def startup_run_migrations():
    """Run any pending SQL migrations from the migrations/ directory."""
    import glob as _glob
    from app.db import get_cursor

    migrations_dir = os.path.join(os.path.dirname(__file__), "..", "migrations")
    files = sorted(_glob.glob(os.path.join(migrations_dir, "*.sql")))
    if not files:
        logger.warning("startup_run_migrations — no migration files found at %s", migrations_dir)
        return

    try:
        with get_cursor(commit=True) as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS dabbahwala.schema_migrations (
                    filename   TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
    except Exception as e:
        logger.error("startup_run_migrations — could not create schema_migrations: %s", e)
        return

    applied = skipped = failed = 0
    for path in files:
        filename = os.path.basename(path)
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM dabbahwala.schema_migrations WHERE filename = %s",
                    (filename,),
                )
                already = cur.fetchone()
        except Exception as e:
            logger.error("startup_run_migrations — could not check %s: %s", filename, e)
            failed += 1
            continue

        if already:
            skipped += 1
            continue

        try:
            with open(path) as f:
                sql = f.read()
            with get_cursor(commit=True) as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO dabbahwala.schema_migrations (filename) VALUES (%s) ON CONFLICT DO NOTHING",
                    (filename,),
                )
            logger.info("startup_run_migrations — APPLIED %s", filename)
            applied += 1
        except Exception as e:
            import psycopg2.errors as _pgerr
            already_exists = isinstance(e, (_pgerr.DuplicateTable, _pgerr.DuplicateObject, _pgerr.UniqueViolation))
            if already_exists:
                # Migration ran before the schema_migrations tracker existed — mark it applied now
                try:
                    with get_cursor(commit=True) as cur:
                        cur.execute(
                            "INSERT INTO dabbahwala.schema_migrations (filename) VALUES (%s) ON CONFLICT DO NOTHING",
                            (filename,),
                        )
                except Exception:
                    pass
                logger.info("startup_run_migrations — BACKFILLED %s (already applied)", filename)
                skipped += 1
            else:
                logger.error("startup_run_migrations — FAILED %s: %s", filename, e)
                failed += 1

    logger.info(
        "startup_run_migrations — done: applied=%d skipped=%d failed=%d",
        applied, skipped, failed,
    )


@app.on_event("startup")
async def startup_sync_chatbot_docs():
    """Kick off chatbot doc reindex in a background thread so port binds immediately."""
    import threading
    from app.routers.chatbot import sync_docs_on_startup
    t = threading.Thread(target=sync_docs_on_startup, daemon=True, name="chatbot-reindex")
    t.start()


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unhandled exception %s %s: %s [%s]",
        request.method,
        request.url.path,
        exc,
        type(exc).__name__,
        exc_info=True,
    )
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "type": type(exc).__name__},
    )

app.include_router(events.router, prefix="/api/events", tags=["events"])
app.include_router(lifecycle.router, prefix="/api/lifecycle", tags=["lifecycle"])
app.include_router(campaigns.router, prefix="/api/campaigns", tags=["campaigns"])
app.include_router(telnyx.router, prefix="/api/telnyx", tags=["telnyx"])
app.include_router(delivery.router, prefix="/api/delivery", tags=["delivery"])
app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
app.include_router(opportunities.router, prefix="/api/opportunities", tags=["opportunities"])
app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
app.include_router(shipday_historical.router, prefix="/api/shipday", tags=["shipday"])
app.include_router(shipday_sync.router,       prefix="/api/shipday", tags=["shipday"])
app.include_router(daily_orders.router, prefix="/api/daily-orders", tags=["daily-orders"])
app.include_router(intelligence.router, prefix="/api/intelligence", tags=["intelligence"])
app.include_router(agent.router, prefix="/api/agent", tags=["agent"])
app.include_router(playbook.router, prefix="/api/playbook", tags=["playbook"])
app.include_router(query.router, prefix="/api/query", tags=["query"])
app.include_router(team_content.router, prefix="/api/team-content", tags=["team-content"])
app.include_router(field_agent.router, prefix="/api/field-agent", tags=["field-agent"])
app.include_router(chatbot.router, prefix="/api/chatbot", tags=["chatbot"])
app.include_router(broadcasts.router, prefix="/api/broadcasts", tags=["broadcasts"])
app.include_router(prospects.router, prefix="/api/prospects", tags=["prospects"])
app.include_router(contacts.router, prefix="/api/contacts", tags=["contacts"])
app.include_router(webhooks.router, prefix="/api/webhooks", tags=["webhooks"])


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """Interactive marketing intelligence dashboard."""
    html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    with open(html_path) as f:
        return f.read()


@app.get("/health")
def health():
    from fastapi.responses import JSONResponse
    logger.debug("Health check requested")
    try:
        from app.db import get_cursor
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT 1")
        logger.debug("Health check OK — DB connected")
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        logger.error("Health check FAILED — DB unreachable: %s", e, exc_info=True)
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": str(e)},
        )


@app.post("/admin/migrate/{migration_number}")
def run_migration(migration_number: int, secret: str = ""):
    """Run a specific migration file by number. Requires admin secret."""
    import glob
    from fastapi import HTTPException
    admin_secret = os.environ.get("ADMIN_SECRET", "")
    if not admin_secret or secret != admin_secret:
        logger.warning("Migration %03d blocked — invalid admin secret", migration_number)
        raise HTTPException(status_code=403, detail="Forbidden")

    matches = glob.glob(f"migrations/{migration_number:03d}_*.sql")
    if not matches:
        logger.warning("Migration %03d not found — no file matches", migration_number)
        return {"error": f"No migration file found for {migration_number:03d}"}

    migration_file = matches[0]
    logger.info("Running migration: %s", migration_file)
    with open(migration_file) as f:
        sql = f.read()

    from app.db import get_cursor
    with get_cursor(commit=True) as cur:
        cur.execute(sql)

    logger.info("Migration %s executed successfully", migration_file)
    return {"status": "ok", "migration": migration_file, "executed": True}


@app.post("/admin/query")
async def run_query(request: Request, secret: str = "", sql: str = ""):
    """Run a read-only SQL query. Accepts SQL via query param or JSON body."""
    from fastapi import HTTPException
    admin_secret = os.environ.get("ADMIN_SECRET", "")

    # Try JSON body if query params empty
    if not sql:
        try:
            body = await request.json()
            secret = body.get("secret", secret)
            sql = body.get("sql", "")
        except Exception:
            pass

    if not admin_secret or secret != admin_secret:
        logger.warning("Admin query blocked — invalid secret from %s", request.client)
        raise HTTPException(status_code=403, detail="Forbidden")

    if not sql.strip():
        logger.debug("Admin query called with empty SQL — ignoring")
        return {"error": "No SQL provided"}

    logger.info("Admin query: %.120s…", sql.strip())
    from app.db import get_cursor
    with get_cursor(commit=False) as cur:
        cur.execute(sql)
        try:
            rows = cur.fetchall()
            logger.debug("Admin query returned %d rows", len(rows))
            return {"status": "ok", "rows": rows, "count": len(rows)}
        except Exception as e:
            logger.warning("Admin query fetchall failed (probably DDL): %s", e)
            return {"status": "ok", "rows": [], "count": 0}


@app.post("/admin/exec")
async def run_exec(request: Request, secret: str = "", sql: str = ""):
    """Run a DDL/DML SQL statement. Accepts SQL via query param or JSON body."""
    from fastapi import HTTPException
    admin_secret = os.environ.get("ADMIN_SECRET", "")

    # Try JSON body if query params empty
    if not sql:
        try:
            body = await request.json()
            secret = body.get("secret", secret)
            sql = body.get("sql", "")
        except Exception:
            pass

    if not admin_secret or secret != admin_secret:
        logger.warning("Admin exec blocked — invalid secret from %s", request.client)
        raise HTTPException(status_code=403, detail="Forbidden")

    if not sql.strip():
        logger.debug("Admin exec called with empty SQL — ignoring")
        return {"error": "No SQL provided"}

    logger.info("Admin exec: %.120s…", sql.strip())
    from app.db import get_cursor
    with get_cursor(commit=True) as cur:
        cur.execute(sql)

    logger.info("Admin exec completed successfully")
    return {"status": "ok", "executed": True}
