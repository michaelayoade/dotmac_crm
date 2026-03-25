from datetime import UTC, datetime, timedelta
from uuid import uuid4

from starlette.requests import Request

from app.models.crm.enums import LeadStatus
from app.models.crm.sales import Lead
from app.models.person import PartyStatus, Person
from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.tickets import Ticket, TicketStatus
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.services import subscriber_reports as subscriber_reports_service
from app.web.admin import reports as reports_web


def test_overview_kpis_counts_historical_activation_and_termination_events(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    active_person = Person(first_name="Active", last_name="Subscriber", email=f"active-{uuid4().hex}@example.com")
    terminated_person = Person(
        first_name="Terminated",
        last_name="Subscriber",
        email=f"terminated-{uuid4().hex}@example.com",
    )
    db_session.add_all([active_person, terminated_person])
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=active_person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                activated_at=now - timedelta(days=10),
                service_region="Central",
            ),
            Subscriber(
                person_id=terminated_person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.terminated,
                is_active=False,
                activated_at=now - timedelta(days=20),
                terminated_at=now - timedelta(days=5),
                service_region="Central",
            ),
        ]
    )
    db_session.commit()

    kpis = subscriber_reports_service.overview_kpis(db_session, start_dt, end_dt)

    assert kpis["activations"] == 2
    assert kpis["terminations"] == 1
    assert kpis["net_growth"] == 1


def test_overview_kpis_regions_covered_uses_regional_breakdown_grouping(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    people = [
        Person(first_name="One", last_name="Region", email=f"one-{uuid4().hex}@example.com"),
        Person(first_name="Two", last_name="Region", email=f"two-{uuid4().hex}@example.com"),
        Person(first_name="Three", last_name="Region", email=f"three-{uuid4().hex}@example.com"),
        Person(first_name="Four", last_name="Region", email=f"four-{uuid4().hex}@example.com"),
    ]
    db_session.add_all(people)
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=people[0].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_city="Wuse 2",
                created_at=now - timedelta(days=10),
            ),
            Subscriber(
                person_id=people[1].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_region="Abuja",
                created_at=now - timedelta(days=9),
            ),
            Subscriber(
                person_id=people[2].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_city="Lekki",
                created_at=now - timedelta(days=8),
            ),
            Subscriber(
                person_id=people[3].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_region="100000065",
                created_at=now - timedelta(days=7),
            ),
        ]
    )
    db_session.commit()

    kpis = subscriber_reports_service.overview_kpis(db_session, start_dt, end_dt)

    assert kpis["regions_covered"] == 3


def test_overview_filter_options_use_grouped_regional_breakdown_labels(db_session):
    people = [
        Person(first_name="One", last_name="Filter", email=f"filter-one-{uuid4().hex}@example.com"),
        Person(first_name="Two", last_name="Filter", email=f"filter-two-{uuid4().hex}@example.com"),
        Person(first_name="Three", last_name="Filter", email=f"filter-three-{uuid4().hex}@example.com"),
    ]
    db_session.add_all(people)
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=people[0].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_city="Wuse 2",
            ),
            Subscriber(
                person_id=people[1].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_city="Lekki",
            ),
            Subscriber(
                person_id=people[2].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_region="100000065",
            ),
        ]
    )
    db_session.commit()

    filter_opts = subscriber_reports_service.overview_filter_options(db_session)

    assert filter_opts["regions"] == ["Abuja", "Lagos", "Unknown"]


def test_overview_filter_options_excludes_null_duplicate_and_inactive_plan_values(db_session):
    people = [
        Person(first_name="One", last_name="Plans", email=f"plans-one-{uuid4().hex}@example.com"),
        Person(first_name="Two", last_name="Plans", email=f"plans-two-{uuid4().hex}@example.com"),
        Person(first_name="Three", last_name="Plans", email=f"plans-three-{uuid4().hex}@example.com"),
        Person(first_name="Four", last_name="Plans", email=f"plans-four-{uuid4().hex}@example.com"),
    ]
    db_session.add_all(people)
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=people[0].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_city="Wuse 2",
                service_plan="Home 100",
            ),
            Subscriber(
                person_id=people[1].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_region="Abuja",
                service_plan="Home 100",
            ),
            Subscriber(
                person_id=people[2].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_region=None,
                service_plan=None,
            ),
            Subscriber(
                person_id=people[3].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=False,
                service_region="Lekki",
                service_plan="Business 200",
            ),
        ]
    )
    db_session.commit()

    filter_opts = subscriber_reports_service.overview_filter_options(db_session)

    assert filter_opts["regions"] == ["Abuja", "Unknown"]
    assert filter_opts["plans"] == ["Home 100"]


def test_overview_filtered_subscriber_ids_use_grouped_region_and_status(db_session):
    people = [
        Person(first_name="One", last_name="Scope", email=f"scope-one-{uuid4().hex}@example.com"),
        Person(first_name="Two", last_name="Scope", email=f"scope-two-{uuid4().hex}@example.com"),
        Person(first_name="Three", last_name="Scope", email=f"scope-three-{uuid4().hex}@example.com"),
    ]
    db_session.add_all(people)
    db_session.flush()

    subscribers = [
        Subscriber(
            person_id=people[0].id,
            subscriber_number=f"SUB-{uuid4().hex[:8]}",
            status=SubscriberStatus.active,
            is_active=True,
            service_city="Wuse 2",
        ),
        Subscriber(
            person_id=people[1].id,
            subscriber_number=f"SUB-{uuid4().hex[:8]}",
            status=SubscriberStatus.suspended,
            is_active=True,
            service_region="Abuja",
        ),
        Subscriber(
            person_id=people[2].id,
            subscriber_number=f"SUB-{uuid4().hex[:8]}",
            status=SubscriberStatus.active,
            is_active=True,
            service_city="Lekki",
        ),
    ]
    db_session.add_all(subscribers)
    db_session.commit()

    filtered_ids = subscriber_reports_service.overview_filtered_subscriber_ids(
        db_session,
        status=SubscriberStatus.active,
        region="Abuja",
    )

    assert filtered_ids == [subscribers[0].id]


