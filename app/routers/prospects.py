"""
Prospect ingestion — CSV upload and single-contact manual entry.

Columns for CSV upload (case-insensitive, extra columns ignored):
  FirstName*, LastName, Phone, EmailId, Address
  (* required; at least one of Phone or EmailId is also required)
"""
import csv
import io
import logging
import os
import re

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from app.db import get_cursor

logger = logging.getLogger(__name__)

router = APIRouter()

# ── CSV template content ────────────────────────────────────────────────────
_TEMPLATE_CSV = "FirstName,LastName,Phone,EmailId,Address\nJane,Doe,2485550100,jane@example.com,\"123 Main St, Troy MI 48084\"\n"


def _col(row: dict, *candidates: str) -> str:
    """Return first matching key value, case-insensitive."""
    low = {k.lower().strip(): v for k, v in row.items()}
    for key in candidates:
        v = low.get(key.lower().strip())
        if v is not None:
            return str(v).strip()
    return ''


def normalize_phone(phone: str) -> str:
    digits = re.sub(r'\D', '', phone)
    return digits[-10:] if len(digits) >= 10 else ''


def _upsert_contact(cur, first_name: str, last_name: str, phone: str, email: str, address: str) -> tuple[int, bool]:
    """Insert or update a contact. Returns (contact_id, is_new)."""

    # Normalise
    phone = normalize_phone(phone) if phone else ''
    email = email.lower().strip() if email else ''
    first_name = first_name.strip()
    last_name = last_name.strip()
    address = address.strip()

    if not first_name:
        raise ValueError("first_name is required")
    if not phone and not email:
        raise ValueError("at least one of phone or email is required")

    # --- Try by email first (unique) ---
    if email:
        cur.execute("SELECT id FROM contacts WHERE email = %s", (email,))
        row = cur.fetchone()
        if row:
            # Update name/phone/address if blank
            cur.execute(
                """UPDATE contacts SET
                    first_name = COALESCE(NULLIF(first_name,''), %s),
                    last_name  = COALESCE(NULLIF(last_name,''),  %s),
                    phone      = COALESCE(NULLIF(phone,''),      %s),
                    address    = COALESCE(NULLIF(address,''),    %s),
                    updated_at = now()
                WHERE id = %s""",
                (first_name, last_name, phone or None, address or None, row['id'])
            )
            return row['id'], False

    # --- Try by phone if given (phone not unique, pick most recent) ---
    if phone:
        cur.execute(
            "SELECT id FROM contacts WHERE phone = %s ORDER BY created_at DESC LIMIT 1",
            (phone,)
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                """UPDATE contacts SET
                    first_name = COALESCE(NULLIF(first_name,''), %s),
                    last_name  = COALESCE(NULLIF(last_name,''),  %s),
                    email      = COALESCE(NULLIF(email,''),      %s),
                    address    = COALESCE(NULLIF(address,''),    %s),
                    updated_at = now()
                WHERE id = %s""",
                (first_name, last_name, email or None, address or None, row['id'])
            )
            return row['id'], False

    # --- Insert new ---
    cur.execute(
        """INSERT INTO contacts (first_name, last_name, phone, email, address,
               lifecycle_segment, email_nurture_enabled, primary_source)
           VALUES (%s, %s, %s, %s, %s, 'cold', true, 'Prospect')
           RETURNING id""",
        (first_name, last_name, phone or None, email or None, address or None)
    )
    return cur.fetchone()['id'], True


# ── Template download ───────────────────────────────────────────────────────

@router.get("/template")
def download_template():
    """Download the blank CSV template for prospect uploads."""
    return StreamingResponse(
        io.BytesIO(_TEMPLATE_CSV.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=\"prospect_template.csv\""},
    )


# ── CSV upload ──────────────────────────────────────────────────────────────

@router.post("/upload-csv")
async def upload_prospects_csv(file: UploadFile = File(...)):
    """
    Parse a CSV of prospects and upsert them into contacts.
    Columns: FirstName*, LastName, Phone, EmailId, Address
    Runs lifecycle cycle after all inserts.
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")  # handle BOM
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise HTTPException(status_code=400, detail="CSV is empty or has no data rows")

    added = 0
    updated = 0
    skipped = 0
    errors = []

    with get_cursor(commit=True) as cur:
        for i, row in enumerate(rows, start=2):  # row 1 = header
            first = _col(row, 'firstname', 'first_name', 'first name')
            last  = _col(row, 'lastname',  'last_name',  'last name')
            phone = _col(row, 'phone', 'phonenumber', 'phone_number')
            email = _col(row, 'emailid', 'email', 'email_id', 'emailaddress')
            addr  = _col(row, 'address', 'addr')

            if not first:
                errors.append(f"Row {i}: FirstName is blank — skipped")
                skipped += 1
                continue
            if not phone and not email:
                errors.append(f"Row {i}: '{first}' has neither Phone nor EmailId — skipped")
                skipped += 1
                continue

            try:
                _, is_new = _upsert_contact(cur, first, last, phone, email, addr)
                if is_new:
                    added += 1
                    logger.info("Prospect added: %s %s", first, last)
                else:
                    updated += 1
                    logger.info("Prospect updated: %s %s", first, last)
            except Exception as e:
                errors.append(f"Row {i}: '{first}' — {e}")
                skipped += 1

        # Run lifecycle cycle to assign campaigns to new cold contacts
        if added > 0:
            try:
                cur.execute("SELECT * FROM run_lifecycle_cycle()")
                lc = cur.fetchone()
                lifecycle_result = {
                    "contacts_updated": lc["contacts_updated"] if lc else 0,
                    "campaigns_queued": lc["campaigns_queued"] if lc else 0,
                }
            except Exception as e:
                logger.warning("Lifecycle cycle after prospect upload failed: %s", e)
                lifecycle_result = {"error": str(e)}
        else:
            lifecycle_result = {"skipped": True, "reason": "no new contacts added"}

    return {
        "status": "ok",
        "rows_processed": len(rows),
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "errors": errors[:20],
        "lifecycle": lifecycle_result,
    }


# ── Manual single-contact entry ─────────────────────────────────────────────

class ProspectIn(BaseModel):
    first_name: str
    last_name: Optional[str] = ""
    phone: Optional[str] = ""
    email: Optional[str] = ""
    address: Optional[str] = ""


@router.post("/add")
def add_single_prospect(p: ProspectIn):
    """
    Add or update a single prospect contact.
    Runs lifecycle cycle after insert.
    """
    if not p.first_name.strip():
        raise HTTPException(status_code=400, detail="first_name is required")
    if not p.phone and not p.email:
        raise HTTPException(status_code=400, detail="At least one of phone or email is required")

    with get_cursor(commit=True) as cur:
        try:
            contact_id, is_new = _upsert_contact(
                cur, p.first_name, p.last_name or '', p.phone or '', p.email or '', p.address or ''
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error("Error adding prospect: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

        # Run lifecycle to assign campaign immediately
        lifecycle_result = {}
        if is_new:
            try:
                cur.execute("SELECT * FROM run_lifecycle_cycle()")
                lc = cur.fetchone()
                lifecycle_result = {
                    "contacts_updated": lc["contacts_updated"] if lc else 0,
                    "campaigns_queued": lc["campaigns_queued"] if lc else 0,
                }
            except Exception as e:
                logger.warning("Lifecycle after add-prospect failed: %s", e)
                lifecycle_result = {"error": str(e)}

    return {
        "status": "added" if is_new else "updated",
        "contact_id": contact_id,
        "lifecycle": lifecycle_result,
    }
