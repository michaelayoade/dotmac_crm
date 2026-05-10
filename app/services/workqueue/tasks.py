"""Workqueue beat tasks.

Two periodic Celery tasks back the Workqueue:

* :func:`sla_tick` — scans open tickets and emits a ``workqueue.changed``
  event for any whose SLA window is about to transition into the *soon* /
  *imminent* / *breach* bands.  This drives WebSocket pushes so clients can
  re-render without a full reload.

* :func:`prune_snoozes` — removes WorkqueueSnooze rows whose ``snooze_until``
  has been in the past for at least 7 days.  Snoozes are user-scoped and
  cheap, but unbounded growth is undesirable.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from app.celery_app import celery_app
from app.db import SessionLocal
from app.metrics import observe_job
from app.models.tickets import Ticket, TicketStatus
from app.models.workqueue import WorkqueueSnooze
from app.services.workqueue.events import emit_change
from app.services.workqueue.scoring_config import TICKET_SLA_SOON_SEC
from app.services.workqueue.types import ItemKind

logger = logging.getLogger(__name__)


_OPEN_STATUSES = (
    TicketStatus.new,
    TicketStatus.open,
    TicketStatus.pending,
    TicketStatus.waiting_on_customer,
)


def _parse_sla_due_at(value: object) -> datetime | None:
    """Return a UTC-aware ``datetime`` parsed from the JSON metadata value, or ``None``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


@celery_app.task(name="app.services.workqueue.tasks.sla_tick")
def sla_tick() -> dict:
    """Emit workqueue.changed for tickets nearing or past their SLA boundary."""
    start = time.monotonic()
    status = "success"
    db = SessionLocal()
    scanned = 0
    emitted = 0
    logger.info("WORKQUEUE_SLA_TICK_START")
    try:
        now = datetime.now(UTC)
        # Pull all open tickets; we filter the SLA in Python because it lives
        # in the JSON ``metadata_`` column and varies in shape.
        rows = (
            db.query(Ticket)
            .filter(Ticket.status.in_(_OPEN_STATUSES))
            .filter(Ticket.is_active.is_(True))
            .all()
        )
        boundary = now + timedelta(seconds=TICKET_SLA_SOON_SEC + 60)
        for t in rows:
            sla_dt = _parse_sla_due_at((t.metadata_ or {}).get("sla_due_at"))
            if sla_dt is None or sla_dt > boundary:
                continue
            scanned += 1
            assignee_ids = [a.person_id for a in (getattr(t, "assignees", None) or [])]
            try:
                emit_change(
                    kind=ItemKind.ticket,
                    item_id=t.id,
                    change="updated",
                    affected_user_ids=assignee_ids,
                    affected_org=True,
                )
                emitted += 1
            except Exception as exc:  # never let a single bad row kill the batch
                logger.warning("WORKQUEUE_SLA_TICK_EMIT_FAILED ticket_id=%s error=%s", t.id, exc)
    except Exception:
        status = "error"
        raise
    finally:
        db.close()
        observe_job("workqueue.sla_tick", status, time.monotonic() - start)
    logger.info("WORKQUEUE_SLA_TICK_COMPLETE scanned=%s emitted=%s", scanned, emitted)
    return {"scanned": scanned, "emitted": emitted}


@celery_app.task(name="app.services.workqueue.tasks.prune_snoozes")
def prune_snoozes() -> dict:
    """Delete snoozes whose ``snooze_until`` has been in the past for >= 7 days."""
    start = time.monotonic()
    status = "success"
    db = SessionLocal()
    deleted = 0
    logger.info("WORKQUEUE_PRUNE_SNOOZES_START")
    try:
        cutoff = datetime.now(UTC) - timedelta(days=7)
        deleted = (
            db.query(WorkqueueSnooze)
            .filter(WorkqueueSnooze.snooze_until.isnot(None))
            .filter(WorkqueueSnooze.snooze_until < cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()
    except Exception:
        status = "error"
        db.rollback()
        raise
    finally:
        db.close()
        observe_job("workqueue.prune_snoozes", status, time.monotonic() - start)
    logger.info("WORKQUEUE_PRUNE_SNOOZES_COMPLETE deleted=%s", deleted)
    return {"deleted": deleted}