def test_overview_growth_trend_returns_zero_filled_daily_series(db_session):
    start_dt = datetime(2026, 3, 1, tzinfo=UTC)
    end_dt = datetime(2026, 3, 3, 23, 59, 59, tzinfo=UTC)

    trend = subscriber_reports_service.overview_growth_trend(db_session, start_dt, end_dt)

    assert trend == [
        {"date": "2026-03-01", "activations": 0, "terminations": 0},
        {"date": "2026-03-02", "activations": 0, "terminations": 0},
        {"date": "2026-03-03", "activations": 0, "terminations": 0},
    ]


def test_overview_growth_trend_aggregates_activations_and_terminations_per_day(db_session):
    start_dt = datetime(2026, 3, 1, tzinfo=UTC)
    end_dt = datetime(2026, 3, 3, 23, 59, 59, tzinfo=UTC)

    active_person = Person(first_name="Daily", last_name="Growth", email=f"growth-{uuid4().hex}@example.com")
    terminated_person = Person(first_name="Daily", last_name="Churn", email=f"churn-{uuid4().hex}@example.com")
    db_session.add_all([active_person, terminated_person])
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=active_person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                activated_at=datetime(2026, 3, 1, 8, tzinfo=UTC),
            ),
            Subscriber(
                person_id=terminated_person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.terminated,
                is_active=False,
                activated_at=datetime(2026, 3, 1, 10, tzinfo=UTC),
                terminated_at=datetime(2026, 3, 2, 16, tzinfo=UTC),
            ),
        ]
    )
    db_session.commit()

    trend = subscriber_reports_service.overview_growth_trend(db_session, start_dt, end_dt)

    assert trend == [
        {"date": "2026-03-01", "activations": 2, "terminations": 0},
        {"date": "2026-03-02", "activations": 0, "terminations": 1},
        {"date": "2026-03-03", "activations": 0, "terminations": 0},
    ]


def test_overview_status_distribution_returns_consistent_non_null_keys(db_session):
    people = [
        Person(first_name="Active", last_name="Status", email=f"active-status-{uuid4().hex}@example.com"),
        Person(first_name="Suspended", last_name="Status", email=f"suspended-status-{uuid4().hex}@example.com"),
        Person(first_name="Inactive", last_name="Status", email=f"inactive-status-{uuid4().hex}@example.com"),
    ]
    db_session.add_all(people)
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=people[0].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
            ),
            Subscriber(
                person_id=people[1].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.suspended,
                is_active=True,
            ),
            Subscriber(
                person_id=people[2].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=False,
            ),
        ]
    )
    db_session.commit()

    distribution = subscriber_reports_service.overview_status_distribution(db_session)

    assert distribution == {"active": 1, "suspended": 1}
    assert all(key is not None and key != "" for key in distribution)


def test_overview_plan_distribution_groups_duplicate_plans_and_excludes_nulls(db_session):
    people = [
        Person(first_name="One", last_name="PlanDist", email=f"plan-dist-one-{uuid4().hex}@example.com"),
        Person(first_name="Two", last_name="PlanDist", email=f"plan-dist-two-{uuid4().hex}@example.com"),
        Person(first_name="Three", last_name="PlanDist", email=f"plan-dist-three-{uuid4().hex}@example.com"),
        Person(first_name="Four", last_name="PlanDist", email=f"plan-dist-four-{uuid4().hex}@example.com"),
    ]
    db_session.add_all(people)
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=people[0].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_plan="Home 100",
            ),
            Subscriber(
                person_id=people[1].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_plan="Home 100",
            ),
            Subscriber(
                person_id=people[2].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_plan="Business 300",
            ),
            Subscriber(
                person_id=people[3].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_plan=None,
            ),
        ]
    )
    db_session.commit()

    distribution = subscriber_reports_service.overview_plan_distribution(db_session)

    assert distribution == [
        {"plan": "Home 100", "count": 2},
        {"plan": "Business 300", "count": 1},
    ]


def test_overview_regional_breakdown_uses_city_when_region_missing(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    person_a = Person(first_name="Abuja", last_name="Active", email=f"abuja-{uuid4().hex}@example.com")
    person_b = Person(first_name="Abuja", last_name="Suspended", email=f"abuja-s-{uuid4().hex}@example.com")
    db_session.add_all([person_a, person_b])
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=person_a.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_city="Abuja",
                created_at=now - timedelta(days=5),
            ),
            Subscriber(
                person_id=person_b.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.suspended,
                is_active=True,
                service_city="Abuja",
                created_at=now - timedelta(days=3),
            ),
        ]
    )
    db_session.commit()

    regional = subscriber_reports_service.overview_regional_breakdown(db_session, start_dt, end_dt)

    assert any(
        row["region"] == "Abuja"
        and row["active"] == 1
        and row["suspended"] == 1
        and row["terminated"] == 0
        and row["new_in_period"] == 2
        for row in regional
    )


def test_overview_regional_breakdown_normalizes_region_variants(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    people = [
        Person(first_name="One", last_name="A", email=f"one-{uuid4().hex}@example.com"),
        Person(first_name="Two", last_name="A", email=f"two-{uuid4().hex}@example.com"),
        Person(first_name="Three", last_name="A", email=f"three-{uuid4().hex}@example.com"),
    ]
    db_session.add_all(people)
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=people[0].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_city="Abuja",
                created_at=now - timedelta(days=5),
            ),
            Subscriber(
                person_id=people[1].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_region="FCT, Abuja",
                created_at=now - timedelta(days=4),
            ),
            Subscriber(
                person_id=people[2].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.suspended,
                is_active=True,
                service_region="F.C.T Abuja",
                created_at=now - timedelta(days=3),
            ),
        ]
    )
    db_session.commit()

    regional = subscriber_reports_service.overview_regional_breakdown(db_session, start_dt, end_dt)

    assert any(
        row["region"] == "Abuja" and row["active"] == 2 and row["suspended"] == 1 and row["new_in_period"] == 3
        for row in regional
    )


