import argparse
from typing import Iterable

from sqlalchemy import asc

from app.db import SessionLocal
from app.models.domain_settings import SettingDomain
from app.models.projects import Project, ProjectTask
from app.models.tickets import Ticket
from app.services.numbering import generate_number


ENTITY_CONFIG = {
    "tickets": {
        "model": Ticket,
        "sequence_key": "ticket_number",
        "enabled_key": "ticket_number_enabled",
        "prefix_key": "ticket_number_prefix",
        "padding_key": "ticket_number_padding",
        "start_key": "ticket_number_start",
    },
    "projects": {
        "model": Project,
        "sequence_key": "project_number",
        "enabled_key": "project_number_enabled",
        "prefix_key": "project_number_prefix",
        "padding_key": "project_number_padding",
        "start_key": "project_number_start",
    },
    "project_tasks": {
        "model": ProjectTask,
        "sequence_key": "project_task_number",
        "enabled_key": "project_task_number_enabled",
        "prefix_key": "project_task_number_prefix",
        "padding_key": "project_task_number_padding",
        "start_key": "project_task_number_start",
    },
}


def _iter_missing_numbers(db, model) -> Iterable:
    return (
        db.query(model)
        .filter(model.number.is_(None))
        .order_by(asc(model.created_at), asc(model.id))
        .all()
    )


def backfill_entity(db, key: str, dry_run: bool = False) -> int:
    cfg = ENTITY_CONFIG[key]
    model = cfg["model"]
    rows = _iter_missing_numbers(db, model)
    if not rows:
        return 0

    updated = 0
    for row in rows:
        number = generate_number(
            db=db,
            domain=SettingDomain.numbering,
            sequence_key=cfg["sequence_key"],
            enabled_key=cfg["enabled_key"],
            prefix_key=cfg["prefix_key"],
            padding_key=cfg["padding_key"],
            start_key=cfg["start_key"],
        )
        if not number:
            continue
        if not dry_run:
            row.number = number
        updated += 1

    if not dry_run and updated:
        db.commit()
    elif dry_run:
        db.rollback()
    return updated


def main():
    parser = argparse.ArgumentParser(description="Backfill numbering for tickets, projects, and tasks.")
    parser.add_argument(
        "--only",
        choices=sorted(ENTITY_CONFIG.keys()),
        help="Backfill only one entity type.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Compute counts without writing.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        targets = [args.only] if args.only else list(ENTITY_CONFIG.keys())
        total = 0
        for key in targets:
            updated = backfill_entity(db, key, dry_run=args.dry_run)
            print(f"{key}: {updated} updated")
            total += updated
        print(f"total: {total} updated")
    finally:
        db.close()


if __name__ == "__main__":
    main()
