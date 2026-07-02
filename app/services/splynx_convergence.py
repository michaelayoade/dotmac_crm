"""Transitional: measure splynx -> selfcare keying convergence.

The selfcare bulk sync (``sync_subscribers_from_selfcare_data``) already adopts
and re-keys legacy ``external_system="splynx"`` subscriber rows onto ``selfcare``
(matching by subscriber_number) and backfills ``people.metadata.selfcare_id``.
This module makes that in-flight convergence *measurable* so the legacy
splynx-specific code can be retired only once the counts reach zero.

Read-only. Delete this module (and its script/doc) once convergence completes.
"""

from __future__ import annotations

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.subscriber import Subscriber
from app.services.common import coerce_uuid


def _expected_selfcare_number(external_id: object) -> str | None:
    """The canonical selfcare subscriber_number for a legacy splynx id.

    dotmac_sub numbers migrated subscribers as ``100`` + zero-padded
    splynx_customer_id (e.g. 17897 -> 100017897).
    """
    raw = str(external_id or "").strip()
    if not raw.isdigit():
        return None
    return "100" + raw.zfill(6)


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


def find_splynx_duplicates(db: Session) -> tuple[list[dict], list[dict]]:
    """Partition active splynx rows into (duplicates, no_twin).

    A splynx row is a *duplicate* when a canonical selfcare row exists for the
    same subscriber (matched by the ``100`` + padded-id subscriber_number). Those
    are safe to retire — the selfcare row is authoritative. ``no_twin`` rows have
    no selfcare counterpart and must NOT be auto-removed (manual review).
    """
    splynx_rows = (
        db.query(Subscriber).filter(Subscriber.external_system == "splynx", Subscriber.is_active.is_(True)).all()
    )
    duplicates: list[dict] = []
    no_twin: list[dict] = []
    for sp in splynx_rows:
        number = _expected_selfcare_number(sp.external_id)
        twin = None
        if number:
            twin = (
                db.query(Subscriber)
                .filter(
                    Subscriber.external_system == "selfcare",
                    Subscriber.subscriber_number == number,
                    Subscriber.is_active.is_(True),
                )
                .first()
            )
        if twin is not None and twin.id != sp.id:
            duplicates.append({"splynx_id": str(sp.id), "external_id": sp.external_id, "twin_id": str(twin.id)})
        else:
            no_twin.append({"splynx_id": str(sp.id), "external_id": sp.external_id})
    return duplicates, no_twin


def dedupe_splynx_duplicates(db: Session, *, apply: bool = False) -> dict:
    """Soft-delete splynx rows that duplicate a canonical selfcare row.

    Dry-run by default (``apply=False``): only reports. With ``apply=True`` it
    sets ``is_active=False`` on the duplicate splynx rows (reversible; the
    selfcare twin is untouched). Run only AFTER the sub-side push fix has
    deployed, otherwise sub recreates the duplicates on the next push.
    """
    duplicates, no_twin = find_splynx_duplicates(db)
    soft_deleted = 0
    if apply:
        for dup in duplicates:
            sub = db.get(Subscriber, coerce_uuid(dup["splynx_id"]))
            if sub is not None and sub.is_active:
                sub.is_active = False
                soft_deleted += 1
        db.commit()
    return {
        "duplicates": len(duplicates),
        "no_twin": len(no_twin),
        "applied": apply,
        "soft_deleted": soft_deleted,
    }


def backfill_person_selfcare_id(db: Session, *, apply: bool = False) -> dict:
    """Backfill ``people.metadata.selfcare_id`` from a linked selfcare subscriber.

    People that still carry only a legacy ``splynx_id`` converge onto selfcare
    identity by copying the dotmac_sub UUID (the ``external_id`` of their linked
    active selfcare subscriber) into ``metadata.selfcare_id``. Dry-run by default.

    Only touches people that have a resolvable selfcare subscriber; those without
    one are reported as ``unresolvable`` and left for separate handling. The
    existing ``splynx_id`` is preserved.
    """
    rows = (
        db.query(Person, Subscriber.external_id)
        .join(
            Subscriber,
            and_(
                Subscriber.person_id == Person.id,
                Subscriber.external_system == "selfcare",
                Subscriber.is_active.is_(True),
            ),
        )
        .all()
    )
    seen: set[str] = set()
    candidates: list[tuple[Person, str]] = []
    for person, external_id in rows:
        pid = str(person.id)
        if pid in seen:
            continue
        meta = person.metadata_ if isinstance(person.metadata_, dict) else {}
        if not str(meta.get("splynx_id") or "").strip():
            continue
        if str(meta.get("selfcare_id") or "").strip():
            continue
        if not external_id:
            continue
        seen.add(pid)
        candidates.append((person, str(external_id)))

    backfilled = 0
    if apply:
        for person, external_id in candidates:
            meta = dict(person.metadata_ or {})
            meta["selfcare_id"] = external_id
            person.metadata_ = meta
            backfilled += 1
        db.commit()
    return {"candidates": len(candidates), "applied": apply, "backfilled": backfilled}
