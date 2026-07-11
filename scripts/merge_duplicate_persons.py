#!/usr/bin/env python3
"""Detect and merge import-generated duplicate people in the CRM.

The importer left two duplicate shapes behind:

  * "<Name> N" numeric-suffix rows (e.g. "John Doe", "John Doe 2", "John Doe 3")
    that all carry one identical real phone  -> TIER A (merged by default).
  * exact same normalized name sharing one identical real phone on >=2 rows,
    with no numeric suffix                    -> TIER B (only with --tier AB).
  * same name but no shared phone/email       -> TIER C (never merged; counted).

Clusters are detected here, deterministically, straight from the CRM DB — no
staging CSV — so the same run reproduces on prod. Each non-survivor is merged
into the cluster survivor via ``People.merge`` (transactional, audited, soft-
deletes the source), which now re-points every customer-subject FK.

Dry-run is the DEFAULT: it writes a per-cluster plan CSV and a review CSV and
touches nothing. ``--apply`` executes the merges.

Usage (staging dry run — start here):
    poetry run python scripts/merge_duplicate_persons.py \
        --plan-csv /tmp/person_merge_plan.csv \
        --review-csv /tmp/person_merge_review.csv

    # include the exact-name+shared-phone (Tier B) set as well:
    poetry run python scripts/merge_duplicate_persons.py --tier AB \
        --plan-csv /tmp/plan.csv --review-csv /tmp/review.csv

    # apply (writes!): supply a system/service person id for the audit log
    poetry run python scripts/merge_duplicate_persons.py --apply \
        --merged-by <system-person-uuid> \
        --plan-csv /tmp/plan.csv --review-csv /tmp/review.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.person import ChannelType, Person, PersonChannel, PersonStatus

# Names that are placeholders, not real identities — never cluster/merge on these.
GENERIC_NAMES = {
    "facebook user",
    "instagram user",
    "whatsapp user",
    "telegram user",
    "unknown",
    "unknown user",
    "guest",
    "guest user",
    "anonymous",
    "anonymous user",
    "test",
    "test user",
    "no name",
    "n/a",
    "na",
    "customer",
    "visitor",
    "web visitor",
    "webchat visitor",
}

# Channel types that carry a phone number.
_PHONE_CHANNELS = {ChannelType.phone, ChannelType.sms, ChannelType.whatsapp}

# Trailing " <int>" suffix that the importer appended to de-collide names.
_SUFFIX_RE = re.compile(r"^(.*?)[\s]+(\d+)$")
_WS_RE = re.compile(r"\s+")


def normalize_name(first: str | None, last: str | None) -> str:
    """lower(collapse-whitespace(first || ' ' || last))."""
    combined = f"{first or ''} {last or ''}".strip().lower()
    return _WS_RE.sub(" ", combined)


def split_suffix(name_key: str) -> tuple[str, int | None]:
    """Return (base_name, suffix_number). suffix_number is None when unsuffixed."""
    match = _SUFFIX_RE.match(name_key)
    if not match:
        return name_key, None
    base = _WS_RE.sub(" ", match.group(1).strip())
    if not base:
        # A name that is only a number ("2") is not a real base name.
        return name_key, None
    return base, int(match.group(2))


def normalize_phone(raw: str | None) -> str | None:
    """Last 10 digits of the digit-string; None if fewer than 10 digits.

    A bare country code (e.g. "234") is not enough to match — require >=10.
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 10:
        return None
    return digits[-10:]


@dataclass
class Candidate:
    person: Person
    name_key: str
    base_name: str
    suffix: int | None
    phones: set[str] = field(default_factory=set)


