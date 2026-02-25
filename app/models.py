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
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    from_campaign: Optional[str] = None
    to_campaign: str


# --- Telnyx message ---
class TelnyxMessageIn(BaseModel):
    contact_email: Optional[str] = None   # resolved from contact_phone if absent
    contact_phone: Optional[str] = None   # fallback for inbound SMS (customer's phone)
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
    contact_email: Optional[str] = None   # resolved from contact_phone if absent
    contact_phone: Optional[str] = None   # fallback for inbound calls (customer's phone)
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
    outcome_notes: Optional[str] = None


# --- Contact ground team overrides ---
class ContactPriority(BaseModel):
    priority_override: str  # 'none' | 'high' | 'do_not_contact'


class ContactNotes(BaseModel):
    sales_notes: str


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
