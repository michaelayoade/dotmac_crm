from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation
from app.models.person import ChannelType, PartyStatus, Person, PersonChannel
from app.models.subscriber import Subscriber, SubscriberStatus

NCC_IDENTITY_AMBIGUOUS_TAG = "ncc-identity-ambiguous"
NCC_QUALIFYING_SUBSCRIBER_STATUSES = frozenset(
    {
        SubscriberStatus.active,
        SubscriberStatus.suspended,
        SubscriberStatus.pending,
    }
)
NCC_ELIGIBLE_PARTY_STATUSES = frozenset({PartyStatus.customer, PartyStatus.subscriber})
_PHONE_CHANNEL_TYPES = frozenset({ChannelType.phone, ChannelType.sms, ChannelType.whatsapp})


@dataclass(frozen=True)
class NccProfileSubjectResolution:
    person: Person | None
    eligible: bool
    reason: str
    original_person_id: uuid.UUID | None
    canonical_person_id: uuid.UUID | None
    candidate_person_ids: tuple[uuid.UUID, ...] = ()
    ambiguous: bool = False
    repointed: bool = False


def _qualifying_subscriber_query(db: Session):
    return db.query(Subscriber).filter(
        Subscriber.person_id.isnot(None),
        Subscriber.is_active.is_(True),
        Subscriber.status.in_(NCC_QUALIFYING_SUBSCRIBER_STATUSES),
    )


def _has_qualifying_subscriber(db: Session, person_id: uuid.UUID) -> bool:
    return _qualifying_subscriber_query(db).filter(Subscriber.person_id == person_id).first() is not None