# (Model, attribute) pairs that reference a person as a customer/subject. Used to
# rank survivors by "customer footprint" and to report re-point counts. Kept in
# sync with People.merge's re-point set (subject columns only, never actor/audit).
def _person_fk_targets() -> list[tuple[type, str]]:
    from app.models.comms import SurveyInvitation, SurveyResponse
    from app.models.crm.campaign import CampaignRecipient
    from app.models.crm.chat_widget import WidgetVisitorSession
    from app.models.crm.conversation import Conversation, ConversationSummary
    from app.models.crm.referral import Referral
    from app.models.crm.sales import Lead, Quote
    from app.models.network import OntAssignment
    from app.models.organization_membership import OrganizationMembership
    from app.models.reseller_commission import ResellerCommission
    from app.models.sales_order import SalesOrder
    from app.models.subscriber import Organization, Subscriber
    from app.models.subscriber_outreach import SubscriberOfflineOutreachLog
    from app.models.tickets import Ticket

    return [
        (Ticket, "customer_person_id"),
        (SalesOrder, "person_id"),
        (Lead, "person_id"),
        (Quote, "person_id"),
        (Conversation, "person_id"),
        (ConversationSummary, "person_id"),
        (Subscriber, "person_id"),
        (Organization, "primary_contact_id"),
        (OrganizationMembership, "person_id"),
        (CampaignRecipient, "person_id"),
        (ResellerCommission, "person_id"),
        (Referral, "referrer_person_id"),
        (Referral, "referred_person_id"),
        (SurveyResponse, "person_id"),
        (SurveyInvitation, "person_id"),
        (WidgetVisitorSession, "person_id"),
        (OntAssignment, "person_id"),
        (SubscriberOfflineOutreachLog, "person_id"),
        (PersonChannel, "person_id"),
    ]


def count_person_refs(db: Session, person_id: uuid.UUID, targets: list[tuple[type, str]]) -> int:
    total = 0
    for model, attr in targets:
        column = getattr(model, attr)
        total += db.query(model).filter(column == person_id).count()
    return total


def _subscriber_linked_ids(db: Session) -> set[uuid.UUID]:
    from app.models.subscriber import Subscriber

    rows = db.query(Subscriber.person_id).filter(Subscriber.person_id.isnot(None)).distinct().all()
    return {r[0] for r in rows}


def load_candidates(db: Session) -> list[Candidate]:
    """Active, non-generic people with their normalized name + phone set."""
    people = db.query(Person).filter(Person.is_active.is_(True), Person.status == PersonStatus.active).all()
    # Batch-load phone channels once (no N+1).
    channels = db.query(PersonChannel).filter(PersonChannel.channel_type.in_(_PHONE_CHANNELS)).all()
    channels_by_person: dict[uuid.UUID, list[PersonChannel]] = defaultdict(list)
    for ch in channels:
        channels_by_person[ch.person_id].append(ch)

    candidates: list[Candidate] = []
    for p in people:
        name_key = normalize_name(p.first_name, p.last_name)
        base, suffix = split_suffix(name_key)
        base_for_generic = base or name_key
        if base_for_generic in GENERIC_NAMES or name_key in GENERIC_NAMES:
            continue
        phones: set[str] = set()
        ph = normalize_phone(p.phone)
        if ph:
            phones.add(ph)
        for ch in channels_by_person.get(p.id, []):
            ph = normalize_phone(ch.address)
            if ph:
                phones.add(ph)
        candidates.append(Candidate(person=p, name_key=name_key, base_name=base, suffix=suffix, phones=phones))
    return candidates


@dataclass
class Cluster:
    tier: str  # "A", "B", "C", or "REVIEW"
    reason: str
    members: list[Candidate]
    survivor: Candidate | None = None


def _select_survivor(
    members: list[Candidate], subscriber_ids: set[uuid.UUID], ref_counts: dict[uuid.UUID, int]
) -> Candidate:
    """Survivor precedence: linked subscriber, no suffix, lowest suffix, most
    FK refs, oldest created_at, then id (stable tiebreak)."""

    def key(c: Candidate) -> tuple:
        return (
            0 if c.person.id in subscriber_ids else 1,  # linked subscriber first
            0 if c.suffix is None else 1,  # unsuffixed first
            c.suffix if c.suffix is not None else -1,  # lowest suffix number
            -ref_counts.get(c.person.id, 0),  # most FK references
            c.person.created_at,  # oldest created_at
            str(c.person.id),  # stable final tiebreak
        )

    return min(members, key=key)


