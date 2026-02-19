from fastapi import FastAPI, Request

from app.routers import agent, campaigns, daily_orders, delivery, events, intelligence, lifecycle, opportunities, playbook, query, reports, sms, telnyx

app = FastAPI(
    title="DabbahWala Marketing System",
    description="Lifecycle-driven marketing orchestration API",
    version="0.1.0",
)

app.include_router(events.router, prefix="/api/events", tags=["events"])
app.include_router(lifecycle.router, prefix="/api/lifecycle", tags=["lifecycle"])
app.include_router(campaigns.router, prefix="/api/campaigns", tags=["campaigns"])
app.include_router(sms.router, prefix="/api/sms", tags=["sms"])
app.include_router(telnyx.router, prefix="/api/telnyx", tags=["telnyx"])
app.include_router(delivery.router, prefix="/api/delivery", tags=["delivery"])
app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
app.include_router(opportunities.router, prefix="/api/opportunities", tags=["opportunities"])
app.include_router(daily_orders.router, prefix="/api/daily-orders", tags=["daily-orders"])
app.include_router(intelligence.router, prefix="/api/intelligence", tags=["intelligence"])
app.include_router(agent.router, prefix="/api/agent", tags=["agent"])
app.include_router(playbook.router, prefix="/api/playbook", tags=["playbook"])
app.include_router(query.router, prefix="/api/query", tags=["query"])


@app.get("/health")
def health():
    try:
        from app.db import get_cursor
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT 1")
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        return {"status": "degraded", "db": str(e)}


@app.post("/admin/migrate/{migration_number}")
def run_migration(migration_number: int, secret: str = ""):
    """Run a specific migration file by number. Requires admin secret."""
    import os
    admin_secret = os.environ.get("ADMIN_SECRET", "dabbahwala-admin-2026")
    if secret != admin_secret:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Forbidden")

    import glob
    matches = glob.glob(f"migrations/{migration_number:03d}_*.sql")
    if not matches:
        return {"error": f"No migration file found for {migration_number:03d}"}

    migration_file = matches[0]
    with open(migration_file) as f:
        sql = f.read()

    from app.db import get_cursor
    with get_cursor(commit=True) as cur:
        cur.execute(sql)

    return {"status": "ok", "migration": migration_file, "executed": True}


@app.post("/admin/query")
async def run_query(request: Request, secret: str = "", sql: str = ""):
    """Run a read-only SQL query. Accepts SQL via query param or JSON body."""
    import os
    admin_secret = os.environ.get("ADMIN_SECRET", "dabbahwala-admin-2026")

    # Try JSON body if query params empty
    if not sql:
        try:
            body = await request.json()
            secret = body.get("secret", secret)
            sql = body.get("sql", "")
        except Exception:
            pass

    if secret != admin_secret:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Forbidden")

    if not sql.strip():
        return {"error": "No SQL provided"}

    from app.db import get_cursor
    with get_cursor(commit=False) as cur:
        cur.execute(sql)
        try:
            rows = cur.fetchall()
            return {"status": "ok", "rows": rows, "count": len(rows)}
        except Exception:
            return {"status": "ok", "rows": [], "count": 0}


@app.post("/admin/exec")
async def run_exec(request: Request, secret: str = "", sql: str = ""):
    """Run a DDL/DML SQL statement. Accepts SQL via query param or JSON body."""
    import os
    admin_secret = os.environ.get("ADMIN_SECRET", "dabbahwala-admin-2026")

    # Try JSON body if query params empty
    if not sql:
        try:
            body = await request.json()
            secret = body.get("secret", secret)
            sql = body.get("sql", "")
        except Exception:
            pass

    if secret != admin_secret:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Forbidden")

    if not sql.strip():
        return {"error": "No SQL provided"}

    from app.db import get_cursor
    with get_cursor(commit=True) as cur:
        cur.execute(sql)

    return {"status": "ok", "executed": True}
