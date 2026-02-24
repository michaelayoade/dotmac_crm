from __future__ import annotations

import builtins
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.models.crm.conversation import Conversation, ConversationAssignment
from app.models.crm.enums import ChannelType as CrmChannelType
from app.models.crm.sales import Lead
from app.models.person import ChannelType as PersonChannelType
from app.models.person import PartyStatus, Person, PersonChannel
from app.services.common import apply_ordering, apply_pagination, coerce_uuid, validate_enum
from app.services.response import ListResponseMixin


def _now():
    return datetime.now(UTC)


def _to_person_channel_type(channel_type: CrmChannelType) -> PersonChannelType:
    return PersonChannelType(channel_type.value)


def _email_from_address(channel_type: CrmChannelType, address: str) -> str:
    safe = "".join(ch if ch.isalnum() else "-" for ch in address)
    return f"{channel_type.value}-{safe}@example.invalid"


def _normalize_email(address: str | None) -> str | None:
    if not address:
        return None
    candidate = address.strip().lower()
    return candidate or None


def _normalize_phone(address: str | None) -> str | None:
    if not address:
        return None
    digits = "".join(ch for ch in address if ch.isdigit())
    if not digits:
        return None
    return f"+{digits}"


def _normalize_channel_address(channel_type: CrmChannelType, address: str) -> str:
    if channel_type == CrmChannelType.email:
        return _normalize_email(address) or address.strip()
    if channel_type == CrmChannelType.whatsapp:
        return _normalize_phone(address) or address.strip()
    return address.strip()


PHONE_CHANNEL_TYPES = {
    PersonChannelType.phone,
    PersonChannelType.sms,
    PersonChannelType.whatsapp,
}


def _find_person_and_channel_by_address(
    db: Session,
    channel_type: CrmChannelType,
    normalized_address: str,
    raw_address: str | None,
) -> tuple[Person | None, PersonChannel | None]:
    person_channel_type = _to_person_channel_type(channel_type)
    if channel_type == CrmChannelType.email:
        channel = (
            db.query(PersonChannel)
            .filter(PersonChannel.channel_type == PersonChannelType.email)
            .filter(func.lower(PersonChannel.address) == normalized_address)
            .first()
        )
        if channel:
            return channel.person, channel
        person = db.query(Person).filter(func.lower(Person.email) == normalized_address).first()
        return person, None
    if channel_type == CrmChannelType.whatsapp:
        channel = (
            db.query(PersonChannel)
            .filter(PersonChannel.channel_type.in_(PHONE_CHANNEL_TYPES))
            .filter(
                or_(
                    PersonChannel.address == normalized_address,
                    PersonChannel.address == raw_address,
                )
            )
            .first()
        )
        if channel:
            return channel.person, channel
        person = (
            db.query(Person)
            .filter(
                or_(
                    Person.phone == normalized_address,
                    Person.phone == raw_address,
                )
            )
            .first()
        )
        return person, None
    channel = (
        db.query(PersonChannel)
        .filter(PersonChannel.channel_type == person_channel_type)
        .filter(PersonChannel.address == normalized_address)
        .first()
    )
    if channel:
        return channel.person, channel
    return None, None


def _ensure_person_channel(
    db: Session,
    person: Person,
    channel_type: PersonChannelType,
    address: str,
    is_primary: bool,
) -> PersonChannel:
    existing = (
        db.query(PersonChannel)
        .filter(PersonChannel.person_id == person.id)
        .filter(PersonChannel.channel_type == channel_type)
        .filter(PersonChannel.address == address)
        .first()
    )
    if existing:
        return existing
    if is_primary:
        has_primary = (
            db.query(PersonChannel)
            .filter(PersonChannel.person_id == person.id)
            .filter(PersonChannel.channel_type == channel_type)
            .filter(PersonChannel.is_primary.is_(True))
            .first()
        )
        if has_primary:
            is_primary = False
    channel = PersonChannel(
        person_id=person.id,
        channel_type=channel_type,
        address=address,
        is_primary=is_primary,
    )
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return channel


def _resolve_owner_agent_id_for_person(db: Session, person_id):
    assignment = (
        db.query(ConversationAssignment)
        .join(Conversation, ConversationAssignment.conversation_id == Conversation.id)
        .filter(Conversation.person_id == person_id)
        .filter(ConversationAssignment.is_active.is_(True))
        .order_by(
            ConversationAssignment.assigned_at.desc().nullslast(),
            ConversationAssignment.created_at.desc(),
        )
        .first()
    )
    if assignment and assignment.agent_id:
        return assignment.agent_id
    return None


