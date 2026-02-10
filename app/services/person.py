from __future__ import annotations

import builtins
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.models.person import (
    ChannelType,
    PartyStatus,
    Person,
    PersonChannel,
    PersonMergeLog,
    PersonStatus,
    PersonStatusLog,
)
from app.schemas.person import PersonChannelCreate, PersonCreate, PersonUpdate
from app.services.common import apply_ordering, apply_pagination, validate_enum
from app.services.response import ListResponseMixin


class InvalidTransitionError(Exception):
    """Raised when an invalid party status transition is attempted."""
    pass


# Valid party status transitions (from -> allowed to states)
VALID_TRANSITIONS = {
    PartyStatus.lead: {PartyStatus.contact, PartyStatus.customer},
    PartyStatus.contact: {PartyStatus.customer, PartyStatus.lead},
    PartyStatus.customer: {PartyStatus.subscriber, PartyStatus.contact},
    PartyStatus.subscriber: {PartyStatus.customer},
}

PHONE_CHANNEL_TYPES = {ChannelType.phone, ChannelType.sms, ChannelType.whatsapp}


def _normalize_email(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip().lower()
    return candidate or None


def _normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return None
    return f"+{digits}"


def _normalize_channel_address(channel_type: ChannelType, address: str | None) -> str | None:
    if not address:
        return None
    if channel_type == ChannelType.email:
        return _normalize_email(address)
    if channel_type in PHONE_CHANNEL_TYPES:
        return _normalize_phone(address)
    candidate = address.strip()
    return candidate or None


def _find_person_by_email(db: Session, email: str | None) -> Person | None:
    normalized = _normalize_email(email)
    if not normalized:
        return None
    return db.query(Person).filter(func.lower(Person.email) == normalized).first()


def _find_person_by_phone(db: Session, phone: str | None) -> Person | None:
    normalized = _normalize_phone(phone)
    raw = phone.strip() if phone else None
    if not normalized and not raw:
        return None
    return (
        db.query(Person)
        .filter(or_(Person.phone == normalized, Person.phone == raw))
        .first()
    )


def _find_person_channel_owner(
    db: Session,
    channel_type: ChannelType,
    address: str | None,
) -> PersonChannel | None:
    normalized = _normalize_channel_address(channel_type, address)
    raw = address.strip() if isinstance(address, str) else None
    if not normalized:
        return None
    if channel_type in PHONE_CHANNEL_TYPES:
        return (
            db.query(PersonChannel)
            .filter(PersonChannel.channel_type.in_(PHONE_CHANNEL_TYPES))
            .filter(or_(PersonChannel.address == normalized, PersonChannel.address == raw))
            .first()
        )
    if channel_type == ChannelType.email:
        return (
            db.query(PersonChannel)
            .filter(PersonChannel.channel_type == ChannelType.email)
            .filter(func.lower(PersonChannel.address) == normalized)
            .first()
        )
    return (
        db.query(PersonChannel)
        .filter(PersonChannel.channel_type == channel_type)
        .filter(PersonChannel.address == normalized)
        .first()
    )


def _is_valid_transition(from_status: PartyStatus, to_status: PartyStatus) -> bool:
    """Check if a party status transition is valid."""
    if from_status == to_status:
        return True  # No-op is always valid
    allowed = VALID_TRANSITIONS.get(from_status, set())
    return to_status in allowed


class People(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: PersonCreate):
        """Create person, auto-create primary email channel."""
        data = payload.model_dump(exclude={"channels"})
        data["email"] = _normalize_email(data.get("email")) or data.get("email")
        data["phone"] = _normalize_phone(data.get("phone"))
        existing_by_email = _find_person_by_email(db, data.get("email"))
        if existing_by_email:
            raise HTTPException(status_code=409, detail="Email already belongs to another person")
        existing_by_phone = _find_person_by_phone(db, data.get("phone"))
        if existing_by_phone:
            raise HTTPException(status_code=409, detail="Phone already belongs to another person")
        for payload_channel in payload.channels:
            existing_channel = _find_person_channel_owner(
                db,
                ChannelType(payload_channel.channel_type.value),
                payload_channel.address,
            )
            if existing_channel:
                raise HTTPException(
                    status_code=409,
                    detail="Channel address already belongs to another person",
                )
        person = Person(**data)
        db.add(person)
        db.flush()

        existing_channels = set()
        # Auto-create email channel from person.email
        if person.email:
            email_channel = PersonChannel(
                person_id=person.id,
                channel_type=ChannelType.email,
                address=_normalize_email(person.email) or person.email,
                is_primary=True,
                is_verified=person.email_verified,
            )
            db.add(email_channel)
            existing_channels.add((ChannelType.email, person.email))

        # Create additional channels from payload
        for ch in payload.channels:
            normalized_address = _normalize_channel_address(
                ChannelType(ch.channel_type.value),
                ch.address,
            )
            if not normalized_address:
                continue
            if (ChannelType(ch.channel_type.value), normalized_address) in existing_channels:
                continue
            channel: PersonChannel = PersonChannel(
                person_id=person.id,
                channel_type=ChannelType(ch.channel_type.value),
                address=normalized_address,
                label=ch.label,
                is_primary=ch.is_primary,
            )
            db.add(channel)

        db.commit()
        db.refresh(person)
        return person

    @staticmethod
    def get(db: Session, person_id: str):
        person = db.get(
            Person,
            person_id,
            options=[selectinload(Person.channels)],
        )
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")
        return person

    @staticmethod
    def list(
        db: Session,
        email: str | None,
        status: str | None,
        party_status: str | None,
        organization_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(Person)
        if email:
            query = query.filter(Person.email.ilike(f"%{email}%"))
        if status:
            query = query.filter(
                Person.status == validate_enum(status, PersonStatus, "status")
            )
        if party_status:
            query = query.filter(
                Person.party_status == validate_enum(party_status, PartyStatus, "party_status")
            )
        if organization_id:
            query = query.filter(Person.organization_id == organization_id)
        if is_active is not None:
            query = query.filter(Person.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "created_at": Person.created_at,
                "last_name": Person.last_name,
                "email": Person.email,
            },
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, person_id: str, payload: PersonUpdate):
        person = db.get(Person, person_id)
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")
        data = payload.model_dump(exclude_unset=True)
        if "email" in data:
            normalized = _normalize_email(data["email"])
            existing = _find_person_by_email(db, normalized)
            if existing and existing.id != person.id:
                raise HTTPException(status_code=409, detail="Email already belongs to another person")
            data["email"] = normalized or data["email"]
        if "phone" in data:
            normalized = _normalize_phone(data["phone"])
            existing = _find_person_by_phone(db, normalized)
            if existing and existing.id != person.id:
                raise HTTPException(status_code=409, detail="Phone already belongs to another person")
            data["phone"] = normalized
        for key, value in data.items():
            setattr(person, key, value)
        db.commit()
        db.refresh(person)
        return person

    @staticmethod
    def delete(db: Session, person_id: str):
        person = db.get(Person, person_id)
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")
        db.delete(person)
        db.commit()

    @staticmethod
    def transition_status(
        db: Session,
        person_id: str,
        new_status: PartyStatus,
        changed_by_id: UUID | None = None,
        reason: str | None = None,
    ) -> Person:
        """Validate and apply party status transition."""
        person = db.get(Person, person_id)
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")

        old_status = person.party_status
        if not _is_valid_transition(old_status, new_status):
            raise InvalidTransitionError(
                f"Cannot transition from {old_status.value} to {new_status.value}"
            )

        person.party_status = new_status

        # Log the transition
        log = PersonStatusLog(
            person_id=person.id,
            from_status=old_status,
            to_status=new_status,
            changed_by_id=changed_by_id,
            reason=reason,
        )
        db.add(log)
        db.commit()
        db.refresh(person)
        return person

    @staticmethod
    def add_channel(
        db: Session,
        person_id: str,
        payload: PersonChannelCreate,
    ) -> PersonChannel:
        """Add a communication channel to a person."""
        person = db.get(Person, person_id)
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")

        channel_type = ChannelType(payload.channel_type.value)
        normalized_address = _normalize_channel_address(channel_type, payload.address)
        if not normalized_address:
            raise HTTPException(status_code=400, detail="Channel address is required")
        existing_channel = _find_person_channel_owner(db, channel_type, normalized_address)
        if existing_channel and existing_channel.person_id != person.id:
            raise HTTPException(
                status_code=409,
                detail="Channel address already belongs to another person",
            )
        channel: PersonChannel = PersonChannel(
            person_id=person.id,
            channel_type=channel_type,
            address=normalized_address,
            label=payload.label,
            is_primary=payload.is_primary,
        )
        db.add(channel)
        db.commit()
        db.refresh(channel)
        return channel

    @staticmethod
    def merge(
        db: Session,
        source_id: UUID,
        target_id: UUID,
        merged_by_id: UUID | None = None,
    ) -> Person:
        """Merge source person into target, preserving all relationships."""
        from app.models.crm.conversation import Conversation
        from app.models.crm.sales import Lead, Quote

        source = db.get(Person, source_id)
        target = db.get(Person, target_id)

        if not source:
            raise HTTPException(status_code=404, detail="Source person not found")
        if not target:
            raise HTTPException(status_code=404, detail="Target person not found")
        if source_id == target_id:
            raise HTTPException(status_code=400, detail="Cannot merge person with itself")

        # Create snapshot of source data before merge
        source_snapshot = {
            "id": str(source.id),
            "first_name": source.first_name,
            "last_name": source.last_name,
            "email": source.email,
            "phone": source.phone,
            "party_status": source.party_status.value if source.party_status else None,
            "organization_id": str(source.organization_id) if source.organization_id else None,
        }

        # Move all relationships to target
        # 1. Move channels (dedupe by type+address)
        for channel in source.channels:
            existing = (
                db.query(PersonChannel)
                .filter(
                    PersonChannel.person_id == target.id,
                    PersonChannel.channel_type == channel.channel_type,
                    PersonChannel.address == channel.address,
                )
                .first()
            )
            if not existing:
                channel.person_id = target.id
            else:
                db.delete(channel)

        # 2. Move leads
        db.query(Lead).filter(Lead.person_id == source.id).update(
            {"person_id": target.id}, synchronize_session=False
        )

        # 3. Move quotes
        db.query(Quote).filter(Quote.person_id == source.id).update(
            {"person_id": target.id}, synchronize_session=False
        )

        # 4. Move conversations
        db.query(Conversation).filter(Conversation.person_id == source.id).update(
            {"person_id": target.id}, synchronize_session=False
        )

        # Log the merge
        merge_log = PersonMergeLog(
            source_person_id=source.id,
            target_person_id=target.id,
            merged_by_id=merged_by_id,
            source_snapshot=source_snapshot,
        )
        db.add(merge_log)

        # Soft-delete source (keep for rollback)
        source.is_active = False
        source.status = PersonStatus.archived

        db.commit()
        db.refresh(target)
        return target

    @staticmethod
    def search(
        db: Session,
        query_str: str,
        limit: int = 10,
    ):
        """Search people by name, email, phone, or channel address."""
        q = query_str.lower()
        return (
            db.query(Person)
            .outerjoin(PersonChannel)
            .filter(
                or_(
                    Person.first_name.ilike(f"%{q}%"),
                    Person.last_name.ilike(f"%{q}%"),
                    Person.email.ilike(f"%{q}%"),
                    Person.phone.ilike(f"%{q}%"),
                    PersonChannel.address.ilike(f"%{q}%"),
                )
            )
            .distinct()
            .limit(limit)
            .all()
        )

    @staticmethod
    def linked_user_labels(db: Session, person_id) -> builtins.list[str]:
        """Check for records that would block user deletion."""
        from app.models.crm.conversation import Conversation, ConversationAssignment
        from app.models.crm.sales import Lead, Quote
        from app.models.crm.team import CrmAgent
        from app.models.projects import Project, ProjectComment, ProjectTask, ProjectTaskComment
        from app.models.tickets import Ticket, TicketComment
        from app.models.workforce import WorkOrder, WorkOrderAssignment, WorkOrderNote

        checks = [
            ("CRM agent", db.query(CrmAgent.id).filter(CrmAgent.person_id == person_id)),
            ("CRM conversations", db.query(Conversation.id).filter(Conversation.person_id == person_id)),
            ("CRM assignments", db.query(ConversationAssignment.id).filter(ConversationAssignment.assigned_by_id == person_id)),
            ("CRM leads", db.query(Lead.id).filter(Lead.person_id == person_id)),
            ("CRM quotes", db.query(Quote.id).filter(Quote.person_id == person_id)),
            (
                "Tickets",
                db.query(Ticket.id).filter(
                    or_(
                        Ticket.created_by_person_id == person_id,
                        Ticket.assigned_to_person_id == person_id,
                    )
                ),
            ),
            ("Ticket comments", db.query(TicketComment.id).filter(TicketComment.author_person_id == person_id)),
            ("Work orders", db.query(WorkOrder.id).filter(WorkOrder.assigned_to_person_id == person_id)),
            ("Work order assignments", db.query(WorkOrderAssignment.id).filter(WorkOrderAssignment.person_id == person_id)),
            ("Work order notes", db.query(WorkOrderNote.id).filter(WorkOrderNote.author_person_id == person_id)),
            (
                "Projects",
                db.query(Project.id).filter(
                    or_(
                        Project.created_by_person_id == person_id,
                        Project.owner_person_id == person_id,
                        Project.manager_person_id == person_id,
                    )
                ),
            ),
            (
                "Project tasks",
                db.query(ProjectTask.id).filter(
                    or_(
                        ProjectTask.assigned_to_person_id == person_id,
                        ProjectTask.created_by_person_id == person_id,
                    )
                ),
            ),
            ("Project task comments", db.query(ProjectTaskComment.id).filter(ProjectTaskComment.author_person_id == person_id)),
            ("Project comments", db.query(ProjectComment.id).filter(ProjectComment.author_person_id == person_id)),
        ]
        linked = []
        for label, query in checks:
            if query.first():
                linked.append(label)
        return linked

    @staticmethod
    def hard_delete_user(db: Session, person_id: str) -> None:
        """Hard delete a user and all auth-related records.

        Raises HTTPException if user is active or has linked business records.
        """
        from sqlalchemy.exc import IntegrityError

        from app.models.auth import ApiKey, MFAMethod, UserCredential
        from app.models.auth import Session as AuthSession
        from app.models.rbac import PersonPermission, PersonRole
        from app.models.subscriber import ResellerUser
        from app.models.vendor import VendorUser

        person = db.get(Person, person_id)
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")
        if person.is_active:
            raise HTTPException(status_code=400, detail="Deactivate user before deleting")

        linked = People.linked_user_labels(db, person.id)
        if linked:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete user. Linked to: {', '.join(linked)}.",
            )

        try:
            db.query(UserCredential).filter(UserCredential.person_id == person.id).delete(synchronize_session=False)
            db.query(MFAMethod).filter(MFAMethod.person_id == person.id).delete(synchronize_session=False)
            db.query(AuthSession).filter(AuthSession.person_id == person.id).delete(synchronize_session=False)
            db.query(ApiKey).filter(ApiKey.person_id == person.id).delete(synchronize_session=False)
            db.query(PersonRole).filter(PersonRole.person_id == person.id).delete(synchronize_session=False)
            db.query(PersonPermission).filter(PersonPermission.person_id == person.id).delete(synchronize_session=False)
            db.query(VendorUser).filter(VendorUser.person_id == person.id).delete(synchronize_session=False)
            db.query(ResellerUser).filter(ResellerUser.person_id == person.id).delete(synchronize_session=False)
            db.delete(person)
            db.commit()
        except IntegrityError:
            db.rollback()
            linked = People.linked_user_labels(db, person.id)
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete user. Linked records exist: {', '.join(linked) if linked else 'unknown'}.",
            )


people = People()
