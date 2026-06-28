"""Reseller portal JSON API — for an external reseller-partner app.

Thin wrappers over app.services.reseller_portal (the web reseller portal calls
the same functions). Resellers authenticate as people holding a reseller role;
every operation is scoped to the actor's reseller organization by the service.
Mounted under require_user_auth.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.services import reseller_portal
from app.services.common import coerce_uuid

router = APIRouter(prefix="/reseller", tags=["reseller-portal"])


def _reseller_actor(auth=Depends(get_current_user), db: Session = Depends(get_db)) -> uuid.UUID:
    person_id = auth.get("person_id") if auth else None
    if not person_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    actor = coerce_uuid(person_id)
    if not reseller_portal.person_has_any_reseller_role(db, person_id=actor):
        raise HTTPException(status_code=403, detail="Reseller access required")
    return actor


class ResellerOrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    domain: str | None = None


class ResellerContactCreate(BaseModel):
    organization_id: str
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = Field(min_length=1, max_length=120)
    email: str | None = None
    phone: str | None = None


class ResellerSubscriberCreate(BaseModel):
    organization_id: str
    subscriber_number: str = Field(min_length=1, max_length=120)
    status: str = "active"
    service_name: str | None = None


def _org_out(org) -> dict:
    return {"id": str(org.id), "name": org.name}


def _contact_out(person) -> dict:
    return {
        "id": str(person.id),
        "first_name": person.first_name,
        "last_name": person.last_name,
        "email": person.email,
        "phone": getattr(person, "phone", None),
    }


def _subscriber_out(sub) -> dict:
    status = getattr(sub, "status", None)
    return {
        "id": str(sub.id),
        "account_number": getattr(sub, "account_number", None),
        "status": getattr(status, "value", status),
    }


# ── organizations ────────────────────────────────────────────────────────────


@router.get("/organizations")
def list_organizations(actor: uuid.UUID = Depends(_reseller_actor), db: Session = Depends(get_db)):
    return [_org_out(o) for o in reseller_portal.list_scope_organizations(db, actor_person_id=actor)]


@router.post("/organizations", status_code=201)
def create_organization(
    payload: ResellerOrgCreate, actor: uuid.UUID = Depends(_reseller_actor), db: Session = Depends(get_db)
):
    org = reseller_portal.create_child_organization(db, actor_person_id=actor, name=payload.name, domain=payload.domain)
    return _org_out(org)


# ── contacts ─────────────────────────────────────────────────────────────────


@router.get("/contacts")
def list_contacts(actor: uuid.UUID = Depends(_reseller_actor), db: Session = Depends(get_db)):
    return [_contact_out(p) for p in reseller_portal.list_contacts_for_actor(db, actor_person_id=actor)]


@router.post("/contacts", status_code=201)
def create_contact(
    payload: ResellerContactCreate, actor: uuid.UUID = Depends(_reseller_actor), db: Session = Depends(get_db)
):
    person = reseller_portal.create_contact(
        db,
        actor_person_id=actor,
        organization_id=payload.organization_id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        phone=payload.phone,
    )
    return _contact_out(person)


# ── subscribers ──────────────────────────────────────────────────────────────


@router.get("/subscribers")
def list_subscribers(actor: uuid.UUID = Depends(_reseller_actor), db: Session = Depends(get_db)):
    return [_subscriber_out(s) for s in reseller_portal.list_subscribers_for_actor(db, actor_person_id=actor)]


@router.post("/subscribers", status_code=201)
def create_subscriber(
    payload: ResellerSubscriberCreate, actor: uuid.UUID = Depends(_reseller_actor), db: Session = Depends(get_db)
):
    sub = reseller_portal.create_subscriber(
        db,
        actor_person_id=actor,
        organization_id=payload.organization_id,
        subscriber_number=payload.subscriber_number,
        status=payload.status,
        service_name=payload.service_name,
    )
    return _subscriber_out(sub)