def test_overview_regional_breakdown_groups_abuja_address_strings(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    person = Person(first_name="Wuse", last_name="Address", email=f"wuse-{uuid4().hex}@example.com")
    db_session.add(person)
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_region="No. 10 Adetokunbo Ademola Crescent, Wuse 2",
                created_at=now - timedelta(days=5),
            ),
            Subscriber(
                person_id=person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_region="Plot 7, Garki Area 11",
                created_at=now - timedelta(days=4),
            ),
        ]
    )
    db_session.commit()

    regional = subscriber_reports_service.overview_regional_breakdown(db_session, start_dt, end_dt)

    assert any(row["region"] == "Abuja" and row["active"] == 2 and row["new_in_period"] == 2 for row in regional)


def test_overview_regional_breakdown_groups_other_nigerian_city_localities(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    people = [
        Person(first_name="Lekki", last_name="User", email=f"lekki-{uuid4().hex}@example.com"),
        Person(first_name="PH", last_name="User", email=f"ph-{uuid4().hex}@example.com"),
        Person(first_name="Bodija", last_name="User", email=f"bodija-{uuid4().hex}@example.com"),
    ]
    db_session.add_all(people)
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=people[0].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_region="Admiralty Way, Lekki Phase 1",
                created_at=now - timedelta(days=5),
            ),
            Subscriber(
                person_id=people[1].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_region="Trans Amadi Industrial Layout",
                created_at=now - timedelta(days=4),
            ),
            Subscriber(
                person_id=people[2].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_region="Bodija Market Area",
                created_at=now - timedelta(days=3),
            ),
        ]
    )
    db_session.commit()

    regional = subscriber_reports_service.overview_regional_breakdown(db_session, start_dt, end_dt)

    assert any(row["region"] == "Lagos" and row["active"] == 1 for row in regional)
    assert any(row["region"] == "Rivers" and row["active"] == 1 for row in regional)
    assert any(row["region"] == "Oyo" and row["active"] == 1 for row in regional)


def test_overview_regional_breakdown_extracts_states_and_drops_invalid_values(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    people = [
        Person(first_name="Lokoja", last_name="User", email=f"lokoja-{uuid4().hex}@example.com"),
        Person(first_name="Yola", last_name="User", email=f"yola-{uuid4().hex}@example.com"),
        Person(first_name="Awka", last_name="User", email=f"awka-{uuid4().hex}@example.com"),
        Person(first_name="Bad", last_name="User", email=f"bad-{uuid4().hex}@example.com"),
    ]
    db_session.add_all(people)
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=people[0].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_region="Lokoja, Kogi",
                created_at=now - timedelta(days=5),
            ),
            Subscriber(
                person_id=people[1].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_region="Adamawa, Yola",
                created_at=now - timedelta(days=4),
            ),
            Subscriber(
                person_id=people[2].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_region="Awka",
                created_at=now - timedelta(days=3),
            ),
            Subscriber(
                person_id=people[3].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                service_region="100000065",
                created_at=now - timedelta(days=2),
            ),
        ]
    )
    db_session.commit()

    regional = subscriber_reports_service.overview_regional_breakdown(db_session, start_dt, end_dt)

    assert any(row["region"] == "Kogi" and row["active"] == 1 for row in regional)
    assert any(row["region"] == "Adamawa" and row["active"] == 1 for row in regional)
    assert any(row["region"] == "Anambra" and row["active"] == 1 for row in regional)
    assert any(row["region"] == "Unknown" and row["active"] == 1 for row in regional)


def test_lifecycle_kpis_uses_inactive_subscribers_as_churn_fallback(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    person = Person(first_name="Churn", last_name="Fallback", email=f"churn-{uuid4().hex}@example.com")
    db_session.add(person)
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                created_at=now - timedelta(days=90),
            ),
            Subscriber(
                person_id=person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=False,
                created_at=now - timedelta(days=60),
                updated_at=now - timedelta(days=5),
            ),
        ]
    )
    db_session.commit()

    kpis = subscriber_reports_service.lifecycle_kpis(db_session, start_dt, end_dt)

    assert kpis["terminated_in_period"] == 1
    assert kpis["churn_rate"] == 50.0


def test_lifecycle_kpis_keeps_small_churn_rates_visible(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    people = [
        Person(first_name=f"Person{i}", last_name="SmallChurn", email=f"small-{i}-{uuid4().hex}@example.com")
        for i in range(101)
    ]
    db_session.add_all(people)
    db_session.flush()

    subscribers = [
        Subscriber(
            person_id=person.id,
            subscriber_number=f"SUB-{uuid4().hex[:8]}",
            status=SubscriberStatus.active,
            is_active=True,
            created_at=now - timedelta(days=60),
        )
        for person in people[:100]
    ]
    subscribers.append(
        Subscriber(
            person_id=people[100].id,
            subscriber_number=f"SUB-{uuid4().hex[:8]}",
            status=SubscriberStatus.active,
            is_active=False,
            created_at=now - timedelta(days=60),
            updated_at=now - timedelta(days=5),
        )
    )
    db_session.add_all(subscribers)
    db_session.commit()

    kpis = subscriber_reports_service.lifecycle_kpis(db_session, start_dt, end_dt)

    assert kpis["terminated_in_period"] == 1
    assert round(kpis["churn_rate"], 5) == 0.9901


def test_lifecycle_kpis_excludes_pre_period_churn_from_starting_base(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    people = [
        Person(first_name="Base", last_name="One", email=f"base1-{uuid4().hex}@example.com"),
        Person(first_name="Base", last_name="Two", email=f"base2-{uuid4().hex}@example.com"),
        Person(first_name="Old", last_name="Churn", email=f"oldchurn-{uuid4().hex}@example.com"),
    ]
    db_session.add_all(people)
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=people[0].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                created_at=now - timedelta(days=90),
            ),
            Subscriber(
                person_id=people[1].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=False,
                created_at=now - timedelta(days=60),
                updated_at=now - timedelta(days=5),
            ),
            Subscriber(
                person_id=people[2].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=False,
                created_at=now - timedelta(days=90),
                updated_at=now - timedelta(days=45),
            ),
        ]
    )
    db_session.commit()

    kpis = subscriber_reports_service.lifecycle_kpis(db_session, start_dt, end_dt)

    assert kpis["terminated_in_period"] == 1
    assert kpis["churn_rate"] == 50.0


