"""Tests for the duplicate-person merge driver (scripts/merge_duplicate_persons)."""

import uuid

from scripts.merge_duplicate_persons import (
    build_clusters,
    load_candidates,
    normalize_phone,
    split_suffix,
    write_plan_csv,
)

from app.models.person import Person, PersonStatus
from app.models.subscriber import Subscriber


def _person(db, first, last, phone=None, status=PersonStatus.active) -> Person:
    p = Person(
        first_name=first,
        last_name=last,
        email=f"p-{uuid.uuid4().hex[:12]}@example.com",
        phone=phone,
        status=status,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _clusters(db, include_tier_b=False):
    return build_clusters(db, load_candidates(db), include_tier_b=include_tier_b)


def _find(clusters, base):
    return next((c for c in clusters if c.members[0].base_name == base), None)


# --- normalization units -----------------------------------------------------


def test_normalize_phone_requires_ten_digits():
    assert normalize_phone("+234 801 234 5678") == "8012345678"
    assert normalize_phone("0801-234-5678") == "8012345678"
    assert normalize_phone("234") is None  # bare country code is not a match
    assert normalize_phone(None) is None


def test_split_suffix():
    assert split_suffix("john doe 2") == ("john doe", 2)
    assert split_suffix("john doe") == ("john doe", None)
    assert split_suffix("2") == ("2", None)  # a bare number is not a base name


# --- tiering ------------------------------------------------------------------


def test_tier_a_suffix_plus_shared_phone(db_session):
    _person(db_session, "John", "Doe", "08012345678")
    _person(db_session, "John", "Doe 2", "08012345678")
    _person(db_session, "John", "Doe 3", "0801-234-5678")

    cluster = _find(_clusters(db_session), "john doe")
    assert cluster is not None
    assert cluster.tier == "A"
    # unsuffixed row is the survivor
    assert cluster.survivor is not None
    assert cluster.survivor.suffix is None


def test_tier_b_exact_name_plus_phone_gated_on_flag(db_session):
    _person(db_session, "Jane", "Roe", "08055500011")
    _person(db_session, "Jane", "Roe", "234-805-5500-011")

    # Without the flag: detected as B but not selected (no survivor).
    without = _find(_clusters(db_session, include_tier_b=False), "jane roe")
    assert without is not None
    assert without.tier == "B"
    assert without.survivor is None

    # With the flag: it becomes an apply-set cluster with a survivor.
    with_flag = _find(_clusters(db_session, include_tier_b=True), "jane roe")
    assert with_flag.tier == "B"
    assert with_flag.survivor is not None


def test_tier_c_name_only_never_merged(db_session):
    _person(db_session, "Sam", "Poe")
    _person(db_session, "Sam", "Poe")

    cluster = _find(_clusters(db_session), "sam poe")
    assert cluster.tier == "C"
    assert cluster.survivor is None


def test_phone_conflict_goes_to_review(db_session):
    _person(db_session, "Amy", "Coe", "08011122233")
    _person(db_session, "Amy", "Coe 2", "08099988877")

    cluster = _find(_clusters(db_session), "amy coe")
    assert cluster.tier == "REVIEW"
    assert cluster.survivor is None


def test_generic_names_excluded(db_session):
    _person(db_session, "Facebook", "User", "08012345678")
    _person(db_session, "Facebook", "User", "08012345678")

    assert _find(_clusters(db_session), "facebook user") is None


# --- survivor selection -------------------------------------------------------


def test_linked_subscriber_beats_no_suffix_for_survivor(db_session):
    plain = _person(db_session, "Ken", "Vale", "08033344455")
    suffixed = _person(db_session, "Ken", "Vale 2", "08033344455")
    # The suffixed row carries the real subscriber -> it must survive.
    db_session.add(Subscriber(person_id=suffixed.id))
    db_session.commit()

    cluster = _find(_clusters(db_session), "ken vale")
    assert cluster.tier == "A"
    assert cluster.survivor.person.id == suffixed.id
    assert plain.id != suffixed.id


# --- apply / idempotency / dry-run -------------------------------------------


def test_apply_merges_and_is_idempotent(db_session):
    from scripts.merge_duplicate_persons import apply_merges

    actor = _person(db_session, "System", "Bot")
    survivor = _person(db_session, "Lee", "Nova", "08077766655")
    dup = _person(db_session, "Lee", "Nova 2", "08077766655")

    apply_set = [c for c in _clusters(db_session) if c.survivor is not None]
    result = apply_merges(db_session, apply_set, actor.id)
    assert result["merged"] == 1
    assert result["cluster_errors"] == 0

    db_session.refresh(dup)
    db_session.refresh(survivor)
    assert dup.status == PersonStatus.archived
    assert not dup.is_active
    assert survivor.is_active

    # Second pass: source already archived -> skipped, no errors, no re-merge.
    apply_set2 = [c for c in _clusters(db_session) if c.survivor is not None]
    result2 = apply_merges(db_session, apply_set2, actor.id)
    assert result2["merged"] == 0
    assert result2["skipped"] >= 0
    assert result2["cluster_errors"] == 0


def test_dry_run_writes_plan_but_no_db_changes(db_session, tmp_path):
    from app.models.person import PersonMergeLog

    survivor = _person(db_session, "Mia", "Fenn", "08066655544")
    dup = _person(db_session, "Mia", "Fenn 2", "08066655544")

    before_logs = db_session.query(PersonMergeLog).count()
    apply_set = [c for c in _clusters(db_session) if c.survivor is not None]
    plan_path = tmp_path / "plan.csv"
    rows = write_plan_csv(db_session, str(plan_path), apply_set)

    assert rows == 1
    assert plan_path.exists()
    # No merge happened: source still active, no new merge-log rows.
    db_session.refresh(dup)
    db_session.refresh(survivor)
    assert dup.is_active
    assert dup.status == PersonStatus.active
    assert db_session.query(PersonMergeLog).count() == before_logs
