"""One ticket for an infrastructure fault (cabinet/OLT/switch/router) that
affects many customers.

Resolves the affected customers from the sub app's Zabbix-linked topology (via
``selfcare.fetch_affected_subscribers``) — with a manual-selection override and
augmentation, because the topology impact is only complete where the e2e chain
(subscriber → ONT → OLT → device-graph → core) is established. The parent
ticket records the asset, the affected set, and the impact coverage; each
affected customer is notified on open and again on resolve, deduped and logged
via ``SubscriberNotificationLog``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.models.subscriber import Subscriber
from app.models.tickets import TicketPriority, TicketStatus
from app.schemas.tickets import TicketCreate, TicketUpdate
from app.services import selfcare
from app.services.common import coerce_uuid
from app.services.subscriber_notifications import queue_bulk_subscriber_notifications
from app.services.tickets import tickets as tickets_service

logger = logging.getLogger(__name__)

INFRASTRUCTURE_TAG = "infrastructure"


def _match_by_subscriber_number(db, numbers: list[str]) -> dict[str, Subscriber]:
    wanted = [n for n in numbers if n]
    if not wanted:
        return {}
    rows = db.query(Subscriber).filter(Subscriber.subscriber_number.in_(wanted), Subscriber.is_active.is_(True)).all()
    return {s.subscriber_number: s for s in rows if s.subscriber_number}


class InfrastructureTickets:
    @staticmethod
    def resolve_affected(
        db,
        *,
        node_id: str | None = None,
        basestation_id: str | None = None,
        olt_id: str | None = None,
        pon_port_id: str | None = None,
        manual_subscriber_ids: list[str | UUID] | None = None,
    ) -> dict[str, Any]:
        """Affected CRM subscriber ids = topology impact (mapped by subscriber
        number) combined with manual selections. Never trusts topology blindly:
        returns the coverage block and topology subscribers not matched in the CRM."""
        crm_ids: list[UUID] = []
        coverage: dict[str, Any] = {}
        unmatched: list[str] = []
        topology_count = 0

        if node_id or basestation_id or olt_id or pon_port_id:
            impact = selfcare.fetch_affected_subscribers(
                db,
                node_id=node_id,
                basestation_id=basestation_id,
                olt_id=olt_id,
                pon_port_id=pon_port_id,
            )
            coverage = impact.get("coverage") or {}
            rows = impact.get("subscribers") or []
            topology_count = len(rows)
            matched = _match_by_subscriber_number(db, [r.get("subscriber_number") for r in rows])
            for row in rows:
                number = row.get("subscriber_number")
                sub = matched.get(number) if number else None
                if sub is not None:
                    crm_ids.append(sub.id)
                else:
                    unmatched.append(number or row.get("id"))

        for sid in manual_subscriber_ids or []:
            with_uuid = coerce_uuid(str(sid))
            if with_uuid is not None:
                crm_ids.append(with_uuid)

        seen: set[UUID] = set()
        deduped: list[UUID] = []
        for cid in crm_ids:
            if cid in seen:
                continue
            seen.add(cid)
            deduped.append(cid)

        return {
            "crm_subscriber_ids": deduped,
            "coverage": coverage,
            "topology_count": topology_count,
            "matched_count": len(deduped),
            "unmatched_subscriber_numbers": unmatched,
        }

    @staticmethod
    def create(
        db,
        *,
        title: str,
        description: str | None = None,
        node_id: str | None = None,
        basestation_id: str | None = None,
        olt_id: str | None = None,
        pon_port_id: str | None = None,
        manual_subscriber_ids: list[str | UUID] | None = None,
        asset_label: str | None = None,
        priority: TicketPriority = TicketPriority.high,
        actor_id: str | UUID | None = None,
        region: str | None = None,
        notify: bool = True,
        channel: str = "email",
        email_subject: str | None = None,
        email_body: str | None = None,
        sms_body: str | None = None,
    ) -> dict[str, Any]:
        affected = InfrastructureTickets.resolve_affected(
            db,
            node_id=node_id,
            basestation_id=basestation_id,
            olt_id=olt_id,
            pon_port_id=pon_port_id,
            manual_subscriber_ids=manual_subscriber_ids,
        )
        ids = affected["crm_subscriber_ids"]

        ticket = tickets_service.create(
            db,
            TicketCreate(
                title=title,
                description=description,
                ticket_type="infrastructure",
                tags=[INFRASTRUCTURE_TAG],
                priority=priority,
                region=region,
                metadata_={
                    "infrastructure": True,
                    "asset": {
                        "node_id": node_id,
                        "basestation_id": basestation_id,
                        "olt_id": olt_id,
                        "pon_port_id": pon_port_id,
                        "label": asset_label,
                    },
                    "affected_subscriber_ids": [str(i) for i in ids],
                    "affected_count": len(ids),
                    "impact_coverage": affected["coverage"],
                    "unmatched_subscriber_numbers": affected["unmatched_subscriber_numbers"],
                },
            ),
        )

        fanout = {"queued": 0, "skipped": 0, "selected": 0}
        if notify and ids:
            fanout = queue_bulk_subscriber_notifications(
                db,
                subscriber_ids=ids,
                channel_value=channel,
                email_subject=email_subject or f"Service update: {title}",
                email_body=email_body
                or (description or "We're aware of an issue affecting your service and are working to restore it."),
                sms_body=sms_body,
                scheduled_local_text=None,
                sent_by_user_id=None,
                sent_by_person_id=coerce_uuid(str(actor_id)) if actor_id else None,
            )
            meta = dict(ticket.metadata_ or {})
            meta["open_notification"] = fanout
            ticket.metadata_ = meta
            db.commit()

        return {"ticket": ticket, "affected": affected, "notification": fanout}

    @staticmethod
    def resolve(
        db,
        ticket_id: str | UUID,
        *,
        actor_id: str | UUID | None = None,
        notify: bool = True,
        channel: str = "email",
        email_subject: str | None = None,
        email_body: str | None = None,
        sms_body: str | None = None,
    ) -> dict[str, Any]:
        """Close the infrastructure ticket and notify every affected customer
        that the fault is resolved (the set recorded at open time)."""
        ticket = tickets_service.get(db, str(ticket_id))
        ids = [
            coerce_uuid(str(x))
            for x in (ticket.metadata_ or {}).get("affected_subscriber_ids", [])
            if coerce_uuid(str(x)) is not None
        ]

        tickets_service.update(
            db,
            str(ticket.id),
            TicketUpdate(status=TicketStatus.closed, closed_at=datetime.now(UTC)),
        )

        fanout = {"queued": 0, "skipped": 0, "selected": 0}
        if notify and ids:
            fanout = queue_bulk_subscriber_notifications(
                db,
                subscriber_ids=ids,
                channel_value=channel,
                email_subject=email_subject or f"Resolved: {ticket.title}",
                email_body=email_body
                or "The issue affecting your service has been resolved. Thank you for your patience.",
                sms_body=sms_body,
                scheduled_local_text=None,
                sent_by_user_id=None,
                sent_by_person_id=coerce_uuid(str(actor_id)) if actor_id else None,
            )
            meta = dict(ticket.metadata_ or {})
            meta["resolve_notification"] = fanout
            ticket.metadata_ = meta
            db.commit()

        return {"ticket": ticket, "notification": fanout}


infrastructure_tickets = InfrastructureTickets()
