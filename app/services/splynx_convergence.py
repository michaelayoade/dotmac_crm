"""Transitional: measure splynx -> selfcare keying convergence.

The selfcare bulk sync (``sync_subscribers_from_selfcare_data``) already adopts
and re-keys legacy ``external_system="splynx"`` subscriber rows onto ``selfcare``
(matching by subscriber_number) and backfills ``people.metadata.selfcare_id``.
This module makes that in-flight convergence *measurable* so the legacy
splynx-specific code can be retired only once the counts reach zero.

Read-only. Delete this module (and its script/doc) once convergence completes.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.subscriber import Subscriber


def _people_metadata_stats(db: Session) -> dict[str, int]:
    """Count people still carrying a legacy splynx_id, and how many of those
    lack a selfcare_id (the not-yet-converged identity rows).

    Dialect-aware: a JSON expression on Postgres, a bounded Python scan on
    SQLite (tests). This is a manual/ops report, not a hot path.
    """
    splynx_only = 0
    splynx_total = 0
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "sqlite":
        for person in db.query(Person).all():
            meta = person.metadata_ if isinstance(person.metadata_, dict) else {}
            sid = str(meta.get("splynx_id") or "").strip()
            if not sid:
                continue
            splynx_total += 1
            if not str(meta.get("selfcare_id") or "").strip():
                splynx_only += 1
    else:
        splynx_expr = func.json_extract_path_text(Person.metadata_, "splynx_id")
        selfcare_expr = func.json_extract_path_text(Person.metadata_, "selfcare_id")
        splynx_total = db.query(func.count(Person.id)).filter(splynx_expr.isnot(None), splynx_expr != "").scalar() or 0
        splynx_only = (
            db.query(func.count(Person.id))
            .filter(splynx_expr.isnot(None), splynx_expr != "")
            .filter((selfcare_expr.is_(None)) | (selfcare_expr == ""))
            .scalar()
            or 0
        )
    return {"people_with_splynx_id": int(splynx_total), "people_splynx_id_without_selfcare_id": int(splynx_only)}


def convergence_status(db: Session) -> dict:
    """Snapshot of splynx -> selfcare convergence progress.

    - subscribers_by_external_system: keying breakdown.
    - subscribers_remaining_splynx: rows still to be re-keyed (target: 0).
    - people_with_splynx_id / people_splynx_id_without_selfcare_id: identity
      backfill progress (target for the latter: 0).
    - converged: True when both remaining counts are 0 → safe to retire the
      legacy splynx code paths.
    """
    rows = db.query(Subscriber.external_system, func.count(Subscriber.id)).group_by(Subscriber.external_system).all()
    by_system = {str(system or "(none)"): int(count) for system, count in rows}
    remaining_splynx = by_system.get("splynx", 0)
    people = _people_metadata_stats(db)

    return {
        "subscribers_by_external_system": by_system,
        "subscribers_remaining_splynx": remaining_splynx,
        **people,
        "converged": remaining_splynx == 0 and people["people_splynx_id_without_selfcare_id"] == 0,
    }
