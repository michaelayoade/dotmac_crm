"""
Subscriber service for managing synced subscriber data.

This service handles subscriber accounts synced from external billing systems
like Splynx, UCRM, WHMCS, or custom platforms.
"""

from __future__ import annotations

import builtins
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.services.common import apply_ordering

from app.models.person import ChannelType, PartyStatus, Person, PersonChannel
from app.models.subscriber import AccountType, Organization, Subscriber, SubscriberStatus


class SubscriberManager:
    """Manager for subscriber operations."""

    _ordering_fields = {
        "created_at": Subscriber.created_at,
        "updated_at": Subscriber.updated_at,
        "subscriber_number": Subscriber.subscriber_number,
        "status": Subscriber.status,
    }

    def list(
        self,
        db: Session,
        *,
        search: str | None = None,
        status: SubscriberStatus | None = None,
        external_system: str | None = None,
        person_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        is_active: bool | None = True,
        order_by: str = "created_at",
        order_dir: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> list[Subscriber]:
        """List subscribers with filters."""
        query = db.query(Subscriber).options(
            joinedload(Subscriber.person),
            joinedload(Subscriber.organization),
        )

        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    Subscriber.subscriber_number.ilike(search_term),
                    Subscriber.account_number.ilike(search_term),
                    Subscriber.service_name.ilike(search_term),
                    Subscriber.external_id.ilike(search_term),
                )
            )

        if status:
            query = query.filter(Subscriber.status == status)

        if external_system:
            query = query.filter(Subscriber.external_system == external_system)

        if person_id:
            query = query.filter(Subscriber.person_id == person_id)

        if organization_id:
            query = query.filter(Subscriber.organization_id == organization_id)

        if is_active is not None:
            query = query.filter(Subscriber.is_active == is_active)

        query = apply_ordering(query, order_by, order_dir, self._ordering_fields)
        return query.offset(offset).limit(limit).all()

    def count(
        self,
        db: Session,
        *,
        search: str | None = None,
        status: SubscriberStatus | None = None,
        external_system: str | None = None,
        is_active: bool | None = True,
    ) -> int:
        """Count subscribers with filters."""
        query = db.query(func.count(Subscriber.id))

        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    Subscriber.subscriber_number.ilike(search_term),
                    Subscriber.account_number.ilike(search_term),
                    Subscriber.service_name.ilike(search_term),
                    Subscriber.external_id.ilike(search_term),
                )
            )

        if status:
            query = query.filter(Subscriber.status == status)

        if external_system:
            query = query.filter(Subscriber.external_system == external_system)

        if is_active is not None:
            query = query.filter(Subscriber.is_active == is_active)

        return query.scalar() or 0

    def get(self, db: Session, subscriber_id: uuid.UUID) -> Subscriber | None:
        """Get subscriber by ID."""
        return (
            db.query(Subscriber)
            .options(
                joinedload(Subscriber.person),
                joinedload(Subscriber.organization),
                joinedload(Subscriber.tickets),
                joinedload(Subscriber.work_orders),
                joinedload(Subscriber.projects),
            )
            .filter(Subscriber.id == subscriber_id)
            .first()
        )

    def get_by_external_id(self, db: Session, external_system: str, external_id: str) -> Subscriber | None:
        """Get subscriber by external system reference."""
        return (
            db.query(Subscriber)
            .filter(
                Subscriber.external_system == external_system,
                Subscriber.external_id == external_id,
            )
            .first()
        )

    def get_by_subscriber_number(self, db: Session, subscriber_number: str) -> Subscriber | None:
        """Get subscriber by subscriber number."""
        return db.query(Subscriber).filter(Subscriber.subscriber_number == subscriber_number).first()

    def create(self, db: Session, data: dict[str, Any]) -> Subscriber:
        """Create a new subscriber."""
        subscriber = Subscriber(**data)
        db.add(subscriber)
        db.commit()
        db.refresh(subscriber)
        return subscriber

    def update(self, db: Session, subscriber: Subscriber, data: dict[str, Any]) -> Subscriber:
        """Update an existing subscriber."""
        for key, value in data.items():
            if hasattr(subscriber, key):
                setattr(subscriber, key, value)
        subscriber.updated_at = datetime.now(UTC)
        db.commit()
        db.refresh(subscriber)
        return subscriber

    def delete(self, db: Session, subscriber: Subscriber) -> None:
        """Soft delete a subscriber."""
        subscriber.is_active = False
        subscriber.updated_at = datetime.now(UTC)
        db.commit()

    def hard_delete(self, db: Session, subscriber: Subscriber) -> None:
        """Permanently delete a subscriber."""
        db.delete(subscriber)
        db.commit()

    # Sync operations
    def sync_from_external(
        self,
        db: Session,
        external_system: str,
        external_id: str,
        data: dict[str, Any],
    ) -> Subscriber:
        """
        Sync subscriber data from external system.
        Creates or updates subscriber based on external_id.
        """
        subscriber = self.get_by_external_id(db, external_system, external_id)

        sync_data = {
            "external_system": external_system,
            "external_id": external_id,
            "last_synced_at": datetime.now(UTC),
            "sync_error": None,
            **data,
        }

        if subscriber:
            return self.update(db, subscriber, sync_data)
        else:
            return self.create(db, sync_data)

    def mark_sync_error(self, db: Session, subscriber: Subscriber, error: str) -> Subscriber:
        """Mark a sync error on subscriber."""
        subscriber.sync_error = error[:500] if error else None
        subscriber.last_synced_at = datetime.now(UTC)
        db.commit()
        db.refresh(subscriber)
        return subscriber

    def get_stats(self, db: Session) -> dict[str, int]:
        """Get subscriber statistics."""
        total = db.query(func.count(Subscriber.id)).filter(Subscriber.is_active.is_(True)).scalar() or 0

        by_status = {}
        for status in SubscriberStatus:
            count = (
                db.query(func.count(Subscriber.id))
                .filter(
                    Subscriber.is_active.is_(True),
                    Subscriber.status == status,
                )
                .scalar()
                or 0
            )
            by_status[status.value] = count

        return {
            "total": total,
            **by_status,
        }

    def link_to_person(self, db: Session, subscriber: Subscriber, person_id: uuid.UUID) -> Subscriber:
        """Link subscriber to a person contact."""
        subscriber.person_id = person_id
        subscriber.updated_at = datetime.now(UTC)
        db.commit()
        db.refresh(subscriber)
        return subscriber

    def link_to_organization(self, db: Session, subscriber: Subscriber, organization_id: uuid.UUID) -> Subscriber:
        """Link subscriber to an organization."""
        subscriber.organization_id = organization_id
        subscriber.updated_at = datetime.now(UTC)
        db.commit()
        db.refresh(subscriber)
        return subscriber

    def reconcile_external_people_links(
        self,
        db: Session,
        *,
        external_system: str = "splynx",
        clear_duplicate_metadata: bool = True,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """
        Reconcile subscriber->person links for one external system.

        Strategy:
        - Match subscriber.external_id to people.metadata.splynx_id
        - Pick a deterministic best person when duplicates exist
        - Backfill missing person metadata from linked subscribers
        - Optionally clear duplicate metadata on non-selected records
        """
        results = {
            "scanned_subscribers": 0,
            "matched_subscribers": 0,
            "linked_subscribers": 0,
            "organization_backfilled": 0,
            "person_metadata_updated": 0,
            "duplicate_metadata_groups": 0,
            "duplicate_metadata_cleared": 0,
            "unmatched_subscribers": 0,
        }

        subscribers = (
            db.query(Subscriber)
            .filter(
                Subscriber.external_system == external_system,
                Subscriber.external_id.isnot(None),
                Subscriber.is_active.is_(True),
            )
            .all()
        )
        results["scanned_subscribers"] = len(subscribers)
        if not subscribers:
            return results

        external_ids = {str(s.external_id).strip() for s in subscribers if s.external_id}
        if not external_ids:
            return results

        # Splynx ID is stored in people.metadata.splynx_id today.
        people = (
            db.query(Person).filter(func.json_extract_path_text(Person.metadata_, "splynx_id").in_(external_ids)).all()
        )

        people_by_splynx_id: dict[str, list[Person]] = {}
        people_by_id = {p.id: p for p in people}
        for person in people:
            if not isinstance(person.metadata_, dict):
                continue
            splynx_id = person.metadata_.get("splynx_id")
            if not splynx_id:
                continue
            key = str(splynx_id).strip()
            if not key:
                continue
            people_by_splynx_id.setdefault(key, []).append(person)

        candidate_ids = [p.id for p in people]
        active_link_counts: dict[uuid.UUID, int] = {}
        if candidate_ids:
            rows = (
                db.query(Subscriber.person_id, func.count(Subscriber.id))
                .filter(
                    Subscriber.person_id.in_(candidate_ids),
                    Subscriber.is_active.is_(True),
                )
                .group_by(Subscriber.person_id)
                .all()
            )
            active_link_counts = {person_id: int(count) for person_id, count in rows if person_id}

        status_rank = {
            PartyStatus.lead: 0,
            PartyStatus.contact: 1,
            PartyStatus.customer: 2,
            PartyStatus.subscriber: 3,
        }
        splynx_ids_by_person: dict[uuid.UUID, set[str]] = {}
        for s in subscribers:
            if not s.person_id or not s.external_id:
                continue
            splynx_ids_by_person.setdefault(s.person_id, set()).add(str(s.external_id).strip())

        def choose_best_person(candidates: list[Person], preferred_person_id: uuid.UUID | None) -> Person:
            def score(person: Person) -> tuple[int, int, int, int, datetime]:
                return (
                    1 if preferred_person_id and person.id == preferred_person_id else 0,
                    active_link_counts.get(person.id, 0),
                    status_rank.get(person.party_status, 0),
                    1 if person.organization_id else 0,
                    person.updated_at or person.created_at or datetime.min.replace(tzinfo=UTC),
                )

            return max(candidates, key=score)

        for subscriber in subscribers:
            external_id = str(subscriber.external_id).strip() if subscriber.external_id else ""
            if not external_id:
                continue

            candidates = people_by_splynx_id.get(external_id, [])
            preferred_person = people_by_id.get(subscriber.person_id) if subscriber.person_id else None

            if len(candidates) > 1:
                results["duplicate_metadata_groups"] += 1

            selected: Person | None = None
            if candidates:
                selected = choose_best_person(candidates, subscriber.person_id)
                results["matched_subscribers"] += 1
            elif preferred_person:
                selected = preferred_person
            else:
                results["unmatched_subscribers"] += 1
                continue

            if subscriber.person_id != selected.id:
                subscriber.person_id = selected.id
                results["linked_subscribers"] += 1

            if not subscriber.organization_id and selected.organization_id:
                subscriber.organization_id = selected.organization_id
                results["organization_backfilled"] += 1

            selected_meta = dict(selected.metadata_ or {})
            person_sids = splynx_ids_by_person.get(selected.id, set())
            # metadata.splynx_id is a single slot; avoid flip-flopping for multi-subscriber people.
            if len(person_sids) <= 1 and selected_meta.get("splynx_id") != external_id:
                selected_meta["splynx_id"] = external_id
                selected.metadata_ = selected_meta
                results["person_metadata_updated"] += 1

            if clear_duplicate_metadata and len(candidates) > 1:
                for person in candidates:
                    if person.id == selected.id:
                        continue
                    if active_link_counts.get(person.id, 0) > 0:
                        continue
                    person_meta = dict(person.metadata_ or {})
                    if person_meta.get("splynx_id") == external_id:
                        person_meta.pop("splynx_id", None)
                        person.metadata_ = person_meta or None
                        results["duplicate_metadata_cleared"] += 1

        if dry_run:
            db.rollback()
        else:
            db.commit()

        return results

    def reconcile_party_status_from_subscribers(
        self,
        db: Session,
        *,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """
        Normalize Person.party_status based on active subscriber linkage.

        Rules:
        - Any person linked to >=1 active subscriber -> subscriber
        - Person currently subscriber with no active subscriber -> customer
        """
        results = {
            "upgraded_to_subscriber": 0,
            "downgraded_to_customer": 0,
            "flagged_people_as_reseller": 0,
            "unflagged_people_as_reseller": 0,
            "promoted_orgs_to_reseller": 0,
            "created_reseller_orgs": 0,
            "linked_people_to_reseller_org": 0,
            "deactivated_empty_reseller_orgs": 0,
        }

        linked_rows = (
            db.query(Subscriber.person_id, func.count(Subscriber.id))
            .filter(
                Subscriber.is_active.is_(True),
                Subscriber.person_id.isnot(None),
            )
            .group_by(Subscriber.person_id)
            .all()
        )
        linked_person_counts = {person_id: int(count) for person_id, count in linked_rows if person_id}
        linked_person_ids = set(linked_person_counts.keys())

        # Business rule: any person with a Splynx ID is considered a subscriber.
        splynx_people_rows = (
            db.query(Person.id)
            .filter(func.json_extract_path_text(Person.metadata_, "splynx_id").isnot(None))
            .filter(func.json_extract_path_text(Person.metadata_, "splynx_id") != "")
            .all()
        )
        splynx_person_ids = {person_id for (person_id,) in splynx_people_rows if person_id}
        subscriber_person_ids = linked_person_ids | splynx_person_ids

        def normalize_email(value: str | None) -> str | None:
            if not value:
                return None
            normalized = value.strip().lower()
            return normalized or None

        def normalize_phone(value: str | None) -> str | None:
            if not value:
                return None
            digits = "".join(ch for ch in value if ch.isdigit())
            return digits or None

        def normalize_name(person: Person) -> str:
            raw = (person.display_name or f"{person.first_name or ''} {person.last_name or ''}".strip()).strip().lower()
            if not raw:
                return ""
            collapsed = "".join(ch if ch.isalnum() else " " for ch in raw)
            return " ".join(collapsed.split())

        candidate_people_by_id: dict[uuid.UUID, Person] = {}
        if subscriber_person_ids:
            for person in db.query(Person).filter(Person.id.in_(subscriber_person_ids)).all():
                candidate_people_by_id[person.id] = person

        contact_point_to_people: dict[str, set[uuid.UUID]] = {}
        person_name_keys: dict[uuid.UUID, str] = {}

        for person in candidate_people_by_id.values():
            person_name_keys[person.id] = normalize_name(person)
            email = normalize_email(person.email)
            phone = normalize_phone(person.phone)
            if email:
                contact_point_to_people.setdefault(f"email:{email}", set()).add(person.id)
            if phone:
                contact_point_to_people.setdefault(f"phone:{phone}", set()).add(person.id)

        if candidate_people_by_id:
            channel_rows = (
                db.query(PersonChannel.person_id, PersonChannel.channel_type, PersonChannel.address)
                .filter(
                    PersonChannel.person_id.in_(list(candidate_people_by_id.keys())),
                    PersonChannel.channel_type.in_([ChannelType.email, ChannelType.phone, ChannelType.whatsapp]),
                )
                .all()
            )
            for person_id, channel_type, address in channel_rows:
                if not person_id or not address:
                    continue
                if channel_type == ChannelType.email:
                    normalized_address = normalize_email(address)
                    if normalized_address:
                        contact_point_to_people.setdefault(f"email:{normalized_address}", set()).add(person_id)
                else:
                    normalized_address = normalize_phone(address)
                    if normalized_address:
                        contact_point_to_people.setdefault(f"phone:{normalized_address}", set()).add(person_id)

        reseller_person_ids: set[uuid.UUID] = set()
        reseller_contact_groups: dict[str, set[uuid.UUID]] = {}
        for contact_key, person_ids in contact_point_to_people.items():
            if len(person_ids) < 2:
                continue
            distinct_names = {person_name_keys.get(person_id, "") for person_id in person_ids}
            distinct_names.discard("")
            if len(distinct_names) >= 2:
                reseller_person_ids.update(person_ids)
                reseller_contact_groups[contact_key] = set(person_ids)

        # Build connected reseller clusters:
        # a person can appear in multiple qualifying keys (email/phone), and all connected
        # people should resolve to one reseller organization.
        parent: dict[uuid.UUID, uuid.UUID] = {}

        def make_set(node: uuid.UUID) -> None:
            if node not in parent:
                parent[node] = node

        def find(node: uuid.UUID) -> uuid.UUID:
            root = parent[node]
            while root != parent[root]:
                root = parent[root]
            while node != root:
                prev = parent[node]
                parent[node] = root
                node = prev
            return root

        def union(a: uuid.UUID, b: uuid.UUID) -> None:
            root_a = find(a)
            root_b = find(b)
            if root_a != root_b:
                parent[root_b] = root_a

        for person_ids in reseller_contact_groups.values():
            people_list = list(person_ids)
            for person_id in people_list:
                make_set(person_id)
            base = people_list[0]
            for person_id in people_list[1:]:
                union(base, person_id)

        cluster_members: dict[uuid.UUID, set[uuid.UUID]] = {}
        for person_id in parent:
            root = find(person_id)
            cluster_members.setdefault(root, set()).add(person_id)

        cluster_keys: dict[uuid.UUID, list[str]] = {}
        for contact_key, person_ids in reseller_contact_groups.items():
            if not person_ids:
                continue
            any_person_id = next(iter(person_ids))
            if any_person_id not in parent:
                continue
            root = find(any_person_id)
            cluster_keys.setdefault(root, []).append(contact_key)

        # Flag/unflag people reseller marker from active-subscriber multiplicity.
        people_to_check: dict[uuid.UUID, Person] = {}
        if subscriber_person_ids:
            for person in db.query(Person).filter(Person.id.in_(subscriber_person_ids)).all():
                people_to_check[person.id] = person
        for person in (
            db.query(Person).filter(func.json_extract_path_text(Person.metadata_, "is_reseller") == "true").all()
        ):
            people_to_check[person.id] = person

        for person in people_to_check.values():
            metadata = dict(person.metadata_ or {})
            should_be_reseller = person.id in reseller_person_ids
            is_reseller = bool(metadata.get("is_reseller"))
            if should_be_reseller and not is_reseller:
                metadata["is_reseller"] = True
                person.metadata_ = metadata
                results["flagged_people_as_reseller"] += 1
            elif not should_be_reseller and is_reseller:
                metadata.pop("is_reseller", None)
                person.metadata_ = metadata or None
                results["unflagged_people_as_reseller"] += 1

        # Ensure reseller contacts are attached to reseller organizations per cluster.
        all_cluster_org_ids = {
            person.organization_id
            for person in people_to_check.values()
            if person.id in reseller_person_ids and person.organization_id
        }
        org_by_id: dict[uuid.UUID, Organization] = {}
        org_member_counts: dict[uuid.UUID, int] = {}
        if all_cluster_org_ids:
            org_by_id = {
                org.id: org for org in db.query(Organization).filter(Organization.id.in_(all_cluster_org_ids)).all()
            }
            org_member_counts = {
                org_id: int(count)
                for org_id, count in (
                    db.query(Person.organization_id, func.count(Person.id))
                    .filter(Person.organization_id.in_(all_cluster_org_ids))
                    .group_by(Person.organization_id)
                    .all()
                )
                if org_id
            }

        def is_transient_single_person_org(org: Organization, owner: Person) -> bool:
            member_count = org_member_counts.get(org.id, 0)
            if member_count != 1:
                return False
            if org.account_type != AccountType.reseller:
                return False
            if org.erp_id or org.domain or org.website or org.legal_name or org.tax_id:
                return False
            display_name = (owner.display_name or f"{owner.first_name or ''} {owner.last_name or ''}".strip()).strip()
            return org.name == display_name or org.name == owner.email or org.name.startswith("Reseller ")

        for root, member_ids in cluster_members.items():
            raw_members = [people_to_check.get(person_id) for person_id in member_ids]
            members: list[Person] = [person for person in raw_members if person is not None]
            if not members:
                continue

            org_usage: dict[uuid.UUID, int] = {}
            for person in members:
                if person.organization_id:
                    org_usage[person.organization_id] = org_usage.get(person.organization_id, 0) + 1

            preferred_org: Organization | None = None
            if org_usage:
                ranked_org_ids = sorted(
                    org_usage.items(),
                    key=lambda item: (
                        1 if org_by_id.get(item[0]) and org_by_id[item[0]].account_type == AccountType.reseller else 0,
                        item[1],
                    ),
                    reverse=True,
                )
                preferred_org = org_by_id.get(ranked_org_ids[0][0])

            if not preferred_org:
                primary_key = ""
                key_candidates = cluster_keys.get(root, [])
                if key_candidates:
                    primary_key = sorted(
                        key_candidates,
                        key=lambda key: (key.startswith("email:"), len(reseller_contact_groups.get(key, set()))),
                        reverse=True,
                    )[0]
                org_name = f"Reseller {primary_key.split(':', 1)[1]}" if primary_key else f"Reseller {str(root)[:8]}"
                preferred_org = Organization(
                    name=org_name[:160],
                    account_type=AccountType.reseller,
                    is_active=True,
                    metadata_={"auto_created_by": "subscriber_reconcile", "reseller_key": primary_key or None},
                )
                db.add(preferred_org)
                db.flush()
                org_by_id[preferred_org.id] = preferred_org
                org_member_counts[preferred_org.id] = 0
                results["created_reseller_orgs"] += 1

            if preferred_org.account_type != AccountType.reseller:
                preferred_org.account_type = AccountType.reseller
                results["promoted_orgs_to_reseller"] += 1

            for person in members:
                if person.organization_id == preferred_org.id:
                    continue
                if person.organization_id is None:
                    person.organization_id = preferred_org.id
                    results["linked_people_to_reseller_org"] += 1
                    org_member_counts[preferred_org.id] = org_member_counts.get(preferred_org.id, 0) + 1
                    continue

                current_org = org_by_id.get(person.organization_id)
                if current_org and is_transient_single_person_org(current_org, person):
                    person.organization_id = preferred_org.id
                    results["linked_people_to_reseller_org"] += 1
                    org_member_counts[current_org.id] = max(0, org_member_counts.get(current_org.id, 1) - 1)
                    org_member_counts[preferred_org.id] = org_member_counts.get(preferred_org.id, 0) + 1

        orphan_reseller_orgs = (
            db.query(Organization)
            .outerjoin(Person, Person.organization_id == Organization.id)
            .filter(Organization.account_type == AccountType.reseller)
            .group_by(Organization.id)
            .having(func.count(Person.id) == 0)
            .all()
        )
        for org in orphan_reseller_orgs:
            metadata = dict(org.metadata_ or {})
            auto_created = metadata.get("auto_created_by") == "subscriber_reconcile"
            lightweight = not (org.erp_id or org.domain or org.website or org.legal_name or org.tax_id)
            if auto_created or lightweight:
                org.is_active = False
                org.account_type = AccountType.other
                results["deactivated_empty_reseller_orgs"] += 1

        if subscriber_person_ids:
            linked_people = db.query(Person).filter(Person.id.in_(subscriber_person_ids)).all()
            for person in linked_people:
                if person.party_status != PartyStatus.subscriber:
                    person.party_status = PartyStatus.subscriber
                    results["upgraded_to_subscriber"] += 1

        stale_query = db.query(Person).filter(Person.party_status == PartyStatus.subscriber)
        if subscriber_person_ids:
            stale_query = stale_query.filter(~Person.id.in_(subscriber_person_ids))
        stale_subscribers = stale_query.all()
        for person in stale_subscribers:
            person.party_status = PartyStatus.customer
            results["downgraded_to_customer"] += 1

        if dry_run:
            db.rollback()
        else:
            db.commit()

        return results

    def list_for_reseller(self, db: Session, reseller_org_id: uuid.UUID) -> builtins.list[Subscriber]:
        """List all subscribers under a reseller org and its child orgs."""
        child_ids = db.query(Organization.id).filter(Organization.parent_id == reseller_org_id).all()
        org_ids = [reseller_org_id] + [c[0] for c in child_ids]
        return (
            db.query(Subscriber)
            .options(joinedload(Subscriber.person), joinedload(Subscriber.organization))
            .filter(Subscriber.organization_id.in_(org_ids))
            .order_by(Subscriber.created_at.desc())
            .all()
        )


# Singleton instance
subscriber = SubscriberManager()