def build_clusters(db: Session, candidates: list[Candidate], include_tier_b: bool) -> list[Cluster]:
    subscriber_ids = _subscriber_linked_ids(db)
    fk_targets = _person_fk_targets()

    by_base: dict[str, list[Candidate]] = defaultdict(list)
    for c in candidates:
        by_base[c.base_name].append(c)

    clusters: list[Cluster] = []
    for members in by_base.values():
        if len(members) < 2:
            continue

        has_suffix = any(m.suffix is not None for m in members)
        distinct_phones: set[str] = set()
        for m in members:
            distinct_phones |= m.phones
        all_have_phone = all(m.phones for m in members)

        if has_suffix:
            # "<Name> N" suffix cluster.
            if all_have_phone and len(distinct_phones) == 1:
                tier, reason = "A", "suffix cluster, single shared phone"
                selected = members
            else:
                # conflicting / partial / absent phones -> manual review
                if len(distinct_phones) > 1:
                    reason = "suffix cluster with conflicting phones"
                elif not all_have_phone:
                    reason = "suffix cluster with missing phone(s)"
                else:
                    reason = "suffix cluster without a real phone"
                clusters.append(Cluster(tier="REVIEW", reason=reason, members=members))
                continue
        else:
            # No numeric suffix: exact-name group.
            if len(distinct_phones) == 1:
                sharers = [m for m in members if m.phones]
                if len(sharers) >= 2:
                    tier, reason = "B", "exact name, single shared phone"
                    selected = sharers
                else:
                    clusters.append(Cluster(tier="C", reason="name-only (no shared phone)", members=members))
                    continue
            else:
                # 0 phones -> name-only; >1 phones -> ambiguous, treat as name-only.
                clusters.append(Cluster(tier="C", reason="name-only (no shared phone)", members=members))
                continue

        if tier == "B" and not include_tier_b:
            # Detected but out of the apply/plan set unless --tier AB.
            clusters.append(Cluster(tier="B", reason=reason + " (not selected)", members=selected))
            continue

        ref_counts = {m.person.id: count_person_refs(db, m.person.id, fk_targets) for m in selected}
        survivor = _select_survivor(selected, subscriber_ids, ref_counts)
        clusters.append(Cluster(tier=tier, reason=reason, members=selected, survivor=survivor))

    clusters.sort(key=lambda cl: (cl.tier, cl.members[0].base_name))
    return clusters


def _display(c: Candidate) -> str:
    return f"{c.person.first_name} {c.person.last_name}".strip()


def write_plan_csv(db: Session, path: str, clusters: list[Cluster]) -> int:
    fk_targets = _person_fk_targets()
    rows = 0
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "cluster_base_name",
                "tier",
                "survivor_id",
                "survivor_name",
                "survivor_email",
                "survivor_phone",
                "source_id",
                "source_name",
                "source_email",
                "source_suffix",
                "source_fk_refs_moving",
            ]
        )
        for cl in clusters:
            if cl.survivor is None:
                continue
            surv = cl.survivor
            surv_phone = next(iter(surv.phones), "")
            for m in cl.members:
                if m.person.id == surv.person.id:
                    continue
                w.writerow(
                    [
                        cl.members[0].base_name,
                        cl.tier,
                        str(surv.person.id),
                        _display(surv),
                        surv.person.email,
                        surv_phone,
                        str(m.person.id),
                        _display(m),
                        m.person.email,
                        m.suffix if m.suffix is not None else "",
                        count_person_refs(db, m.person.id, fk_targets),
                    ]
                )
                rows += 1
    return rows


def write_review_csv(path: str, clusters: list[Cluster]) -> int:
    rows = 0
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["cluster_base_name", "reason", "member_id", "member_name", "member_email", "member_phone", "suffix"]
        )
        for cl in clusters:
            if cl.tier != "REVIEW":
                continue
            for m in cl.members:
                w.writerow(
                    [
                        cl.members[0].base_name,
                        cl.reason,
                        str(m.person.id),
                        _display(m),
                        m.person.email,
                        ";".join(sorted(m.phones)),
                        m.suffix if m.suffix is not None else "",
                    ]
                )
                rows += 1
    return rows


