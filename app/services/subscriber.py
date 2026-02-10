"""
Subscriber service for managing synced subscriber data.

This service handles subscriber accounts synced from external billing systems
like Splynx, UCRM, WHMCS, or custom platforms.
"""
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.models.subscriber import Subscriber, SubscriberStatus


class SubscriberManager:
    """Manager for subscriber operations."""

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

        query = query.order_by(Subscriber.created_at.desc())
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

    def get_by_external_id(
        self, db: Session, external_system: str, external_id: str
    ) -> Subscriber | None:
        """Get subscriber by external system reference."""
        return (
            db.query(Subscriber)
            .filter(
                Subscriber.external_system == external_system,
                Subscriber.external_id == external_id,
            )
            .first()
        )

    def get_by_subscriber_number(
        self, db: Session, subscriber_number: str
    ) -> Subscriber | None:
        """Get subscriber by subscriber number."""
        return (
            db.query(Subscriber)
            .filter(Subscriber.subscriber_number == subscriber_number)
            .first()
        )

    def create(self, db: Session, data: dict[str, Any]) -> Subscriber:
        """Create a new subscriber."""
        subscriber = Subscriber(**data)
        db.add(subscriber)
        db.commit()
        db.refresh(subscriber)
        return subscriber

    def update(
        self, db: Session, subscriber: Subscriber, data: dict[str, Any]
    ) -> Subscriber:
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

    def mark_sync_error(
        self, db: Session, subscriber: Subscriber, error: str
    ) -> Subscriber:
        """Mark a sync error on subscriber."""
        subscriber.sync_error = error[:500] if error else None
        subscriber.last_synced_at = datetime.now(UTC)
        db.commit()
        db.refresh(subscriber)
        return subscriber

    def get_stats(self, db: Session) -> dict[str, int]:
        """Get subscriber statistics."""
        total = db.query(func.count(Subscriber.id)).filter(
            Subscriber.is_active.is_(True)
        ).scalar() or 0

        by_status = {}
        for status in SubscriberStatus:
            count = db.query(func.count(Subscriber.id)).filter(
                Subscriber.is_active.is_(True),
                Subscriber.status == status,
            ).scalar() or 0
            by_status[status.value] = count

        return {
            "total": total,
            **by_status,
        }

    def link_to_person(
        self, db: Session, subscriber: Subscriber, person_id: uuid.UUID
    ) -> Subscriber:
        """Link subscriber to a person contact."""
        subscriber.person_id = person_id
        subscriber.updated_at = datetime.now(UTC)
        db.commit()
        db.refresh(subscriber)
        return subscriber

    def link_to_organization(
        self, db: Session, subscriber: Subscriber, organization_id: uuid.UUID
    ) -> Subscriber:
        """Link subscriber to an organization."""
        subscriber.organization_id = organization_id
        subscriber.updated_at = datetime.now(UTC)
        db.commit()
        db.refresh(subscriber)
        return subscriber


# Singleton instance
subscriber = SubscriberManager()