def test_lifecycle_kpis_avg_days_to_convert_uses_won_lead_cycle_time(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    winner = Person(first_name="Won", last_name="Lead", email=f"won-{uuid4().hex}@example.com")
    other = Person(first_name="Other", last_name="Lead", email=f"other-{uuid4().hex}@example.com")
    db_session.add_all([winner, other])
    db_session.flush()

    db_session.add_all(
        [
            Lead(
                person_id=winner.id,
                status=LeadStatus.won,
                is_active=True,
                created_at=now - timedelta(days=12),
                closed_at=now - timedelta(days=2),
            ),
            Lead(
                person_id=other.id,
                status=LeadStatus.won,
                is_active=True,
                created_at=now - timedelta(days=8),
                closed_at=now - timedelta(days=4),
            ),
            Lead(
                person_id=other.id,
                status=LeadStatus.won,
                is_active=True,
                created_at=now - timedelta(days=50),
                closed_at=now - timedelta(days=40),
            ),
            Lead(
                person_id=other.id,
                status=LeadStatus.new,
                is_active=True,
                created_at=now - timedelta(days=10),
                closed_at=None,
            ),
        ]
    )
    db_session.commit()

    kpis = subscriber_reports_service.lifecycle_kpis(db_session, start_dt, end_dt)

    assert kpis["avg_days_to_convert"] == 7.0


def test_lifecycle_funnel_sorts_stages_by_descending_count(db_session):
    statuses = (
        (PartyStatus.customer, 4),
        (PartyStatus.lead, 3),
        (PartyStatus.subscriber, 2),
        (PartyStatus.contact, 1),
    )

    for status, count in statuses:
        for index in range(count):
            db_session.add(
                Person(
                    first_name=f"{status.value}-{index}",
                    last_name="Lifecycle",
                    email=f"{status.value}-{index}-{uuid4().hex}@example.com",
                    party_status=status,
                    is_active=True,
                )
            )

    db_session.commit()

    funnel = subscriber_reports_service.lifecycle_funnel(db_session)

    assert funnel == [
        {"stage": "customer", "count": 4},
        {"stage": "lead", "count": 3},
        {"stage": "subscriber", "count": 2},
        {"stage": "contact", "count": 1},
    ]


def test_lifecycle_churn_trend_includes_inactive_terminated_subscribers(db_session):
    now = datetime.now(UTC)

    active_person = Person(first_name="Active", last_name="Churn", email=f"active-churn-{uuid4().hex}@example.com")
    inactive_person = Person(
        first_name="Inactive",
        last_name="Churn",
        email=f"inactive-churn-{uuid4().hex}@example.com",
    )
    db_session.add_all([active_person, inactive_person])
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=active_person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.terminated,
                is_active=True,
                terminated_at=now - timedelta(days=20),
            ),
            Subscriber(
                person_id=inactive_person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.terminated,
                is_active=False,
                terminated_at=now - timedelta(days=10),
            ),
        ]
    )
    db_session.commit()

    trend = subscriber_reports_service.lifecycle_churn_trend(db_session)
    current_month = (now - timedelta(days=10)).strftime("%Y-%m")

    assert {"month": current_month, "count": 2} in trend


def test_lifecycle_longest_tenure_includes_requested_columns(db_session):
    now = datetime.now(UTC)

    person = Person(first_name="Tenure", last_name="Leader", email=f"tenure-{uuid4().hex}@example.com")
    db_session.add(person)
    db_session.flush()

    db_session.add(
        Subscriber(
            person_id=person.id,
            subscriber_number="SUB-TEST-001",
            status=SubscriberStatus.active,
            is_active=True,
            service_plan="Business Fiber",
            service_region="Abuja",
            activated_at=now - timedelta(days=120),
        )
    )
    db_session.commit()

    rows = subscriber_reports_service.lifecycle_longest_tenure(db_session, limit=10)

    assert rows[0]["name"] == "Tenure Leader"
    assert rows[0]["subscriber_number"] == "SUB-TEST-001"
    assert rows[0]["plan"] == "Business Fiber"
    assert rows[0]["region"] == "Abuja"
    assert rows[0]["activated_at"] == (now - timedelta(days=120)).strftime("%Y-%m-%d")
    assert rows[0]["tenure_days"] >= 119


def test_lifecycle_longest_tenure_cleans_display_name_format(db_session):
    now = datetime.now(UTC)

    person = Person(
        first_name="michael",
        last_name="ayoade",
        display_name="mimi's Home",
        email=f"tenure-clean-{uuid4().hex}@example.com",
    )
    db_session.add(person)
    db_session.flush()

    db_session.add(
        Subscriber(
            person_id=person.id,
            subscriber_number="SUB-CLEAN-001",
            status=SubscriberStatus.active,
            is_active=True,
            activated_at=now - timedelta(days=30),
        )
    )
    db_session.commit()

    rows = subscriber_reports_service.lifecycle_longest_tenure(db_session, limit=10)

    assert rows[0]["name"] == "Mimi's Home"


