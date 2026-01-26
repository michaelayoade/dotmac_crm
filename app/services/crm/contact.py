from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, ConversationAssignment
from app.models.crm.enums import ChannelType as CrmChannelType
from app.models.crm.sales import Lead
from app.models.person import ChannelType as PersonChannelType, PartyStatus, Person, PersonChannel
from app.services.common import apply_ordering, apply_pagination, coerce_uuid, validate_enum
from app.services.response import ListResponseMixin


def _now():
    return datetime.now(timezone.utc)


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
    return digits or None


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
        person = (
            db.query(Person)
            .filter(func.lower(Person.email) == normalized_address)
            .first()
        )
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


class Contacts(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        data = payload.model_dump()
        if data.get("email"):
            data["email"] = _normalize_email(data["email"]) or data["email"]
            existing = (
                db.query(Person)
                .filter(func.lower(Person.email) == data["email"])
                .first()
            )
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
        db.add(person)
        db.flush()
        _create_default_channels(db, person)
        if person.party_status == PartyStatus.lead:
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
        is_active: bool | None,
        search: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(Person)
        if person_id:
            query = query.filter(Person.id == coerce_uuid(person_id))
        if organization_id:
            query = query.filter(Person.organization_id == coerce_uuid(organization_id))
        if search:
            like = f"%{search.strip()}%"
            query = (
                query.outerjoin(PersonChannel)
                .filter(
                    or_(
                        Person.display_name.ilike(like),
                        Person.first_name.ilike(like),
                        Person.last_name.ilike(like),
                        Person.email.ilike(like),
                        Person.phone.ilike(like),
                        PersonChannel.address.ilike(like),
                    )
                )
                .distinct()
            )
        if is_active is None:
            query = query.filter(Person.is_active.is_(True))
        else:
            query = query.filter(Person.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": Person.created_at, "display_name": Person.display_name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, contact_id: str, payload):
        person = db.get(Person, coerce_uuid(contact_id))
        if not person:
            raise HTTPException(status_code=404, detail="Contact not found")
        data = payload.model_dump(exclude_unset=True)
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
            person_match = (
                db.query(Person)
                .filter(func.lower(Person.email) == normalized_address)
                .first()
            )
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
        if "address" in data and data["address"]:
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
                person_match = (
                    db.query(Person)
                    .filter(func.lower(Person.email) == normalized_address)
                    .first()
                )
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
        if "is_primary" in data and data["is_primary"]:
            db.query(PersonChannel).filter(
                PersonChannel.person_id == channel.person_id,
                PersonChannel.channel_type == channel.channel_type,
            ).update({"is_primary": False})
        for key, value in data.items():
            setattr(channel, key, value)
        db.commit()
        db.refresh(channel)
        return channel


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
        email=normalized_address if channel_type == CrmChannelType.email else _email_from_address(channel_type, normalized_address),
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
    _ensure_lead_for_person(db, person)
    return person, channel