def _normalize_email(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized or None


def _normalize_phone(value: str | None) -> str | None:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return f"+{digits}" if digits else None


def _verified_contact_keys(db: Session, person: Person) -> tuple[set[str], set[str]]:
    emails: set[str] = set()
    phones: set[str] = set()
    if person.email_verified:
        normalized_email = _normalize_email(person.email)
        if normalized_email:
            emails.add(normalized_email)

    channels = (
        db.query(PersonChannel)
        .filter(
            PersonChannel.person_id == person.id,
            PersonChannel.is_verified.is_(True),
            PersonChannel.channel_type.in_({ChannelType.email, *_PHONE_CHANNEL_TYPES}),
        )
        .all()
    )
    for channel in channels:
        if channel.channel_type == ChannelType.email:
            normalized_email = _normalize_email(channel.address)
            if normalized_email:
                emails.add(normalized_email)
        else:
            normalized_phone = _normalize_phone(channel.address)
            if normalized_phone:
                phones.add(normalized_phone)
    return emails, phones


def _external_identity_candidates(db: Session, person: Person) -> set[uuid.UUID]:
    metadata = person.metadata_ if isinstance(person.metadata_, dict) else {}
    identity_values = {
        str(metadata.get(key) or "").strip()
        for key in ("selfcare_id", "selfcare_subscriber_id")
        if str(metadata.get(key) or "").strip()
    }
    predicates = []
    if identity_values:
        predicates.extend(
            [
                Subscriber.external_id.in_(identity_values),
                Subscriber.subscriber_number.in_(identity_values),
                Subscriber.sync_metadata["selfcare_id"].as_string().in_(identity_values),
                Subscriber.sync_metadata["selfcare_subscriber_number"].as_string().in_(identity_values),
            ]
        )

    # Selfcare may return its CRM identity marker in subscriber metadata even
    # when the local Subscriber.person_id link drifted to another Person.
    predicates.append(Subscriber.sync_metadata["crm_person_id"].as_string() == str(person.id))
    rows = (
        _qualifying_subscriber_query(db)
        .join(Person, Person.id == Subscriber.person_id)
        .filter(Person.is_active.is_(True), or_(*predicates))
        .with_entities(Subscriber.person_id)
        .distinct()
        .all()
    )
    return {person_id for (person_id,) in rows if person_id and person_id != person.id}


def _verified_contact_candidates(db: Session, person: Person) -> set[uuid.UUID]:
    emails, phones = _verified_contact_keys(db, person)
    if not emails and not phones:
        return set()

    linked_person_ids = (
        select(Subscriber.person_id)
        .where(
            Subscriber.person_id.isnot(None),
            Subscriber.is_active.is_(True),
            Subscriber.status.in_(NCC_QUALIFYING_SUBSCRIBER_STATUSES),
        )
        .distinct()
    )
    candidate_ids: set[uuid.UUID] = set()
    if emails:
        person_rows = (
            db.query(Person.id)
            .filter(
                Person.id.in_(linked_person_ids),
                Person.id != person.id,
                Person.is_active.is_(True),
                Person.email_verified.is_(True),
                func.lower(func.trim(Person.email)).in_(emails),
            )
            .all()
        )
        candidate_ids.update(person_id for (person_id,) in person_rows)

        channel_rows = (
            db.query(PersonChannel.person_id)
            .join(Person, Person.id == PersonChannel.person_id)
            .filter(
                PersonChannel.person_id.in_(linked_person_ids),
                PersonChannel.person_id != person.id,
                PersonChannel.channel_type == ChannelType.email,
                PersonChannel.is_verified.is_(True),
                Person.is_active.is_(True),
                func.lower(func.trim(PersonChannel.address)).in_(emails),
            )
            .distinct()
            .all()
        )
        candidate_ids.update(person_id for (person_id,) in channel_rows)

    if phones:
        channel_rows = (
            db.query(PersonChannel.person_id)
            .join(Person, Person.id == PersonChannel.person_id)
            .filter(
                PersonChannel.person_id.in_(linked_person_ids),
                PersonChannel.person_id != person.id,
                PersonChannel.channel_type.in_(_PHONE_CHANNEL_TYPES),
                PersonChannel.is_verified.is_(True),
                PersonChannel.address.in_(phones),
                Person.is_active.is_(True),
            )
            .distinct()
            .all()
        )
        candidate_ids.update(person_id for (person_id,) in channel_rows)
    return candidate_ids


def resolve_ncc_profile_subject(db: Session, *, conversation: Conversation) -> NccProfileSubjectResolution:
    """Resolve NCC eligibility and the one Person that may receive profile writes.

    The function only repoints ``conversation.person_id`` when strong identifiers
    resolve to exactly one current subscriber-linked Person. The caller owns the
    surrounding transaction and must commit the repoint with the intake state.
    """
    original_person_id = conversation.person_id
    person = db.get(Person, original_person_id) if original_person_id else None
    if person is None:
        return NccProfileSubjectResolution(
            person=None,
            eligible=False,
            reason="person_missing",
            original_person_id=original_person_id,
            canonical_person_id=None,
        )

    if _has_qualifying_subscriber(db, person.id):
        return NccProfileSubjectResolution(
            person=person,
            eligible=True,
            reason="direct_current_subscriber",
            original_person_id=person.id,
            canonical_person_id=person.id,
        )

    if person.party_status in NCC_ELIGIBLE_PARTY_STATUSES:
        return NccProfileSubjectResolution(
            person=person,
            eligible=True,
            reason="eligible_party_status",
            original_person_id=person.id,
            canonical_person_id=person.id,
        )

    candidate_ids = _external_identity_candidates(db, person) | _verified_contact_candidates(db, person)
    ordered_candidate_ids = tuple(sorted(candidate_ids, key=str))
    if len(ordered_candidate_ids) > 1:
        return NccProfileSubjectResolution(
            person=person,
            eligible=False,
            reason="ambiguous_identity",
            original_person_id=person.id,
            canonical_person_id=person.id,
            candidate_person_ids=ordered_candidate_ids,
            ambiguous=True,
        )

    if len(ordered_candidate_ids) == 1:
        canonical_person = db.get(Person, ordered_candidate_ids[0])
        if canonical_person is not None:
            conversation.person_id = canonical_person.id
            db.flush()
            return NccProfileSubjectResolution(
                person=canonical_person,
                eligible=True,
                reason="strong_identifier_redirect",
                original_person_id=person.id,
                canonical_person_id=canonical_person.id,
                candidate_person_ids=ordered_candidate_ids,
                repointed=True,
            )

    return NccProfileSubjectResolution(
        person=person,
        eligible=False,
        reason="not_customer",
        original_person_id=person.id,
        canonical_person_id=person.id,
    )