def test_lifecycle_longest_tenure_falls_back_to_created_at(db_session):
    now = datetime.now(UTC)

    person = Person(first_name="Created", last_name="Fallback", email=f"created-{uuid4().hex}@example.com")
    db_session.add(person)
    db_session.flush()

    db_session.add(
        Subscriber(
            person_id=person.id,
            subscriber_number="SUB-CREATED-001",
            status=SubscriberStatus.active,
            is_active=True,
            service_plan="Starter",
            service_region="Central",
            created_at=now - timedelta(days=45),
            activated_at=None,
        )
    )
    db_session.commit()

    rows = subscriber_reports_service.lifecycle_longest_tenure(db_session, limit=10)

    assert rows[0]["subscriber_number"] == "SUB-CREATED-001"
    assert rows[0]["activated_at"] == (now - timedelta(days=45)).strftime("%Y-%m-%d")
    assert rows[0]["tenure_days"] >= 44


def test_lifecycle_recent_churns_fall_back_to_inactive_updated_at(db_session):
    now = datetime.now(UTC)

    person = Person(first_name="Recent", last_name="Churn", email=f"recent-churn-{uuid4().hex}@example.com")
    db_session.add(person)
    db_session.flush()

    db_session.add(
        Subscriber(
            person_id=person.id,
            subscriber_number="SUB-RECENT-001",
            status=SubscriberStatus.terminated,
            is_active=False,
            service_region="Central",
            created_at=now - timedelta(days=60),
            updated_at=now - timedelta(days=5),
            terminated_at=None,
        )
    )
    db_session.commit()

    rows = subscriber_reports_service.lifecycle_recent_churns(db_session, limit=10)

    assert rows[0]["subscriber_number"] == "SUB-RECENT-001"
    assert rows[0]["region"] == "Central"
    assert rows[0]["terminated_at"] == (now - timedelta(days=5)).strftime("%Y-%m-%d")
    assert rows[0]["activated_at"] == (now - timedelta(days=60)).strftime("%Y-%m-%d")
    assert rows[0]["tenure_days"] >= 54


def test_lifecycle_recent_churns_only_uses_last_30_days_and_limits_to_five(db_session):
    now = datetime.now(UTC)

    for index in range(7):
        person = Person(
            first_name=f"Recent{index}",
            last_name="Churn",
            email=f"recent-limit-{index}-{uuid4().hex}@example.com",
        )
        db_session.add(person)
        db_session.flush()
        db_session.add(
            Subscriber(
                person_id=person.id,
                subscriber_number=f"SUB-RECENT-{index}",
                status=SubscriberStatus.terminated,
                is_active=False,
                created_at=now - timedelta(days=90),
                updated_at=now - timedelta(days=index + 1),
                terminated_at=None,
            )
        )

    old_person = Person(first_name="Old", last_name="Churn", email=f"old-churn-{uuid4().hex}@example.com")
    db_session.add(old_person)
    db_session.flush()
    db_session.add(
        Subscriber(
            person_id=old_person.id,
            subscriber_number="SUB-OLD-CHURN",
            status=SubscriberStatus.terminated,
            is_active=False,
            created_at=now - timedelta(days=120),
            updated_at=now - timedelta(days=45),
            terminated_at=None,
        )
    )
    db_session.commit()

    rows = subscriber_reports_service.lifecycle_recent_churns(db_session)

    assert len(rows) == 5
    assert all(row["subscriber_number"] != "SUB-OLD-CHURN" for row in rows)
    assert rows[0]["subscriber_number"] == "SUB-RECENT-0"
    assert rows[-1]["subscriber_number"] == "SUB-RECENT-4"