def _ensure_lead_for_person(db: Session, person: Person):
    existing = db.query(Lead).filter(Lead.person_id == person.id).first()
    if existing:
        return existing
    lead = Lead(
        person_id=person.id,
        owner_agent_id=_resolve_owner_agent_id_for_person(db, person.id),
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def _create_default_channels(db: Session, person: Person):
    if person.email:
        db.add(
            PersonChannel(
                person_id=person.id,
                channel_type=PersonChannelType.email,
                address=person.email,
                is_primary=True,
                is_verified=person.email_verified,
            )
        )
    if person.phone:
        db.add(
            PersonChannel(
                person_id=person.id,
                channel_type=PersonChannelType.phone,
                address=person.phone,
                is_primary=False,
            )
        )


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def update_contact_channels(
    db: Session,
    person: Person,
    emails: list[str] | None,
    phones: list[str] | None,
    whatsapp_phones: list[str] | None,
    primary_email_index: int | None,
    primary_phone_index: int | None,
):
    email_values = [(_normalize_email(e) or e.strip().lower()) for e in (emails or [])]
    email_values = [e for e in email_values if e]
    email_values = _dedupe_preserve_order(email_values)

    phone_values = [(_normalize_phone(p) or p.strip()) for p in (phones or [])]
    phone_values = [p for p in phone_values if p]
    phone_values = _dedupe_preserve_order(phone_values)

    whatsapp_values = [(_normalize_phone(p) or p.strip()) for p in (whatsapp_phones or [])]
    whatsapp_values = [p for p in whatsapp_values if p]
    whatsapp_values = _dedupe_preserve_order(whatsapp_values)
    if phone_values:
        whatsapp_values = [p for p in whatsapp_values if p in set(phone_values)]
    else:
        whatsapp_values = []

    if email_values:
        if primary_email_index is None or primary_email_index < 0 or primary_email_index >= len(email_values):
            primary_email_index = 0
    else:
        primary_email_index = None

    if phone_values:
        if primary_phone_index is None or primary_phone_index < 0 or primary_phone_index >= len(phone_values):
            primary_phone_index = 0
    else:
        primary_phone_index = None

    existing = list(person.channels or [])
    existing_by_type: dict[PersonChannelType, dict[str, PersonChannel]] = {
        PersonChannelType.email: {},
        PersonChannelType.phone: {},
        PersonChannelType.whatsapp: {},
    }
    for channel in existing:
        if channel.channel_type in existing_by_type:
            existing_by_type[channel.channel_type][channel.address] = channel

    desired = {
        PersonChannelType.email: email_values,
        PersonChannelType.phone: phone_values,
        PersonChannelType.whatsapp: whatsapp_values,
    }
    primary_map = {
        PersonChannelType.email: primary_email_index,
        PersonChannelType.phone: primary_phone_index,
        PersonChannelType.whatsapp: None,
    }

    for channel_type, addresses in desired.items():
        primary_index = primary_map[channel_type]
        desired_set = set(addresses)
        existing_map = existing_by_type[channel_type]

        # Remove channels that are no longer present
        for address, channel in list(existing_map.items()):
            if address not in desired_set:
                db.delete(channel)
                existing_map.pop(address, None)

        # Upsert desired channels
        for idx, address in enumerate(addresses):
            is_primary = primary_index is not None and idx == primary_index
            if address in existing_map:
                existing_map[address].is_primary = is_primary
            else:
                db.add(
                    PersonChannel(
                        person_id=person.id,
                        channel_type=channel_type,
                        address=address,
                        is_primary=is_primary,
                    )
                )

    # Keep primary fields in sync for legacy displays
    if email_values:
        person.email = email_values[primary_email_index or 0]
    if phone_values:
        person.phone = phone_values[primary_phone_index or 0]

    db.commit()
    db.refresh(person)


class Contacts(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        data = payload.model_dump()
        raw_splynx_id = data.get("splynx_id")
        splynx_id: str | None = str(raw_splynx_id).strip() if raw_splynx_id is not None else ""
        splynx_id = splynx_id or None
        # `Person.splynx_id` is a read-only hybrid property backed by metadata_.
        # Always remove it from constructor data to avoid AttributeError.
        data.pop("splynx_id", None)
        if data.get("email"):
            data["email"] = _normalize_email(data["email"]) or data["email"]
            existing = db.query(Person).filter(func.lower(Person.email) == data["email"]).first()
            if existing:
                raise HTTPException(status_code=409, detail="Email already belongs to another contact")
        if data.get("phone"):
            raw_phone = data["phone"].strip()
            data["phone"] = _normalize_phone(data["phone"])
            if data["phone"] or raw_phone:
                existing_phone = (
                    db.query(PersonChannel)
                    .filter(PersonChannel.channel_type.in_(PHONE_CHANNEL_TYPES))
                    .filter(
                        or_(
                            PersonChannel.address == data["phone"],
                            PersonChannel.address == raw_phone,
                        )
                    )
                    .first()
                )
                if existing_phone and existing_phone.person_id:
                    raise HTTPException(
                        status_code=409,
                        detail="Phone already belongs to another contact",
                    )
                existing_person_phone = (
                    db.query(Person)
                    .filter(
                        or_(
                            Person.phone == data["phone"],
                            Person.phone == raw_phone,
                        )
                    )
                    .first()
                )
                if existing_person_phone:
                    raise HTTPException(
                        status_code=409,
                        detail="Phone already belongs to another contact",
                    )
        person = Person(**data)
        if splynx_id:
            person.metadata_ = dict(person.metadata_ or {})
            person.metadata_["splynx_id"] = splynx_id
        db.add(person)
        db.flush()
        _create_default_channels(db, person)
        if splynx_id:
            person.party_status = PartyStatus.subscriber
        elif person.party_status == PartyStatus.lead:
            person.party_status = PartyStatus.contact
        db.commit()
        db.refresh(person)
        _ensure_lead_for_person(db, person)
        return person

    @staticmethod
    def get(db: Session, contact_id: str):
        person = db.get(Person, coerce_uuid(contact_id))
        if not person:
            raise HTTPException(status_code=404, detail="Contact not found")
        return person

    @staticmethod
    def list(
        db: Session,
        person_id: str | None,
        organization_id: str | None,
        party_status: str | None,
        is_active: bool | None,
        search: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(Person).options(selectinload(Person.channels))
        if person_id:
            query = query.filter(Person.id == coerce_uuid(person_id))
        if organization_id:
            query = query.filter(Person.organization_id == coerce_uuid(organization_id))
        if party_status:
            status_value = validate_enum(party_status, PartyStatus, "party_status")
            query = query.filter(Person.party_status == status_value)
        if search:
            like = f"%{search.strip()}%"
            matching_ids = (
                db.query(Person.id)
                .outerjoin(PersonChannel)
                .filter(
                    or_(
                        Person.display_name.ilike(like),
                        Person.first_name.ilike(like),
                        Person.last_name.ilike(like),
                        Person.email.ilike(like),
                        Person.phone.ilike(like),
                        func.json_extract_path_text(Person.metadata_, "splynx_id").ilike(like),
                        PersonChannel.address.ilike(like),
                    )
                )
                .distinct()
            )
            query = query.filter(Person.id.in_(matching_ids))
        if is_active is not None:
            query = query.filter(Person.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": Person.created_at, "display_name": Person.display_name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def list_whatsapp_contacts(
        db: Session,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> builtins.list[dict[str, Any]]:
        query = (
            db.query(Person)
            .join(PersonChannel, PersonChannel.person_id == Person.id)
            .filter(PersonChannel.channel_type == PersonChannelType.whatsapp)
            .filter(Person.is_active.is_(True))
            .options(selectinload(Person.channels))
            .distinct()
        )
        if search:
            like = f"%{search.strip()}%"
            matching_ids = (
                db.query(Person.id)
                .outerjoin(PersonChannel)
                .filter(
                    or_(
                        Person.display_name.ilike(like),
                        Person.first_name.ilike(like),
                        Person.last_name.ilike(like),
                        Person.email.ilike(like),
                        PersonChannel.address.ilike(like),
                    )
                )
                .distinct()
            )
            query = query.filter(Person.id.in_(matching_ids))

        query = apply_ordering(
            query,
            "display_name",
            "asc",
            {"created_at": Person.created_at, "display_name": Person.display_name},
        )
        persons = apply_pagination(query, limit, offset).all()
        results: list[dict] = []
        for person in persons:
            channels = [
                ch for ch in (person.channels or []) if ch.channel_type == PersonChannelType.whatsapp and ch.address
            ]
            if not channels:
                continue
            primary = next((ch for ch in channels if ch.is_primary), None)
            channel = primary or channels[0]
            name = (
                person.display_name
                or f"{person.first_name or ''} {person.last_name or ''}".strip()
                or person.email
                or "Contact"
            )
            results.append(
                {
                    "id": str(person.id),
                    "name": name,
                    "whatsapp_address": channel.address,
                }
            )
        return results

    @staticmethod
    def update(db: Session, contact_id: str, payload):
        person = db.get(Person, coerce_uuid(contact_id))
        if not person:
            raise HTTPException(status_code=404, detail="Contact not found")
        data = payload.model_dump(exclude_unset=True)
        if "splynx_id" in data:
            splynx_id = str(data["splynx_id"]).strip() if data["splynx_id"] else ""
            if splynx_id:
                person.metadata_ = dict(person.metadata_ or {})
                person.metadata_["splynx_id"] = splynx_id
                person.party_status = PartyStatus.subscriber
            else:
                if person.metadata_ and isinstance(person.metadata_, dict):
                    person.metadata_.pop("splynx_id", None)
            data.pop("splynx_id", None)
        for key, value in data.items():
            setattr(person, key, value)
        db.commit()
        db.refresh(person)
        return person

    @staticmethod
    def delete(db: Session, contact_id: str):
        person = db.get(Person, coerce_uuid(contact_id))
        if not person:
            raise HTTPException(status_code=404, detail="Contact not found")
        person.is_active = False
        db.commit()


class ContactChannels(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        person = db.get(Person, payload.person_id)
        if not person:
            raise HTTPException(status_code=404, detail="Contact not found")
        channel_type = PersonChannelType(payload.channel_type.value)
        normalized_address = _normalize_channel_address(payload.channel_type, payload.address)
        raw_address = payload.address.strip()
        if not normalized_address:
            raise HTTPException(status_code=400, detail="Channel address is required")
        if channel_type == PersonChannelType.email:
            existing = (
                db.query(PersonChannel)
                .filter(PersonChannel.channel_type == PersonChannelType.email)
                .filter(func.lower(PersonChannel.address) == normalized_address)
                .first()
            )
            if existing and existing.person_id != payload.person_id:
                raise HTTPException(
                    status_code=409,
                    detail="Email address already belongs to another contact",
                )
            person_match = db.query(Person).filter(func.lower(Person.email) == normalized_address).first()
            if person_match and person_match.id != payload.person_id:
                raise HTTPException(
                    status_code=409,
                    detail="Email address already belongs to another contact",
                )
        elif channel_type in PHONE_CHANNEL_TYPES:
            existing = (
                db.query(PersonChannel)
                .filter(PersonChannel.channel_type.in_(PHONE_CHANNEL_TYPES))
                .filter(
                    or_(
                        PersonChannel.address == normalized_address,
                        PersonChannel.address == raw_address,
                    )
                )
                .first()
            )
            if existing and existing.person_id != payload.person_id:
                raise HTTPException(
                    status_code=409,
                    detail="Phone number already belongs to another contact",
                )
            person_match = (
                db.query(Person)
                .filter(
                    or_(
                        Person.phone == normalized_address,
                        Person.phone == raw_address,
                    )
                )
                .first()
            )
            if person_match and person_match.id != payload.person_id:
                raise HTTPException(
                    status_code=409,
                    detail="Phone number already belongs to another contact",
                )
        else:
            existing = (
                db.query(PersonChannel)
                .filter(PersonChannel.channel_type == channel_type)
                .filter(PersonChannel.address == normalized_address)
                .first()
            )
            if existing and existing.person_id != payload.person_id:
                raise HTTPException(
                    status_code=409,
                    detail="Channel address already belongs to another contact",
                )
        channel = PersonChannel(
            person_id=payload.person_id,
            channel_type=channel_type,
            address=normalized_address,
            is_primary=payload.is_primary,
            is_verified=payload.is_verified,
            metadata_=payload.metadata_,
        )
        if channel.is_primary:
            db.query(PersonChannel).filter(
                PersonChannel.person_id == payload.person_id,
                PersonChannel.channel_type == channel_type,
            ).update({"is_primary": False})
        db.add(channel)
        db.commit()
        db.refresh(channel)
        return channel

    @staticmethod
    def list(
        db: Session,
        person_id: str | None,
        channel_type: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(PersonChannel)
        if person_id:
            query = query.filter(PersonChannel.person_id == coerce_uuid(person_id))
        if channel_type:
            enum_value = validate_enum(channel_type, PersonChannelType, "channel_type")
            query = query.filter(PersonChannel.channel_type == enum_value)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": PersonChannel.created_at, "address": PersonChannel.address},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, channel_id: str, payload):
        channel = db.get(PersonChannel, coerce_uuid(channel_id))
        if not channel:
            raise HTTPException(status_code=404, detail="Contact channel not found")
        data = payload.model_dump(exclude_unset=True)
        if data.get("address"):
            normalized_address = _normalize_channel_address(
                CrmChannelType(channel.channel_type.value),
                data["address"],
            )
            raw_address = data["address"].strip()
            if channel.channel_type == PersonChannelType.email:
                existing = (
                    db.query(PersonChannel)
                    .filter(PersonChannel.channel_type == PersonChannelType.email)
                    .filter(func.lower(PersonChannel.address) == normalized_address)
                    .first()
                )
                if existing and existing.person_id != channel.person_id:
                    raise HTTPException(
                        status_code=409,
                        detail="Email address already belongs to another contact",
                    )
                person_match = db.query(Person).filter(func.lower(Person.email) == normalized_address).first()
                if person_match and person_match.id != channel.person_id:
                    raise HTTPException(
                        status_code=409,
                        detail="Email address already belongs to another contact",
                    )
            elif channel.channel_type in PHONE_CHANNEL_TYPES:
                existing = (
                    db.query(PersonChannel)
                    .filter(PersonChannel.channel_type.in_(PHONE_CHANNEL_TYPES))
                    .filter(
                        or_(
                            PersonChannel.address == normalized_address,
                            PersonChannel.address == raw_address,
                        )
                    )
                    .first()
                )
                if existing and existing.person_id != channel.person_id:
                    raise HTTPException(
                        status_code=409,
                        detail="Phone number already belongs to another contact",
                    )
                person_match = (
                    db.query(Person)
                    .filter(
                        or_(
                            Person.phone == normalized_address,
                            Person.phone == raw_address,
                        )
                    )
                    .first()
                )
                if person_match and person_match.id != channel.person_id:
                    raise HTTPException(
                        status_code=409,
                        detail="Phone number already belongs to another contact",
                    )
            else:
                existing = (
                    db.query(PersonChannel)
                    .filter(PersonChannel.channel_type == channel.channel_type)
                    .filter(PersonChannel.address == normalized_address)
                    .first()
                )
                if existing and existing.person_id != channel.person_id:
                    raise HTTPException(
                        status_code=409,
                        detail="Channel address already belongs to another contact",
                    )
            data["address"] = normalized_address
        if data.get("is_primary"):
            db.query(PersonChannel).filter(
                PersonChannel.person_id == channel.person_id,
                PersonChannel.channel_type == channel.channel_type,
            ).update({"is_primary": False})
        for key, value in data.items():
            setattr(channel, key, value)
        db.commit()
        db.refresh(channel)
        return channel


def get_contact_context(db: Session, contact: Person) -> dict:
    """Get contact context including related tickets, projects, and conversations.

    Returns a dict with recent_tickets, recent_projects, conversations_summary, and tags.
    """
    from sqlalchemy.orm import selectinload

    from app.models.projects import Project, ProjectStatus
    from app.models.tickets import Ticket

    person_id = contact.id

    # Collect tags from conversations
    tags = set()
    for conv in contact.conversations or []:
        conv_with_tags = db.get(
            Conversation,
            conv.id,
            options=[selectinload(Conversation.tags)],
        )
        if conv_with_tags and conv_with_tags.tags:
            for tag in conv_with_tags.tags:
                tags.add(tag.tag)

    # Get recent tickets for this person
    recent_tickets = []
    tickets = (
        db.query(Ticket)
        .filter(Ticket.created_by_person_id == person_id)
        .order_by(Ticket.created_at.desc())
        .limit(3)
        .all()
    )
    for t in tickets:
        ticket_status = t.status.value if hasattr(t.status, "value") else str(t.status)
        recent_tickets.append(
            {
                "id": str(t.id),
                "label": f"TKT-{str(t.id)[:8].upper()}",
                "subject": t.title or "No subject",
                "status": ticket_status,
                "href": f"/admin/support/tickets/{t.id}",
            }
        )

    # Get recent projects for this person
    recent_projects = []
    projects = (
        db.query(Project)
        .filter(Project.is_active.is_(True))
        .filter(Project.status != ProjectStatus.completed)
        .filter(
            or_(
                Project.created_by_person_id == person_id,
                Project.owner_person_id == person_id,
                Project.manager_person_id == person_id,
            )
        )
        .order_by(Project.updated_at.desc(), Project.created_at.desc())
        .limit(3)
        .all()
    )
    for project in projects:
        project_status = project.status.value if hasattr(project.status, "value") else str(project.status)
        recent_projects.append(
            {
                "id": f"PRJ-{str(project.id)[:8].upper()}",
                "name": project.name or "Untitled project",
                "status": project_status,
                "href": f"/admin/projects/{project.id}",
            }
        )

    # Get conversations summary
    conversations_summary = []
    conversations_query = (
        db.query(Conversation)
        .options(
            selectinload(Conversation.assignments).selectinload(ConversationAssignment.agent),
            selectinload(Conversation.assignments).selectinload(ConversationAssignment.team),
        )
        .filter(Conversation.contact_id == contact.id)  # type: ignore[arg-type]
        .order_by(Conversation.updated_at.desc())
        .limit(5)
        .all()
    )
    for conv in conversations_query:
        active_assignment = next(
            (assignment for assignment in conv.assignments or [] if assignment.is_active),
            None,
        )
        agent_id = str(active_assignment.agent_id) if active_assignment and active_assignment.agent_id else ""
        team_id = str(active_assignment.team_id) if active_assignment and active_assignment.team_id else ""
        agent_name = ""
        if active_assignment and active_assignment.agent and active_assignment.agent.person_id:
            agent_person = db.get(Person, active_assignment.agent.person_id)
            if agent_person:
                agent_name = (
                    agent_person.display_name
                    or " ".join(part for part in [agent_person.first_name, agent_person.last_name] if part).strip()
                )
        team_name = active_assignment.team.name if active_assignment and active_assignment.team else ""
        conversations_summary.append(
            {
                "id": str(conv.id),
                "subject": conv.subject or f"Conversation {str(conv.id)[:8]}",
                "status": conv.status.value if conv.status else "open",
                "agent_id": agent_id,
                "team_id": team_id,
                "agent_name": agent_name,
                "team_name": team_name,
            }
        )

    return {
        "tags": list(tags),
        "recent_tickets": recent_tickets,
        "recent_projects": recent_projects,
        "conversations_summary": conversations_summary,
    }


def get_person_with_relationships(
    db: Session,
    person_id: str,
) -> Person | None:
    """Get person with channels and conversations eager-loaded."""
    return (
        db.query(Person)
        .options(
            selectinload(Person.channels),
            selectinload(Person.conversations),
        )
        .filter(Person.id == coerce_uuid(person_id))
        .first()
    )


def get_contact_social_comments(
    db: Session,
    contact_id: str,
    limit: int = 10,
):
    """Get social comments for contact's social channels.

    Returns a list of SocialComment objects (raw model instances).
    """
    from app.models.crm.comments import SocialComment, SocialCommentPlatform

    contact = db.get(Person, coerce_uuid(contact_id))
    if not contact:
        return []

    social_channels = (
        db.query(PersonChannel)
        .filter(PersonChannel.person_id == contact.id)
        .filter(
            PersonChannel.channel_type.in_(
                [
                    PersonChannelType.facebook_messenger,
                    PersonChannelType.instagram_dm,
                ]
            )
        )
        .all()
    )

    fb_addresses = []
    ig_addresses = []
    for channel in social_channels:
        if channel.channel_type == PersonChannelType.facebook_messenger:
            fb_addresses.append(channel.address)
        elif channel.channel_type == PersonChannelType.instagram_dm:
            ig_addresses.append(channel.address)

    comment_filters = []
    if fb_addresses:
        comment_filters.append(
            (SocialComment.platform == SocialCommentPlatform.facebook)
            & (SocialComment.author_id.in_(fb_addresses) | SocialComment.author_name.in_(fb_addresses))
        )
    if ig_addresses:
        comment_filters.append(
            (SocialComment.platform == SocialCommentPlatform.instagram)
            & (SocialComment.author_id.in_(ig_addresses) | SocialComment.author_name.in_(ig_addresses))
        )

    if not comment_filters:
        return []

    return (
        db.query(SocialComment)
        .filter(SocialComment.is_active.is_(True))
        .filter(or_(*comment_filters))
        .order_by(
            SocialComment.created_time.desc().nullslast(),
            SocialComment.created_at.desc(),
        )
        .limit(limit)
        .all()
    )


def get_contact_conversations_summary(
    db: Session,
    contact_id: str,
    limit: int = 5,
) -> list[dict]:
    """Get recent conversations for contact with assignment info."""
    conversations = (
        db.query(Conversation)
        .options(
            selectinload(Conversation.assignments).selectinload(ConversationAssignment.agent),
            selectinload(Conversation.assignments).selectinload(ConversationAssignment.team),
        )
        .filter(Conversation.contact_id == coerce_uuid(contact_id))
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .all()
    )

    # Collect person_ids from active assignments to bulk fetch
    person_ids = []
    for conv in conversations:
        active_assignment = next(
            (a for a in conv.assignments or [] if a.is_active),
            None,
        )
        if active_assignment and active_assignment.agent and active_assignment.agent.person_id:
            person_ids.append(active_assignment.agent.person_id)

    # Bulk fetch all agent persons
    person_map = {}
    if person_ids:
        persons = db.query(Person).filter(Person.id.in_(person_ids)).all()
        person_map = {p.id: p for p in persons}

    result = []
    for conv in conversations:
        active_assignment = next(
            (a for a in conv.assignments or [] if a.is_active),
            None,
        )
        agent_id = str(active_assignment.agent_id) if active_assignment and active_assignment.agent_id else ""
        team_id = str(active_assignment.team_id) if active_assignment and active_assignment.team_id else ""
        agent_name = ""
        if active_assignment and active_assignment.agent and active_assignment.agent.person_id:
            agent_person = person_map.get(active_assignment.agent.person_id)
            if agent_person:
                agent_name = (
                    agent_person.display_name
                    or " ".join(part for part in [agent_person.first_name, agent_person.last_name] if part).strip()
                )
        team_name = active_assignment.team.name if active_assignment and active_assignment.team else ""

        result.append(
            {
                "id": str(conv.id),
                "subject": conv.subject or f"Conversation {str(conv.id)[:8]}",
                "status": conv.status.value if conv.status else "open",
                "agent_id": agent_id,
                "team_id": team_id,
                "agent_name": agent_name,
                "team_name": team_name,
                "updated_at": conv.updated_at,
            }
        )

    return result


def get_contact_resolved_conversations(
    db: Session,
    contact_id: str,
) -> list[dict]:
    """Get resolved conversations for contact."""
    from app.models.crm.enums import ConversationStatus as CrmConversationStatus

    conversations = (
        db.query(Conversation)
        .filter(Conversation.contact_id == coerce_uuid(contact_id))
        .filter(Conversation.status == CrmConversationStatus.resolved)
        .order_by(Conversation.updated_at.desc())
        .all()
    )

    result = []
    for conv in conversations:
        result.append(
            {
                "id": str(conv.id),
                "subject": conv.subject or f"Conversation {str(conv.id)[:8]}",
                "status": conv.status.value if conv.status else "resolved",
                "updated_at": conv.updated_at,
                "last_message_at": conv.last_message_at,
            }
        )

    return result


def get_contact_recent_conversations(
    db: Session,
    contact_id: str,
    limit: int = 5,
):
    """Get recent conversations for a contact.

    Returns a list of Conversation objects (raw model instances).
    """
    return (
        db.query(Conversation)
        .filter(Conversation.contact_id == coerce_uuid(contact_id))
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .all()
    )


def get_contact_tags(db: Session, contact_id: str) -> set[str]:
    """Get all unique tags from a contact's conversations efficiently.

    Uses a single query with join instead of N+1 pattern.
    """
    from app.models.crm.conversation import ConversationTag

    tags = (
        db.query(ConversationTag.tag)
        .join(Conversation, Conversation.id == ConversationTag.conversation_id)
        .filter(Conversation.contact_id == coerce_uuid(contact_id))
        .distinct()
        .all()
    )
    return {tag[0] for tag in tags if tag[0]}


def get_contact_recent_tickets(
    db: Session,
    person_id: str,
    subscriber_ids: list | None = None,
    limit: int = 3,
) -> list[dict]:
    """Get recent tickets for a person."""
    from app.models.tickets import Ticket

    ticket_filters = []
    if person_id:
        ticket_filters.append(Ticket.created_by_person_id == coerce_uuid(person_id))
    if subscriber_ids:
        ticket_filters.append(Ticket.subscriber_id.in_(subscriber_ids))

    if not ticket_filters:
        return []

    tickets = db.query(Ticket).filter(or_(*ticket_filters)).order_by(Ticket.created_at.desc()).limit(limit).all()

    result = []
    for t in tickets:
        ticket_status = t.status.value if hasattr(t.status, "value") else str(t.status)
        result.append(
            {
                "id": str(t.id),
                "label": f"TKT-{str(t.id)[:8].upper()}",
                "subject": t.title or "No subject",
                "status": ticket_status,
                "href": f"/admin/support/tickets/{t.id}",
            }
        )

    return result


def get_contact_recent_projects(
    db: Session,
    person_id: str,
    subscriber_ids: list | None = None,
    limit: int = 3,
) -> list[dict]:
    """Get recent projects for a person."""
    from app.models.projects import Project, ProjectStatus

    project_filters = []
    if person_id:
        person_uuid = coerce_uuid(person_id)
        project_filters.append(Project.created_by_person_id == person_uuid)
        project_filters.append(Project.owner_person_id == person_uuid)
        project_filters.append(Project.manager_person_id == person_uuid)
    if subscriber_ids:
        project_filters.append(Project.subscriber_id.in_(subscriber_ids))

    if not project_filters:
        return []

    projects = (
        db.query(Project)
        .filter(Project.is_active.is_(True))
        .filter(Project.status != ProjectStatus.completed)
        .filter(or_(*project_filters))
        .order_by(Project.updated_at.desc(), Project.created_at.desc())
        .limit(limit)
        .all()
    )

    result = []
    for project in projects:
        project_status = project.status.value if hasattr(project.status, "value") else str(project.status)
        result.append(
            {
                "id": f"PRJ-{str(project.id)[:8].upper()}",
                "name": project.name or "Untitled project",
                "status": project_status,
                "href": f"/admin/projects/{project.id}",
            }
        )

    return result


def get_contact_recent_tasks(
    db: Session,
    person_id: str,
    subscriber_ids: list | None = None,
    limit: int = 3,
) -> list[dict]:
    """Get recent tasks for a person."""
    from app.models.projects import Project, ProjectTask

    task_filters = []
    if person_id:
        person_uuid = coerce_uuid(person_id)
        task_filters.append(ProjectTask.created_by_person_id == person_uuid)
        task_filters.append(ProjectTask.assigned_to_person_id == person_uuid)
    if subscriber_ids:
        task_filters.append(Project.subscriber_id.in_(subscriber_ids))

    if not task_filters:
        return []

    tasks = (
        db.query(ProjectTask)
        .join(Project, ProjectTask.project_id == Project.id)
        .filter(or_(*task_filters))
        .order_by(ProjectTask.updated_at.desc(), ProjectTask.created_at.desc())
        .limit(limit)
        .all()
    )

    result = []
    for task in tasks:
        task_status = task.status.value if hasattr(task.status, "value") else str(task.status)
        result.append(
            {
                "id": str(task.id),
                "label": f"TSK-{str(task.id)[:8].upper()}",
                "title": task.title or "Untitled task",
                "status": task_status,
                "href": f"/admin/projects/tasks/{task.id}",
            }
        )

    return result


def get_or_create_contact_by_channel(
    db: Session,
    channel_type: CrmChannelType,
    address: str,
    display_name: str | None = None,
):
    person_channel_type = _to_person_channel_type(channel_type)
    normalized_address = _normalize_channel_address(channel_type, address)
    raw_address = address.strip()
    person, channel = _find_person_and_channel_by_address(
        db,
        channel_type,
        normalized_address,
        raw_address,
    )
    if person:
        if display_name and not person.display_name:
            person.display_name = display_name
            db.commit()
            db.refresh(person)
        if channel and channel.channel_type == person_channel_type:
            return person, channel
        channel = _ensure_person_channel(
            db,
            person,
            person_channel_type,
            normalized_address,
            is_primary=True,
        )
        return person, channel

    display_first, display_last = "", ""
    if display_name:
        parts = display_name.split()
        display_first = parts[0]
        display_last = " ".join(parts[1:]) if len(parts) > 1 else "Unknown"
    person = Person(
        first_name=display_first or "Unknown",
        last_name=display_last or "Unknown",
        display_name=display_name,
        email=normalized_address
        if channel_type == CrmChannelType.email
        else _email_from_address(channel_type, normalized_address),
        phone=normalized_address if channel_type == CrmChannelType.whatsapp else None,
        party_status=PartyStatus.lead,
    )
    db.add(person)
    db.flush()
    channel = PersonChannel(
        person_id=person.id,
        channel_type=person_channel_type,
        address=normalized_address,
        is_primary=True,
    )
    db.add(channel)
    db.commit()
    db.refresh(person)
    db.refresh(channel)
    return person, channel


# Singleton instances
contacts = Contacts()
contact_channels = ContactChannels()
