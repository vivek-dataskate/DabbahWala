from datetime import datetime
from typing import Optional

from pydantic import BaseModel


# --- Event ingestion ---
class EventIngest(BaseModel):
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None  # fallback if email unknown (e.g. Shipday)
    event_type: str
    metadata: dict = {}


class EventResponse(BaseModel):
    event_id: int


# --- Lifecycle ---
class LifecycleResult(BaseModel):
    contacts_updated: int
    campaigns_queued: int


# --- Campaign queue ---
class CampaignMove(BaseModel):
    queue_id: int
    contact_email: str
    contact_phone: Optional[str] = None
    from_campaign: Optional[str] = None
    to_campaign: str


# --- SMS queue ---
class SmsPending(BaseModel):
    contact_id: int
    contact_email: str
    phone: str
    sms_level: int
    lifecycle: str


# --- Telnyx message ---
class TelnyxMessageIn(BaseModel):
    contact_email: str
    direction: str
    from_number: str
    to_number: str
    body: Optional[str] = None
    telnyx_msg_id: Optional[str] = None
    status: Optional[str] = None
    is_delivery_staff: bool = False
    metadata: dict = {}


# --- Telnyx call ---
class TelnyxCallIn(BaseModel):
    contact_email: str
    direction: str
    from_number: str
    to_number: str
    duration_sec: Optional[int] = None
    recording_url: Optional[str] = None
    transcript: Optional[str] = None
    summary: Optional[str] = None
    is_delivery_staff: bool = False
    metadata: dict = {}
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


# --- Delivery status ---
class DeliveryStatusIn(BaseModel):
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None  # fallback if email unknown (e.g. Shipday)
    order_ref: Optional[str] = None
    status: str
    updated_by: Optional[str] = None
    notes: Optional[str] = None
    location: Optional[str] = None
    metadata: dict = {}


# --- Opportunities ---
class OpportunityCreate(BaseModel):
    contact_id: int
    action: str
    priority: str
    reason: str
    suggested_message: Optional[str] = None
    confidence_score: Optional[float] = None


class OpportunityDispatched(BaseModel):
    airtable_record_id: str


class OpportunityOutcome(BaseModel):
    status: str
    outcome: Optional[str] = None


# --- Field agent SMS ---
class FieldAgentSmsIn(BaseModel):
    contact_phone: Optional[str] = None   # field agents know phone, not email
    contact_email: Optional[str] = None
    agent_name: str
    body: str
    sent_at: Optional[datetime] = None    # allow backdating if logged after the fact
    notes: Optional[str] = None


# --- Generic ---
class IdResponse(BaseModel):
    id: int
