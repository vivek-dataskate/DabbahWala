from fastapi import APIRouter

from app.db import get_cursor
from app.models import LifecycleResult

router = APIRouter()


@router.post("/run", response_model=LifecycleResult)
def run_lifecycle_cycle():
    with get_cursor() as cur:
        cur.execute("SELECT * FROM run_lifecycle_cycle()")
        row = cur.fetchone()
        return LifecycleResult(
            contacts_updated=row["contacts_updated"],
            campaigns_queued=row["campaigns_queued"],
        )