def test_service_quality_kpis_counts_active_work_orders_in_period_without_subscriber_link(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    db_session.add_all(
        [
            WorkOrder(
                title="In period active",
                status=WorkOrderStatus.draft,
                is_active=True,
                project_id=uuid4(),
                created_at=now - timedelta(days=5),
            ),
            WorkOrder(
                title="Out of period active",
                status=WorkOrderStatus.draft,
                is_active=True,
                project_id=uuid4(),
                created_at=now - timedelta(days=45),
            ),
            WorkOrder(
                title="Completed in period",
                status=WorkOrderStatus.completed,
                is_active=True,
                project_id=uuid4(),
                created_at=now - timedelta(days=3),
            ),
        ]
    )
    db_session.commit()

    kpis = subscriber_reports_service.service_quality_kpis(db_session, start_dt, end_dt)

    assert kpis["active_work_orders"] == 1


def test_service_quality_kpis_falls_back_to_ticket_due_dates_for_sla_compliance(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    db_session.add_all(
        [
            Ticket(
                title="Met SLA",
                status=TicketStatus.closed,
                is_active=True,
                due_at=now - timedelta(days=4),
                closed_at=now - timedelta(days=5),
                created_at=now - timedelta(days=7),
            ),
            Ticket(
                title="Breached SLA",
                status=TicketStatus.closed,
                is_active=True,
                due_at=now - timedelta(days=6),
                closed_at=now - timedelta(days=4),
                created_at=now - timedelta(days=8),
            ),
        ]
    )
    db_session.commit()

    kpis = subscriber_reports_service.service_quality_kpis(db_session, start_dt, end_dt)

    assert kpis["sla_compliance"] == 50.0


def test_service_quality_wo_by_type_counts_in_period_without_subscriber_link(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    db_session.add_all(
        [
            WorkOrder(
                title="Install WO",
                status=WorkOrderStatus.draft,
                is_active=True,
                work_type="install",
                project_id=uuid4(),
                created_at=now - timedelta(days=5),
            ),
            WorkOrder(
                title="Repair WO",
                status=WorkOrderStatus.draft,
                is_active=True,
                work_type="repair",
                project_id=uuid4(),
                created_at=now - timedelta(days=4),
            ),
            WorkOrder(
                title="Old WO",
                status=WorkOrderStatus.draft,
                is_active=True,
                work_type="install",
                project_id=uuid4(),
                created_at=now - timedelta(days=45),
            ),
        ]
    )
    db_session.commit()

    rows = subscriber_reports_service.service_quality_wo_by_type(db_session, start_dt, end_dt)

    assert rows == {"install": 1, "repair": 1}


def test_service_quality_high_maintenance_keeps_subscribers_without_person_link(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    subscriber = Subscriber(
        subscriber_number="SUB-NO-PERSON-001",
        status=SubscriberStatus.active,
        is_active=True,
    )
    db_session.add(subscriber)
    db_session.flush()

    db_session.add_all(
        [
            Ticket(
                title="Ticket one",
                status=TicketStatus.open,
                is_active=True,
                subscriber_id=subscriber.id,
                created_at=now - timedelta(days=3),
            ),
            Ticket(
                title="Ticket two",
                status=TicketStatus.open,
                is_active=True,
                subscriber_id=subscriber.id,
                created_at=now - timedelta(days=2),
            ),
        ]
    )
    db_session.commit()

    rows = subscriber_reports_service.service_quality_high_maintenance(db_session, start_dt, end_dt, limit=5)

    assert rows[0]["name"] == "SUB-NO-PERSON-001"
    assert rows[0]["subscriber_number"] == "SUB-NO-PERSON-001"
    assert rows[0]["tickets"] == 2
    assert rows[0]["total"] == 2


def test_service_quality_high_maintenance_deduplicates_same_clean_name(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    person = Person(
        first_name="Test",
        last_name="User",
        display_name="CKK Capital Limited Lagos Branch",
        email=f"ckk-{uuid4().hex}@example.com",
    )
    db_session.add(person)
    db_session.flush()

    subscriber_a = Subscriber(
        subscriber_number="CKK Capital Limited Lagos Branch",
        status=SubscriberStatus.active,
        is_active=True,
    )
    subscriber_b = Subscriber(
        subscriber_number="100017497",
        person_id=person.id,
        status=SubscriberStatus.active,
        is_active=True,
    )
    db_session.add_all([subscriber_a, subscriber_b])
    db_session.flush()

    db_session.add_all(
        [
            Ticket(
                title="Ticket one",
                status=TicketStatus.open,
                is_active=True,
                subscriber_id=subscriber_a.id,
                created_at=now - timedelta(days=3),
            ),
            Ticket(
                title="Ticket two",
                status=TicketStatus.open,
                is_active=True,
                subscriber_id=subscriber_b.id,
                created_at=now - timedelta(days=2),
            ),
            Ticket(
                title="Ticket three",
                status=TicketStatus.open,
                is_active=True,
                subscriber_id=subscriber_b.id,
                created_at=now - timedelta(days=1),
            ),
        ]
    )
    db_session.commit()

    rows = subscriber_reports_service.service_quality_high_maintenance(db_session, start_dt, end_dt, limit=10)

    assert rows[0]["name"] == "CKK Capital Limited Lagos Branch"
    assert rows[0]["tickets"] == 3
    assert len([row for row in rows if row["name"] == "CKK Capital Limited Lagos Branch"]) == 1


def test_service_quality_regional_ignores_city_when_region_missing(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    person = Person(first_name="Regional", last_name="Fallback", email=f"regional-{uuid4().hex}@example.com")
    db_session.add(person)
    db_session.flush()

    subscriber = Subscriber(
        person_id=person.id,
        subscriber_number="SUB-REGION-001",
        status=SubscriberStatus.active,
        is_active=True,
        service_city="FCT, Abuja",
    )
    db_session.add(subscriber)
    db_session.flush()

    db_session.add(
        Ticket(
            title="Regional ticket",
            status=TicketStatus.resolved,
            is_active=True,
            subscriber_id=subscriber.id,
            created_at=now - timedelta(days=5),
            resolved_at=now - timedelta(days=4),
        )
    )
    db_session.add(
        WorkOrder(
            title="Regional work order",
            status=WorkOrderStatus.draft,
            is_active=True,
            project_id=uuid4(),
            created_at=now - timedelta(days=3),
        )
    )
    db_session.commit()

    rows = subscriber_reports_service.service_quality_regional(db_session, start_dt, end_dt)

    assert rows == []


def test_subscriber_overview_page_calculates_net_growth(monkeypatch):
    monkeypatch.setattr(reports_web, "get_sidebar_stats", lambda _db: {"open_tickets": 0, "dispatch_jobs": 0})
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_kpis",
        lambda _db, _start_dt, _end_dt, subscriber_ids=None: {
            "active_subscribers": 120,
            "activations": 10,
            "terminations": 3,
            "suspended_count": 5,
            "avg_tickets_per_sub": 0.4,
            "regions_covered": 4,
        },
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_growth_trend",
        lambda _db, _start_dt, _end_dt, subscriber_ids=None: [
            {"date": "2026-03-01", "activations": 10, "terminations": 3}
        ],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_status_distribution",
        lambda _db, subscriber_ids=None: {"active": 120},
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_plan_distribution",
        lambda _db, limit=10, subscriber_ids=None: [{"plan": "Home 100", "count": 60}],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_regional_breakdown",
        lambda _db, _start_dt, _end_dt, subscriber_ids=None: [
            {"region": "Central", "active": 60, "suspended": 2, "terminated": 1, "new_in_period": 4, "ticket_count": 3}
        ],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_filter_options",
        lambda _db: {"regions": ["Central"], "plans": ["Home 100"]},
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_filtered_subscriber_ids",
        lambda _db, status=None, region=None: None,
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/overview",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )

    response = reports_web.subscriber_overview(
        request=request,
        db=None,
        days=30,
        start_date=None,
        end_date=None,
    )

    assert response.status_code == 200
    body = response.body.decode()
    assert "Subscriber Overview" in body
    assert "Net Growth" in body
    assert ">7<" in body


def test_subscriber_overview_page_renders_filters_kpis_and_distribution_sections(monkeypatch):
    monkeypatch.setattr(reports_web, "get_sidebar_stats", lambda _db: {"open_tickets": 0, "dispatch_jobs": 0})
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_kpis",
        lambda _db, _start_dt, _end_dt, subscriber_ids=None: {
            "active_subscribers": 7060,
            "activations": 120,
            "terminations": 55,
            "net_growth": 65,
            "suspended_count": 40,
            "suspended_pct": 3.2,
            "avg_tickets_per_sub": 0.4,
            "regions_covered": 8,
        },
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_growth_trend",
        lambda _db, _start_dt, _end_dt, subscriber_ids=None: [
            {"date": "2026-03-01", "activations": 10, "terminations": 3}
        ],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_status_distribution",
        lambda _db, subscriber_ids=None: {"active": 7060, "suspended": 40, "terminated": 12},
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_plan_distribution",
        lambda _db, limit=10, subscriber_ids=None: [{"plan": "Home 100", "count": 60}],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_regional_breakdown",
        lambda _db, _start_dt, _end_dt, subscriber_ids=None: [
            {"region": "Central", "active": 60, "suspended": 2, "terminated": 1, "new_in_period": 4, "ticket_count": 3}
        ],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_filter_options",
        lambda _db: {"regions": ["Central"], "plans": ["Home 100"]},
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_filtered_subscriber_ids",
        lambda _db, status=None, region=None: None,
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/overview",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )

    response = reports_web.subscriber_overview(
        request=request,
        db=None,
        days=30,
        start_date=None,
        end_date=None,
    )

    body = response.body.decode()
    assert response.status_code == 200
    assert "Filters" in body
    assert "Growth Trend" in body
    assert "Status Distribution" in body
    assert "Service Plan Distribution" in body
    assert "Active Subscribers" in body
    assert "7060" in body


def test_subscriber_overview_status_and_region_filters_affect_scope(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(reports_web, "get_sidebar_stats", lambda _db: {"open_tickets": 0, "dispatch_jobs": 0})
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_filter_options",
        lambda _db: {"regions": ["Abuja"], "plans": []},
    )

    def _capture_scope(_db, status=None, region=None):
        captured["status"] = status.value if status else None
        captured["region"] = region
        return ["sub-1"]

    monkeypatch.setattr(subscriber_reports_service, "overview_filtered_subscriber_ids", _capture_scope)
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_kpis",
        lambda _db, _start_dt, _end_dt, subscriber_ids=None: {
            "active_subscribers": 1,
            "activations": 1,
            "terminations": 0,
            "suspended_count": 0,
            "avg_tickets_per_sub": 0,
            "regions_covered": 1,
        },
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_growth_trend",
        lambda _db, _start_dt, _end_dt, subscriber_ids=None: [],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_status_distribution",
        lambda _db, subscriber_ids=None: {},
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_plan_distribution",
        lambda _db, limit=10, subscriber_ids=None: [],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_regional_breakdown",
        lambda _db, _start_dt, _end_dt, subscriber_ids=None: [],
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/overview",
            "headers": [],
            "query_string": b"status=active&region=Abuja",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )

    response = reports_web.subscriber_overview(
        request=request,
        db=None,
        days=30,
        start_date=None,
        end_date=None,
        status="active",
        region="Abuja",
    )

    assert response.status_code == 200
    assert captured == {"status": "active", "region": "Abuja"}


def test_subscriber_lifecycle_page_renders(monkeypatch):
    monkeypatch.setattr(reports_web, "get_sidebar_stats", lambda _db: {"open_tickets": 0, "dispatch_jobs": 0})
    monkeypatch.setattr(
        subscriber_reports_service,
        "lifecycle_kpis",
        lambda _db, _start_dt, _end_dt: {
            "conversion_rate": 12.5,
            "avg_days_to_convert": 7.2,
            "churn_rate": 1.8,
            "terminated_in_period": 3,
            "pipeline_value": 25000.0,
            "leads_won": 4,
        },
    )
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_funnel", lambda _db: [{"stage": "lead", "count": 20}])
    monkeypatch.setattr(
        subscriber_reports_service, "lifecycle_churn_trend", lambda _db: [{"month": "2026-03", "count": 2}]
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "lifecycle_conversion_by_source",
        lambda _db, _start_dt, _end_dt: [{"source": "Referral", "total": 10, "won": 3}],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "lifecycle_recent_churns",
        lambda _db: [
            {
                "name": "Jane Doe",
                "subscriber_number": "SUB-1",
                "region": "North",
                "terminated_at": "2026-03-01",
                "tenure_days": 90,
            }
        ],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "lifecycle_longest_tenure",
        lambda _db: [{"name": "John Doe", "subscriber_number": "SUB-2", "plan": "Home 50", "tenure_days": 420}],
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/lifecycle",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )

    response = reports_web.subscriber_lifecycle(
        request=request,
        db=None,
        days=30,
        start_date=None,
        end_date=None,
    )

    assert response.status_code == 200
    body = response.body.decode()
    assert "Subscriber Lifecycle" in body
    assert "Recent Churn" in body


def test_churned_subscribers_kpis_and_rows_use_selected_period(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=90)
    end_dt = now

    recent_person = Person(first_name="Recent", last_name="Churn", email=f"recent-{uuid4().hex}@example.com")
    old_person = Person(first_name="Old", last_name="Churn", email=f"old-{uuid4().hex}@example.com")
    db_session.add_all([recent_person, old_person])
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=recent_person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.terminated,
                is_active=False,
                service_plan="Home 200",
                service_region="Wuse 2",
                activated_at=now - timedelta(days=180),
                terminated_at=now - timedelta(days=20),
            ),
            Subscriber(
                person_id=old_person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.terminated,
                is_active=False,
                service_plan="Business 500",
                service_region="Lagos",
                activated_at=now - timedelta(days=400),
                terminated_at=now - timedelta(days=150),
            ),
        ]
    )
    db_session.commit()

    kpis = subscriber_reports_service.churned_subscribers_kpis(db_session, start_dt, end_dt)
    rows = subscriber_reports_service.churned_subscribers_rows(db_session, start_dt, end_dt, limit=20)
    trend = subscriber_reports_service.churned_subscribers_trend(db_session, start_dt, end_dt)

    assert kpis["churned_count"] == 1
    assert kpis["impacted_regions"] == 1
    assert kpis["impacted_plans"] == 1
    assert rows == [
        {
            "name": "Recent Churn",
            "subscriber_number": rows[0]["subscriber_number"],
            "plan": "Home 200",
            "region": "Abuja",
            "activated_at": (now - timedelta(days=180)).strftime("%Y-%m-%d"),
            "terminated_at": (now - timedelta(days=20)).strftime("%Y-%m-%d"),
            "tenure_days": 160,
        }
    ]
    assert trend == [{"date": (now - timedelta(days=20)).strftime("%Y-%m-%d"), "count": 1}]


def test_churned_subscribers_page_renders(monkeypatch):
    monkeypatch.setattr(reports_web, "get_sidebar_stats", lambda _db: {"open_tickets": 0, "dispatch_jobs": 0})
    monkeypatch.setattr(
        subscriber_reports_service,
        "churned_subscribers_kpis",
        lambda _db, _start_dt, _end_dt: {
            "churned_count": 12,
            "avg_tenure_days": 147.5,
            "impacted_regions": 4,
            "impacted_plans": 3,
        },
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "churned_subscribers_trend",
        lambda _db, _start_dt, _end_dt: [{"date": "2026-03-01", "count": 2}],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "churned_subscribers_rows",
        lambda _db, _start_dt, _end_dt: [
            {
                "name": "Jane Doe",
                "subscriber_number": "SUB-1",
                "plan": "Home 100",
                "region": "Abuja",
                "activated_at": "2025-01-10",
                "terminated_at": "2026-03-01",
                "tenure_days": 416,
            }
        ],
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/churned",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )

    response = reports_web.churned_subscribers(
        request=request,
        db=None,
        days=90,
        start_date=None,
        end_date=None,
    )

    assert response.status_code == 200
    body = response.body.decode()
    assert "Churned Subscribers" in body
    assert "Average Tenure" in body
    assert "Jane Doe" in body


def test_subscriber_service_quality_page_renders(monkeypatch):
    monkeypatch.setattr(reports_web, "get_sidebar_stats", lambda _db: {"open_tickets": 0, "dispatch_jobs": 0})
    monkeypatch.setattr(
        subscriber_reports_service,
        "service_quality_kpis",
        lambda _db, _start_dt, _end_dt: {
            "subs_with_open_tickets": 11,
            "avg_resolution_hrs": 6.4,
            "repeat_contact_rate": 21.0,
            "active_work_orders": 5,
            "sla_compliance": 93.2,
        },
    )
    monkeypatch.setattr(
        subscriber_reports_service, "service_quality_tickets_by_type", lambda _db, _start_dt, _end_dt: {"outage": 4}
    )
    monkeypatch.setattr(
        subscriber_reports_service, "service_quality_wo_by_type", lambda _db, _start_dt, _end_dt: {"repair": 2}
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "service_quality_weekly_trend",
        lambda _db, _start_dt, _end_dt: [{"week": "2026-03-02", "created": 3, "resolved": 2}],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "service_quality_high_maintenance",
        lambda _db, _start_dt, _end_dt: [
            {"name": "Acme Corp", "region": "Central", "tickets": 3, "work_orders": 1, "projects": 0, "total": 4}
        ],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "service_quality_regional",
        lambda _db, _start_dt, _end_dt: [
            {
                "region": "Central",
                "active_subscribers": 40,
                "avg_tickets_per_sub": 0.5,
                "avg_resolution_hrs": 5.0,
                "wo_count": 3,
            }
        ],
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/service-quality",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )

    response = reports_web.subscriber_service_quality(
        request=request,
        db=None,
        days=30,
        start_date=None,
        end_date=None,
    )

    assert response.status_code == 200
    body = response.body.decode()
    assert "Subscriber Service Quality" in body
    assert "High Maintenance Subscribers" in body


def test_subscriber_revenue_page_renders(monkeypatch):
    monkeypatch.setattr(reports_web, "get_sidebar_stats", lambda _db: {"open_tickets": 0, "dispatch_jobs": 0})
    monkeypatch.setattr(
        subscriber_reports_service,
        "revenue_kpis",
        lambda _db, _start_dt, _end_dt: {
            "total_value": 100000.0,
            "order_count": 8,
            "avg_value": 12500.0,
            "pipeline_value": 35000.0,
            "collection_rate": 88.4,
            "pending_fulfillment": 2,
        },
    )
    monkeypatch.setattr(
        subscriber_reports_service, "revenue_monthly_trend", lambda _db: [{"month": "2026-03", "total": 100000.0}]
    )
    monkeypatch.setattr(
        subscriber_reports_service, "revenue_payment_status", lambda _db, _start_dt, _end_dt: {"paid": 5, "pending": 3}
    )
    monkeypatch.setattr(
        subscriber_reports_service, "revenue_order_status", lambda _db, _start_dt, _end_dt: {"confirmed": 4, "paid": 4}
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "revenue_top_subscribers",
        lambda _db, _start_dt, _end_dt: [
            {
                "name": "Revenue Leader",
                "email": "lead@example.com",
                "total_revenue": 50000.0,
                "order_count": 3,
                "status": "active",
            }
        ],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "revenue_outstanding_balances",
        lambda _db: [
            {
                "order_number": "SO-1",
                "customer": "Customer A",
                "balance": 1500.0,
                "due_date": "2026-03-05",
                "days_overdue": 10,
            }
        ],
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/revenue",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )

    response = reports_web.subscriber_revenue(
        request=request,
        db=None,
        days=30,
        start_date=None,
        end_date=None,
    )

    assert response.status_code == 200
    body = response.body.decode()
    assert "Subscriber Revenue - Admin" in body
    assert "Outstanding Balances" in body
