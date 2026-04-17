"""One-time backfill for CRM conversation summaries.

Usage:
    poetry run python scripts/backfill_conversation_summaries.py [--dry-run] [--limit N]
"""

from __future__ import annotations

import sys
from pathlib import Path
import argparse

# Bootstrap the app environment
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.db import SessionLocal
from app.models.crm.conversation import Conversation
from app.services.crm.inbox import cache as inbox_cache
from app.services.crm.inbox.summaries import recompute_conversation_summary


def _process_batch(db, conversation_ids: list[str], stats: dict[str, int], errors: list[str]) -> None:
    for conversation_id in conversation_ids:
        try:
            if recompute_conversation_summary(db, conversation_id) is not None:
                stats["updated"] += 1
        except Exception as exc:  # pragma: no cover - one-time migration safety net
            stats["errors"] += 1
            if len(errors) < 10:
                errors.append(f"{conversation_id}: {exc}")


def backfill(*, dry_run: bool = False, active_only: bool = True, limit: int | None = None, batch_size: int = 200) -> dict:
    """Recompute conversation summaries for a bounded set of conversations."""
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than 0")
    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0")

    db = SessionLocal()
    stats: dict[str, int] = {
        "processed": 0,
        "updated": 0,
        "errors": 0,
        "total_candidates": 0,
    }
    errors: list[str] = []

    query = db.query(Conversation).order_by(Conversation.id)
    if active_only:
        query = query.filter(Conversation.is_active.is_(True))
    if limit is not None and limit > 0:
        query = query.limit(limit)

    try:
        total_candidates = query.count()
        batch_ids: list[str] = []

        for conversation in query.yield_per(batch_size):
            if stats["processed"] >= total_candidates:
                break
            batch_ids.append(str(conversation.id))

            if len(batch_ids) < batch_size:
                continue

            _process_batch(db, batch_ids, stats, errors)
            stats["processed"] += len(batch_ids)
            batch_ids.clear()

        if batch_ids:
            _process_batch(db, batch_ids, stats, errors)
            stats["processed"] += len(batch_ids)

        stats["total_candidates"] = total_candidates

        if dry_run:
            db.rollback()
            stats["status"] = "dry_run"
            if errors:
                print("sample_errors:")
                for error in errors:
                    print(f"  - {error}")
            return dict(stats)

        db.commit()
        inbox_cache.invalidate_inbox_list()
        stats["status"] = "applied"
        if errors:
            print("sample_errors:")
            for error in errors:
                print(f"  - {error}")
        return dict(stats)
    finally:
        db.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recompute CRM conversation summaries (one-time backfill).")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run without writing changes")
    parser.add_argument("--all", action="store_true", help="Include inactive conversations")
    parser.add_argument("--limit", type=int, default=None, help="Optional hard cap on conversations")
    parser.add_argument("--batch-size", type=int, default=200, help="Rows per batch")
    return parser.parse_args()


def main() -> dict:
    args = _parse_args()
    result = backfill(
        dry_run=args.dry_run,
        active_only=not args.all,
        limit=args.limit,
        batch_size=args.batch_size,
    )
    print(
        "Conversation summary backfill complete: "
        f"status={result.get('status')} total={result.get('total_candidates', 0)} "
        f"processed={result.get('processed', 0)} updated={result.get('updated', 0)} errors={result.get('errors', 0)}"
    )
    return result


if __name__ == "__main__":
    main()
