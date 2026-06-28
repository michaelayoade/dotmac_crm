"""Mobile-agent inbox actions JSON API — thin wrappers over the inbox services.

Exposes agent runtime actions that were previously admin-web only: bulk actions,
snooze, run/list macros, and per-agent saved filters. Mounted under the CRM
router (require_user_auth). (Conversation comments / private notes / message
attachments remain a follow-up.)
"""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.services.common import coerce_uuid
from app.services.crm.inbox import saved_filters as saved_filters_service
from app.services.crm.inbox.bulk_actions import apply_bulk_action
from app.services.crm.inbox.conversation_status import snooze_conversation
from app.services.crm.inbox.macro_executor import execute_macro
from app.services.crm.inbox.macros import conversation_macros

router = APIRouter(prefix="/crm/inbox", tags=["crm-inbox-actions"])


def _person_id(auth) -> str | None:
    return str(auth["person_id"]) if auth and auth.get("person_id") else None


def _require_person(auth) -> str:
    person_id = _person_id(auth)
    if not person_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return person_id


# ── bulk actions ─────────────────────────────────────────────────────────────


class BulkActionRequest(BaseModel):
    conversation_ids: list[str]
    action: str  # e.g. "status:resolved", "assign:<agent_id>", "tag:<tag>"


@router.post("/conversations/bulk")
def bulk_action(payload: BulkActionRequest, db: Session = Depends(get_db), auth=Depends(get_current_user)):
    result = apply_bulk_action(
        db, conversation_ids=payload.conversation_ids, action=payload.action, actor_id=_person_id(auth)
    )
    if result.kind != "success":
        raise HTTPException(status_code=400, detail=result.detail or "Invalid bulk action")
    return asdict(result)


# ── snooze ───────────────────────────────────────────────────────────────────


class SnoozeRequest(BaseModel):
    preset: str  # e.g. 1_hour / tomorrow / next_week / custom
    until: str | None = None  # ISO datetime, required when preset == "custom"


@router.post("/conversations/{conversation_id}/snooze")
def snooze(conversation_id: str, payload: SnoozeRequest, db: Session = Depends(get_db), auth=Depends(get_current_user)):
    result = snooze_conversation(
        db,
        conversation_id=conversation_id,
        preset=payload.preset,
        until_at_raw=payload.until,
        actor_id=_person_id(auth),
    )
    if result.kind == "not_found":
        raise HTTPException(status_code=404, detail=result.detail or "Conversation not found")
    if result.kind != "updated":
        raise HTTPException(status_code=400, detail=result.detail or "Invalid snooze request")
    return asdict(result)


# ── macros ───────────────────────────────────────────────────────────────────


@router.get("/macros")
def list_macros(agent_id: str, db: Session = Depends(get_db)):
    macros = conversation_macros.list_for_agent(db, agent_id)
    return [{"id": str(m.id), "name": m.name, "is_active": m.is_active} for m in macros]


class RunMacroRequest(BaseModel):
    macro_id: str


@router.post("/conversations/{conversation_id}/run-macro")
def run_macro(
    conversation_id: str, payload: RunMacroRequest, db: Session = Depends(get_db), auth=Depends(get_current_user)
):
    macro = conversation_macros.get(db, payload.macro_id)
    result = execute_macro(
        db,
        macro_id=str(macro.id),
        conversation_id=conversation_id,
        actions=macro.actions,
        actor_person_id=_person_id(auth),
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error_detail or "Macro execution failed")
    return asdict(result)


# ── saved filters (per agent) ────────────────────────────────────────────────


class SavedFilterCreate(BaseModel):
    name: str
    params: dict[str, str]


@router.get("/saved-filters")
def list_saved_filters(db: Session = Depends(get_db), auth=Depends(get_current_user)):
    return saved_filters_service.list_saved_filters(db, coerce_uuid(_require_person(auth)))


@router.post("/saved-filters", status_code=201)
def create_saved_filter(payload: SavedFilterCreate, db: Session = Depends(get_db), auth=Depends(get_current_user)):
    saved = saved_filters_service.save_saved_filter(
        db, coerce_uuid(_require_person(auth)), name=payload.name, params=payload.params
    )
    if saved is None:
        raise HTTPException(status_code=400, detail="Could not save filter")
    return saved


@router.delete("/saved-filters/{filter_id}", status_code=204)
def delete_saved_filter(filter_id: str, db: Session = Depends(get_db), auth=Depends(get_current_user)):
    if not saved_filters_service.delete_saved_filter(db, coerce_uuid(_require_person(auth)), filter_id):
        raise HTTPException(status_code=404, detail="Saved filter not found")
