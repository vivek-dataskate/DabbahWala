"""
Agent Playbook — User-configurable rules for the Claude Agent.

Users edit rules in Airtable -> n8n syncs to this API -> Claude uses them for decisions.

Airtable table structure:
  - Rule Name (text)
  - Category (single select: inference, decision, messaging, exclusion, priority, general)
  - Instruction (long text) — natural language rule for Claude
  - Priority (number, 1-100, higher = more important)
  - Active (checkbox)
  - Created By (text)

n8n workflow:
  1. Poll Airtable every 15 min (or webhook on change)
  2. POST /api/playbook/sync with all active records
  3. Playbook is cached and injected into Claude's system prompt
"""
import os

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.db import get_cursor

router = APIRouter()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class PlaybookRule(BaseModel):
    rule_name: str
    category: str = 'general'
    instruction: str
    priority: int = 50
    is_active: bool = True
    created_by: str = 'airtable'
    airtable_record_id: str | None = None


class PlaybookSyncResult(BaseModel):
    synced: int
    created: int
    updated: int
    deactivated: int


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------
@router.get("/rules")
def get_active_rules():
    """Get all active playbook rules, sorted by priority."""
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT id, rule_name, category, instruction, priority, is_active,
                   created_by, created_at
            FROM agent_playbook
            WHERE is_active = true
            ORDER BY priority DESC, created_at
        """)
        return [dict(r) for r in cur.fetchall()]


@router.get("/rules/for-prompt")
def get_rules_for_prompt():
    """
    Get all active rules formatted as a prompt section for Claude.
    This is what gets injected into the agent's system prompt.
    """
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT category, rule_name, instruction, priority
            FROM agent_playbook
            WHERE is_active = true
            ORDER BY
                CASE category
                    WHEN 'exclusion' THEN 1  -- exclusions first (safety)
                    WHEN 'priority' THEN 2
                    WHEN 'inference' THEN 3
                    WHEN 'decision' THEN 4
                    WHEN 'messaging' THEN 5
                    ELSE 6
                END,
                priority DESC
        """)
        rules = cur.fetchall()

    if not rules:
        return {"prompt_section": "", "rule_count": 0}

    # Group by category
    categories = {}
    for r in rules:
        cat = r['category']
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(r)

    # Build prompt section
    lines = ["\n## Your Playbook (User-Configured Rules)\n"]
    lines.append("Follow these rules when analyzing contacts. They override default behavior.\n")

    category_labels = {
        'exclusion': 'EXCLUSION RULES (Must Follow)',
        'priority': 'PRIORITY RULES',
        'inference': 'INFERENCE RULES (What to Look For)',
        'decision': 'DECISION RULES (What to Do)',
        'messaging': 'MESSAGING RULES (How to Communicate)',
        'general': 'GENERAL RULES',
    }

    for cat in ['exclusion', 'priority', 'inference', 'decision', 'messaging', 'general']:
        if cat in categories:
            lines.append(f"\n### {category_labels.get(cat, cat.upper())}")
            for r in categories[cat]:
                lines.append(f"- **{r['rule_name']}** (priority {r['priority']}): {r['instruction']}")

    prompt_text = "\n".join(lines)
    return {"prompt_section": prompt_text, "rule_count": len(rules)}


@router.post("/rules", response_model=dict)
async def create_rule(rule: PlaybookRule):
    """Create a new playbook rule."""
    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO agent_playbook (rule_name, category, instruction, priority, is_active, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (rule.rule_name, rule.category, rule.instruction,
             rule.priority, rule.is_active, rule.created_by)
        )
        row = cur.fetchone()
        return {"id": row['id'], "status": "created"}


@router.put("/rules/{rule_id}")
async def update_rule(rule_id: int, rule: PlaybookRule):
    """Update an existing playbook rule."""
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE agent_playbook SET rule_name = %s, category = %s, instruction = %s, "
            "priority = %s, is_active = %s, updated_at = now() WHERE id = %s",
            (rule.rule_name, rule.category, rule.instruction,
             rule.priority, rule.is_active, rule_id)
        )
        return {"id": rule_id, "status": "updated"}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int):
    """Soft-delete a rule (deactivate it)."""
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE agent_playbook SET is_active = false, updated_at = now() WHERE id = %s",
            (rule_id,)
        )
        return {"id": rule_id, "status": "deactivated"}


# ---------------------------------------------------------------------------
# Airtable sync endpoint
# ---------------------------------------------------------------------------
@router.post("/sync-from-airtable", response_model=PlaybookSyncResult)
async def sync_from_airtable(request: Request):
    """
    Sync playbook rules from Airtable.
    n8n calls this with all active Airtable records.

    Expected body:
    {
        "records": [
            {
                "airtable_id": "rec...",
                "rule_name": "...",
                "category": "inference",
                "instruction": "...",
                "priority": 80,
                "active": true
            }
        ]
    }
    """
    body = await request.json()
    records = body.get('records', [])

    created = 0
    updated = 0
    deactivated = 0

    with get_cursor(commit=True) as cur:
        # Get existing rules created by airtable
        cur.execute(
            "SELECT id, rule_name, instruction, is_active FROM agent_playbook "
            "WHERE created_by = 'airtable'"
        )
        existing = {r['rule_name']: dict(r) for r in cur.fetchall()}

        incoming_names = set()

        for record in records:
            name = record.get('rule_name', '').strip()
            if not name:
                continue
            incoming_names.add(name)

            category = record.get('category', 'general')
            instruction = record.get('instruction', '')
            priority = record.get('priority', 50)
            active = record.get('active', True)

            if name in existing:
                # Update existing
                cur.execute(
                    "UPDATE agent_playbook SET category = %s, instruction = %s, "
                    "priority = %s, is_active = %s, updated_at = now() "
                    "WHERE rule_name = %s AND created_by = 'airtable'",
                    (category, instruction, priority, active, name)
                )
                updated += 1
            else:
                # Create new
                cur.execute(
                    "INSERT INTO agent_playbook (rule_name, category, instruction, "
                    "priority, is_active, created_by) VALUES (%s, %s, %s, %s, %s, 'airtable')",
                    (name, category, instruction, priority, active)
                )
                created += 1

        # Deactivate rules that were removed from Airtable
        for name, rule in existing.items():
            if name not in incoming_names and rule['is_active']:
                cur.execute(
                    "UPDATE agent_playbook SET is_active = false, updated_at = now() "
                    "WHERE rule_name = %s AND created_by = 'airtable'",
                    (name,)
                )
                deactivated += 1

    return PlaybookSyncResult(
        synced=len(records),
        created=created,
        updated=updated,
        deactivated=deactivated,
    )