def apply_merges(db: Session, clusters: list[Cluster], merged_by_id: uuid.UUID) -> dict:
    from app.services.person import people

    merged = 0
    skipped = 0
    cluster_errors = 0
    for cl in clusters:
        if cl.survivor is None:
            continue
        survivor_id = cl.survivor.person.id
        try:
            for m in cl.members:
                source = m.person
                if source.id == survivor_id:
                    continue
                # Idempotent: a prior run already archived this source.
                fresh = db.get(Person, source.id)
                if fresh is None or not fresh.is_active or fresh.status == PersonStatus.archived:
                    skipped += 1
                    continue
                people.merge(db, source.id, survivor_id, merged_by_id)
                merged += 1
        except Exception as exc:  # report and continue to the next cluster
            db.rollback()
            cluster_errors += 1
            print(
                f"  ! cluster '{cl.members[0].base_name}' ({cl.tier}) failed: {exc}",
                file=sys.stderr,
            )
    return {"merged": merged, "skipped": skipped, "cluster_errors": cluster_errors}


def _counts_by_tier(clusters: list[Cluster]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for cl in clusters:
        out[cl.tier] += 1
    return dict(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Execute merges (default: dry run, writes nothing)")
    parser.add_argument(
        "--tier", choices=["A", "AB"], default="A", help="Apply set: A (suffix) or AB (also exact-name+phone)"
    )
    parser.add_argument(
        "--include-phone-shared",
        action="store_true",
        help="Alias for --tier AB (include Tier B exact-name+shared-phone clusters)",
    )
    parser.add_argument(
        "--merged-by", help="System/service person UUID recorded in the merge audit log (required with --apply)"
    )
    parser.add_argument("--plan-csv", default="person_merge_plan.csv", help="Per-cluster plan CSV output path")
    parser.add_argument(
        "--review-csv", default="person_merge_review.csv", help="Review CSV for excluded suffix clusters"
    )
    args = parser.parse_args()

    include_tier_b = args.tier == "AB" or args.include_phone_shared

    merged_by_id: uuid.UUID | None = None
    if args.apply:
        if not args.merged_by:
            parser.error("--apply requires --merged-by <system-person-uuid> for the audit log")
        try:
            merged_by_id = uuid.UUID(str(args.merged_by))
        except ValueError:
            parser.error("--merged-by must be a valid UUID")

    db = SessionLocal()
    try:
        if args.apply:
            actor = db.get(Person, merged_by_id)
            if actor is None:
                parser.error(f"--merged-by person {merged_by_id} not found")

        candidates = load_candidates(db)
        clusters = build_clusters(db, candidates, include_tier_b=include_tier_b)

        apply_set = [c for c in clusters if c.survivor is not None]
        plan_rows = write_plan_csv(db, args.plan_csv, apply_set)
        review_rows = write_review_csv(args.review_csv, clusters)

        by_tier = _counts_by_tier(clusters)
        print("Cluster summary (by tier):")
        print(f"  A (suffix + shared phone):        {by_tier.get('A', 0)}")
        print(f"  B (exact name + shared phone):    {by_tier.get('B', 0)}")
        print(f"  C (name-only, never merge):       {by_tier.get('C', 0)}")
        print(f"  REVIEW (suffix, phone conflict):  {by_tier.get('REVIEW', 0)}")
        print(f"\nPlan written to {args.plan_csv} ({plan_rows} source->survivor merges in apply set)")
        print(f"Review written to {args.review_csv} ({review_rows} rows for manual review)")

        if not args.apply:
            db.rollback()  # belt-and-braces: guarantee a read-only dry run
            print("\nDRY RUN — nothing changed. Re-run with --apply --merged-by <uuid> to execute.")
            return

        result = apply_merges(db, apply_set, merged_by_id)  # type: ignore[arg-type]
        print(
            f"\nAPPLIED — merged {result['merged']} source(s), skipped {result['skipped']} "
            f"already-archived, {result['cluster_errors']} cluster(s) errored (see stderr)."
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
