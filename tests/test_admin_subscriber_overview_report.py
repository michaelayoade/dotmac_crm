from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from starlette.requests import Request

from app.models.crm.enums import LeadStatus
from app.models.crm.sales import Lead
from app.models.event_store import EventStore
from app.models.person import ChannelType, PartyStatus, Person, PersonChannel
from app.models.sales_order import SalesOrder, SalesOrderPaymentStatus, SalesOrderStatus
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


def test_lifecycle_kpis_uses_behavioral_last_payment_churn(db_session):
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
                activated_at=now - timedelta(days=90),
                created_at=now - timedelta(days=90),
            ),
            Subscriber(
                person_id=person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                activated_at=now - timedelta(days=60),
                created_at=now - timedelta(days=60),
                sync_metadata={"last_transaction_date": (now - timedelta(days=45)).strftime("%Y-%m-%d")},
            ),
        ]
    )
    db_session.commit()

    kpis = subscriber_reports_service.lifecycle_kpis(db_session, start_dt, end_dt)

    assert kpis["terminated_in_period"] == 1
    assert kpis["churn_rate"] == 50.0
    assert kpis["operational_churn_in_period"] == 0
    assert kpis["behavioral_churn_in_period"] == 1
    assert kpis["total_active_subscribers_start"] == 2


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
            activated_at=now - timedelta(days=60),
            created_at=now - timedelta(days=60),
        )
        for person in people[:100]
    ]
    subscribers.append(
        Subscriber(
            person_id=people[100].id,
            subscriber_number=f"SUB-{uuid4().hex[:8]}",
            status=SubscriberStatus.active,
            is_active=True,
            activated_at=now - timedelta(days=60),
            created_at=now - timedelta(days=60),
            sync_metadata={"last_transaction_date": (now - timedelta(days=45)).strftime("%Y-%m-%d")},
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
                activated_at=now - timedelta(days=90),
                created_at=now - timedelta(days=90),
            ),
            Subscriber(
                person_id=people[1].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                activated_at=now - timedelta(days=60),
                created_at=now - timedelta(days=60),
                sync_metadata={"last_transaction_date": (now - timedelta(days=45)).strftime("%Y-%m-%d")},
            ),
            Subscriber(
                person_id=people[2].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.terminated,
                is_active=False,
                activated_at=now - timedelta(days=90),
                created_at=now - timedelta(days=90),
                terminated_at=now - timedelta(days=40),
            ),
        ]
    )
    db_session.commit()

    kpis = subscriber_reports_service.lifecycle_kpis(db_session, start_dt, end_dt)

    assert kpis["terminated_in_period"] == 1
    assert kpis["churn_rate"] == 50.0


def test_lifecycle_kpis_uses_behavioral_invoice_due_non_payment_churn(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    person = Person(first_name="Due", last_name="Churn", email=f"due-churn-{uuid4().hex}@example.com")
    db_session.add(person)
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                activated_at=now - timedelta(days=120),
                created_at=now - timedelta(days=120),
            ),
            Subscriber(
                person_id=person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                activated_at=now - timedelta(days=90),
                created_at=now - timedelta(days=90),
                next_bill_date=now - timedelta(days=45),
                balance="120.00",
            ),
        ]
    )
    db_session.commit()

    kpis = subscriber_reports_service.lifecycle_kpis(db_session, start_dt, end_dt)

    assert kpis["terminated_in_period"] == 1
    assert kpis["churn_rate"] == 50.0
    assert kpis["operational_churn_in_period"] == 0
    assert kpis["behavioral_churn_in_period"] == 1
    assert kpis["total_active_subscribers_start"] == 2


def test_lifecycle_kpis_prioritizes_operational_over_behavioral(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    person = Person(first_name="Priority", last_name="Churn", email=f"priority-{uuid4().hex}@example.com")
    db_session.add(person)
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                activated_at=now - timedelta(days=120),
                created_at=now - timedelta(days=120),
            ),
            Subscriber(
                person_id=person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.terminated,
                is_active=True,
                activated_at=now - timedelta(days=90),
                created_at=now - timedelta(days=90),
                terminated_at=now - timedelta(days=10),
                sync_metadata={"last_transaction_date": (now - timedelta(days=80)).strftime("%Y-%m-%d")},
            ),
        ]
    )
    db_session.commit()

    kpis = subscriber_reports_service.lifecycle_kpis(db_session, start_dt, end_dt)

    assert kpis["terminated_in_period"] == 1
    assert kpis["operational_churn_in_period"] == 1
    assert kpis["behavioral_churn_in_period"] == 0


def test_lifecycle_kpis_falls_back_to_created_at_for_starting_base(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    active_person = Person(first_name="Activated", last_name="Base", email=f"activated-{uuid4().hex}@example.com")
    missing_activation_person = Person(
        first_name="Missing",
        last_name="Activation",
        email=f"missing-activation-{uuid4().hex}@example.com",
    )
    db_session.add_all([active_person, missing_activation_person])
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=active_person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                activated_at=now - timedelta(days=90),
                created_at=now - timedelta(days=90),
            ),
            Subscriber(
                person_id=missing_activation_person.id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.active,
                is_active=True,
                created_at=now - timedelta(days=90),
                sync_metadata={"last_transaction_date": (now - timedelta(days=45)).strftime("%Y-%m-%d")},
            ),
        ]
    )
    db_session.commit()

    kpis = subscriber_reports_service.lifecycle_kpis(db_session, start_dt, end_dt)

    assert kpis["total_active_subscribers_start"] == 2
    assert kpis["terminated_in_period"] == 1
    assert kpis["behavioral_churn_in_period"] == 1
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


def test_lifecycle_kpis_calculates_average_lifecycle_upgrade_downgrade_and_engagement(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    people = [
        Person(first_name="Active", last_name="One", email=f"active-one-{uuid4().hex}@example.com"),
        Person(first_name="Active", last_name="Two", email=f"active-two-{uuid4().hex}@example.com"),
        Person(first_name="Churn", last_name="One", email=f"churn-one-{uuid4().hex}@example.com"),
        Person(first_name="Churn", last_name="Two", email=f"churn-two-{uuid4().hex}@example.com"),
    ]
    db_session.add_all(people)
    db_session.flush()

    subscribers = [
        Subscriber(
            person_id=people[0].id,
            subscriber_number=f"SUB-{uuid4().hex[:8]}",
            status=SubscriberStatus.active,
            is_active=True,
            activated_at=now - timedelta(days=120),
        ),
        Subscriber(
            person_id=people[1].id,
            subscriber_number=f"SUB-{uuid4().hex[:8]}",
            status=SubscriberStatus.active,
            is_active=True,
            activated_at=now - timedelta(days=90),
        ),
        Subscriber(
            person_id=people[2].id,
            subscriber_number=f"SUB-{uuid4().hex[:8]}",
            status=SubscriberStatus.terminated,
            is_active=False,
            activated_at=now - timedelta(days=70),
            terminated_at=now - timedelta(days=10),
        ),
        Subscriber(
            person_id=people[3].id,
            subscriber_number=f"SUB-{uuid4().hex[:8]}",
            status=SubscriberStatus.terminated,
            is_active=False,
            activated_at=now - timedelta(days=65),
            terminated_at=now - timedelta(days=5),
        ),
    ]
    db_session.add_all(subscribers)
    db_session.flush()

    db_session.add_all(
        [
            EventStore(
                event_id=uuid4(),
                event_type="subscription.upgraded",
                payload={},
                subscriber_id=subscribers[0].id,
                created_at=now - timedelta(days=8),
            ),
            EventStore(
                event_id=uuid4(),
                event_type="subscription.downgraded",
                payload={},
                subscriber_id=subscribers[1].id,
                created_at=now - timedelta(days=6),
            ),
        ]
    )
    db_session.add(
        Ticket(
            title="Active engagement",
            status=TicketStatus.open,
            is_active=True,
            subscriber_id=subscribers[0].id,
            created_at=now - timedelta(days=4),
        )
    )
    db_session.add(
        WorkOrder(
            title="Active work order",
            status=WorkOrderStatus.draft,
            is_active=True,
            subscriber_id=subscribers[1].id,
            project_id=uuid4(),
            created_at=now - timedelta(days=3),
        )
    )
    db_session.commit()

    kpis = subscriber_reports_service.lifecycle_kpis(db_session, start_dt, end_dt)

    assert kpis["avg_lifecycle_days"] == 60.0
    assert kpis["avg_lifecycle_months"] == 2.0
    assert kpis["upgraded_in_period"] == 1
    assert kpis["downgraded_in_period"] == 1
    assert kpis["upgrade_rate"] == 25.0
    assert kpis["downgrade_rate"] == 25.0
    assert kpis["engagement_score"] == 58.3


def test_lifecycle_time_to_convert_distribution_buckets_won_leads(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=90)
    end_dt = now

    person = Person(first_name="Convert", last_name="Histogram", email=f"convert-{uuid4().hex}@example.com")
    db_session.add(person)
    db_session.flush()

    db_session.add_all(
        [
            Lead(
                person_id=person.id,
                status=LeadStatus.won,
                is_active=True,
                created_at=now - timedelta(days=10),
                closed_at=now - timedelta(days=5),
            ),
            Lead(
                person_id=person.id,
                status=LeadStatus.won,
                is_active=True,
                created_at=now - timedelta(days=25),
                closed_at=now - timedelta(days=5),
            ),
            Lead(
                person_id=person.id,
                status=LeadStatus.won,
                is_active=True,
                created_at=now - timedelta(days=120),
                closed_at=now - timedelta(days=100),
            ),
        ]
    )
    db_session.commit()

    distribution = subscriber_reports_service.lifecycle_time_to_convert_distribution(db_session, start_dt, end_dt)

    assert distribution == [
        {"label": "0-7 days", "count": 1},
        {"label": "8-14 days", "count": 0},
        {"label": "15-30 days", "count": 1},
        {"label": "31-60 days", "count": 0},
        {"label": "61-90 days", "count": 0},
        {"label": "91+ days", "count": 0},
    ]


def test_lifecycle_plan_migration_flow_groups_plan_movements_from_event_payloads(db_session):
    now = datetime.now(UTC)
    start_dt = now - timedelta(days=30)
    end_dt = now

    db_session.add_all(
        [
            EventStore(
                event_id=uuid4(),
                event_type="subscription.upgraded",
                payload={"from_plan": "Home 100", "to_plan": "Home 200"},
                created_at=now - timedelta(days=7),
            ),
            EventStore(
                event_id=uuid4(),
                event_type="subscription.downgraded",
                payload={"before": {"service_plan": "Business 500"}, "after": {"service_plan": "Business 300"}},
                created_at=now - timedelta(days=6),
            ),
            EventStore(
                event_id=uuid4(),
                event_type="subscription.upgraded",
                payload={"from_plan": "Home 100", "to_plan": "Home 200"},
                created_at=now - timedelta(days=3),
            ),
        ]
    )
    db_session.commit()

    flows = subscriber_reports_service.lifecycle_plan_migration_flow(db_session, start_dt, end_dt)

    assert flows == [
        {"source": "Home 100", "target": "Home 200", "count": 2},
        {"source": "Business 500", "target": "Business 300", "count": 1},
    ]


def test_lifecycle_retention_cohorts_returns_monthly_percentages(db_session):
    now = datetime(2026, 3, 27, tzinfo=UTC)
    start_dt = datetime(2026, 1, 1, tzinfo=UTC)
    end_dt = now

    people = [
        Person(first_name="Cohort", last_name="One", email=f"cohort-one-{uuid4().hex}@example.com"),
        Person(first_name="Cohort", last_name="Two", email=f"cohort-two-{uuid4().hex}@example.com"),
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
                activated_at=datetime(2026, 1, 10, tzinfo=UTC),
            ),
            Subscriber(
                person_id=people[1].id,
                subscriber_number=f"SUB-{uuid4().hex[:8]}",
                status=SubscriberStatus.terminated,
                is_active=False,
                activated_at=datetime(2026, 1, 12, tzinfo=UTC),
                terminated_at=datetime(2026, 2, 15, tzinfo=UTC),
            ),
        ]
    )
    db_session.commit()

    cohorts = subscriber_reports_service.lifecycle_retention_cohorts(db_session, start_dt, end_dt)

    assert cohorts["months"] == ["2026-01", "2026-02", "2026-03"]
    assert cohorts["rows"][0]["cohort"] == "2026-01"
    assert cohorts["rows"][0]["size"] == 2
    assert [cell["retention_pct"] for cell in cohorts["rows"][0]["values"]] == [100.0, 50.0, 50.0]


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
    current_month_label = (now - timedelta(days=10)).strftime("%b %Y")

    assert len(trend) == 12
    assert {"month": current_month_label, "month_key": current_month, "count": 2} in trend


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


def test_lifecycle_top_subscribers_by_value_orders_by_sales_order_total(db_session):
    person_one = Person(first_name="Value", last_name="Leader", email=f"value-one-{uuid4().hex}@example.com")
    person_two = Person(first_name="Value", last_name="Runner", email=f"value-two-{uuid4().hex}@example.com")
    db_session.add_all([person_one, person_two])
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=person_one.id,
                subscriber_number="SUB-VALUE-1",
                status=SubscriberStatus.active,
                is_active=True,
                service_plan="Home 200",
                service_region="Lagos",
            ),
            Subscriber(
                person_id=person_two.id,
                subscriber_number="SUB-VALUE-2",
                status=SubscriberStatus.active,
                is_active=True,
                service_plan="Home 100",
                service_region="Abuja",
            ),
            SalesOrder(
                person_id=person_one.id,
                order_number=f"SO-{uuid4().hex[:8]}",
                status=SalesOrderStatus.paid,
                total=15000,
                amount_paid=15000,
            ),
            SalesOrder(
                person_id=person_one.id,
                order_number=f"SO-{uuid4().hex[:8]}",
                status=SalesOrderStatus.fulfilled,
                total=5000,
                amount_paid=2500,
            ),
            SalesOrder(
                person_id=person_two.id,
                order_number=f"SO-{uuid4().hex[:8]}",
                status=SalesOrderStatus.confirmed,
                total=12000,
                amount_paid=4000,
            ),
        ]
    )
    db_session.commit()

    rows = subscriber_reports_service.lifecycle_top_subscribers_by_value(db_session)

    assert rows[0]["name"] == "Value Leader"
    assert rows[0]["subscriber_id"]
    assert rows[0]["subscriber_number"] == "SUB-VALUE-1"
    assert rows[0]["plan"] == "Home 200"
    assert rows[0]["status"] == "active"
    assert rows[0]["activated_at"]
    assert rows[0]["tenure_months"] >= 0
    assert rows[0]["order_count"] == 2
    assert rows[0]["total_paid"] == 17500.0
    assert rows[0]["avg_monthly_spend"] == 17500.0


def test_lifecycle_top_subscribers_by_value_includes_non_active_subscribers(db_session):
    active_person = Person(first_name="Active", last_name="Customer", email=f"active-value-{uuid4().hex}@example.com")
    terminated_person = Person(
        first_name="Former",
        last_name="Customer",
        email=f"terminated-value-{uuid4().hex}@example.com",
    )
    db_session.add_all([active_person, terminated_person])
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=active_person.id,
                subscriber_number="SUB-ACTIVE-1",
                status=SubscriberStatus.active,
                is_active=True,
                activated_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            Subscriber(
                person_id=terminated_person.id,
                subscriber_number="SUB-TERM-1",
                status=SubscriberStatus.terminated,
                is_active=False,
                activated_at=datetime(2025, 1, 1, tzinfo=UTC),
                terminated_at=datetime(2026, 2, 1, tzinfo=UTC),
            ),
            SalesOrder(
                person_id=active_person.id,
                order_number=f"SO-{uuid4().hex[:8]}",
                status=SalesOrderStatus.paid,
                total=5000,
                amount_paid=5000,
            ),
            SalesOrder(
                person_id=terminated_person.id,
                order_number=f"SO-{uuid4().hex[:8]}",
                status=SalesOrderStatus.paid,
                total=25000,
                amount_paid=25000,
            ),
        ]
    )
    db_session.commit()

    rows = subscriber_reports_service.lifecycle_top_subscribers_by_value(db_session, limit=10)

    assert rows[0]["name"] == "Former Customer"
    assert rows[0]["subscriber_number"] == "SUB-TERM-1"
    assert rows[0]["plan"] == ""
    assert rows[0]["status"] == "terminated"
    assert rows[0]["total_paid"] == 25000.0


def test_lifecycle_top_subscribers_by_value_cleans_duplicate_person_rows(db_session):
    person = Person(first_name="Merged", last_name="Customer", email=f"merged-{uuid4().hex}@example.com")
    db_session.add(person)
    db_session.flush()

    db_session.add_all(
        [
            Subscriber(
                person_id=person.id,
                subscriber_number="SUB-MERGE-1",
                status=SubscriberStatus.active,
                is_active=True,
                activated_at=datetime(2025, 1, 1, tzinfo=UTC),
            ),
            Subscriber(
                person_id=person.id,
                subscriber_number="SUB-MERGE-2",
                status=SubscriberStatus.active,
                is_active=True,
                activated_at=datetime(2025, 1, 1, tzinfo=UTC),
            ),
            SalesOrder(
                person_id=person.id,
                order_number=f"SO-{uuid4().hex[:8]}",
                status=SalesOrderStatus.paid,
                total=10000,
                amount_paid=10000,
            ),
        ]
    )
    db_session.commit()

    rows = subscriber_reports_service.lifecycle_top_subscribers_by_value(db_session, limit=20)
    matching_rows = [row for row in rows if row["name"] == "Merged Customer"]

    assert len(matching_rows) == 1
    assert matching_rows[0]["subscriber_number"] == "SUB-MERGE-1"
    assert matching_rows[0]["plan"] == ""
    assert matching_rows[0]["total_paid"] == 10000.0


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


def test_subscriber_lifecycle_sorts_top_subscribers_by_tenure_when_requested(monkeypatch):
    monkeypatch.setattr(reports_web, "get_sidebar_stats", lambda _db: {"open_tickets": 0, "dispatch_jobs": 0})
    monkeypatch.setattr(
        subscriber_reports_service,
        "lifecycle_kpis",
        lambda _db, _start_dt, _end_dt: {
            "conversion_rate": 0,
            "avg_days_to_convert": 0,
            "churn_rate": 0,
            "terminated_in_period": 0,
            "avg_lifecycle_days": 0,
            "avg_lifecycle_months": 0,
            "upgrade_rate": 0,
            "downgrade_rate": 0,
            "engagement_score": 0,
            "pipeline_value": 0,
            "leads_won": 0,
        },
    )
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_funnel", lambda _db: [])
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_churn_trend", lambda _db: [])
    monkeypatch.setattr(
        subscriber_reports_service, "lifecycle_conversion_by_source", lambda _db, _start_dt, _end_dt: []
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "lifecycle_retention_cohorts",
        lambda _db, _start_dt, _end_dt: {"months": [], "rows": []},
    )
    monkeypatch.setattr(
        subscriber_reports_service, "lifecycle_time_to_convert_distribution", lambda _db, _start_dt, _end_dt: []
    )
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_plan_migration_flow", lambda _db, _start_dt, _end_dt: [])
    monkeypatch.setattr(subscriber_reports_service, "overview_plan_distribution", lambda _db, limit=8: [])
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_recent_churns", lambda _db: [])
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_longest_tenure", lambda _db: [])
    monkeypatch.setattr(
        subscriber_reports_service,
        "lifecycle_top_subscribers_by_value",
        lambda _db: [
            {
                "subscriber_id": "sub-1",
                "name": "Lower Paid Longer",
                "subscriber_number": "SUB-1",
                "plan": "Business Fiber",
                "status": "active",
                "activated_at": "2024-01-01",
                "tenure_months": 20.0,
                "order_count": 1,
                "total_paid": 1000.0,
                "avg_monthly_spend": 50.0,
            },
            {
                "subscriber_id": "sub-2",
                "name": "Higher Paid Shorter",
                "subscriber_number": "SUB-2",
                "plan": "Home 100",
                "status": "active",
                "activated_at": "2025-12-01",
                "tenure_months": 2.0,
                "order_count": 1,
                "total_paid": 5000.0,
                "avg_monthly_spend": 2500.0,
            },
        ],
    )
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_top_subscribers_by_tenure_proxy", lambda _db: [])
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_top_subscribers_by_estimated_plan_value", lambda _db: [])
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_top_subscribers_by_hybrid_score", lambda _db: [])

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/lifecycle",
            "headers": [],
            "query_string": b"sort_by=tenure_months",
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
        sort_by="tenure_months",
    )

    body = response.body.decode()
    assert response.status_code == 200
    assert "By Tenure" in body
    assert body.index("Lower Paid Longer") < body.index("Higher Paid Shorter")
    assert "Sorted by tenure, with total paid as tie-breaker." in body


def test_subscriber_lifecycle_sorts_top_subscribers_by_plan_type_when_requested(monkeypatch):
    monkeypatch.setattr(reports_web, "get_sidebar_stats", lambda _db: {"open_tickets": 0, "dispatch_jobs": 0})
    monkeypatch.setattr(
        subscriber_reports_service,
        "lifecycle_kpis",
        lambda _db, _start_dt, _end_dt: {
            "conversion_rate": 0,
            "avg_days_to_convert": 0,
            "churn_rate": 0,
            "terminated_in_period": 0,
            "avg_lifecycle_days": 0,
            "avg_lifecycle_months": 0,
            "upgrade_rate": 0,
            "downgrade_rate": 0,
            "engagement_score": 0,
            "pipeline_value": 0,
            "leads_won": 0,
        },
    )
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_funnel", lambda _db: [])
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_churn_trend", lambda _db: [])
    monkeypatch.setattr(
        subscriber_reports_service, "lifecycle_conversion_by_source", lambda _db, _start_dt, _end_dt: []
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "lifecycle_retention_cohorts",
        lambda _db, _start_dt, _end_dt: {"months": [], "rows": []},
    )
    monkeypatch.setattr(
        subscriber_reports_service, "lifecycle_time_to_convert_distribution", lambda _db, _start_dt, _end_dt: []
    )
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_plan_migration_flow", lambda _db, _start_dt, _end_dt: [])
    monkeypatch.setattr(subscriber_reports_service, "overview_plan_distribution", lambda _db, limit=8: [])
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_recent_churns", lambda _db: [])
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_longest_tenure", lambda _db: [])
    monkeypatch.setattr(
        subscriber_reports_service,
        "lifecycle_top_subscribers_by_value",
        lambda _db: [
            {
                "subscriber_id": "sub-1",
                "name": "Zulu Customer",
                "subscriber_number": "SUB-1",
                "plan": "Zulu Plan",
                "status": "active",
                "activated_at": "2024-01-01",
                "tenure_months": 20.0,
                "order_count": 1,
                "total_paid": 1000.0,
                "avg_monthly_spend": 50.0,
            },
            {
                "subscriber_id": "sub-2",
                "name": "Alpha Customer",
                "subscriber_number": "SUB-2",
                "plan": "Alpha Plan",
                "status": "active",
                "activated_at": "2025-12-01",
                "tenure_months": 2.0,
                "order_count": 1,
                "total_paid": 5000.0,
                "avg_monthly_spend": 2500.0,
            },
        ],
    )
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_top_subscribers_by_tenure_proxy", lambda _db: [])
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_top_subscribers_by_estimated_plan_value", lambda _db: [])
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_top_subscribers_by_hybrid_score", lambda _db: [])

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/lifecycle",
            "headers": [],
            "query_string": b"sort_by=plan_type",
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
        sort_by="plan_type",
    )

    body = response.body.decode()
    assert response.status_code == 200
    assert "Plan Type" in body
    assert body.index("Alpha Customer") < body.index("Zulu Customer")
    assert "Sorted alphabetically by plan type, with revenue and tenure as tie-breakers." in body


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


def test_lifecycle_recent_churns_include_behavioral_40_day_non_payment(db_session):
    now = datetime.now(UTC)

    person = Person(first_name="Recent", last_name="Churn", email=f"recent-churn-{uuid4().hex}@example.com")
    db_session.add(person)
    db_session.flush()

    db_session.add(
        Subscriber(
            person_id=person.id,
            subscriber_number="SUB-RECENT-001",
            status=SubscriberStatus.active,
            is_active=True,
            service_region="Central",
            created_at=now - timedelta(days=60),
            sync_metadata={"last_transaction_date": (now - timedelta(days=45)).strftime("%Y-%m-%d")},
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
            status=TicketStatus.closed,
            is_active=True,
            subscriber_id=subscriber.id,
            created_at=now - timedelta(days=5),
            resolved_at=now - timedelta(days=4),
            closed_at=now - timedelta(days=4),
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
            "avg_lifecycle_days": 90.0,
            "avg_lifecycle_months": 3.0,
            "upgrade_rate": 4.5,
            "downgrade_rate": 1.1,
            "engagement_score": 62.0,
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
        "lifecycle_retention_cohorts",
        lambda _db, _start_dt, _end_dt: {
            "months": ["2026-02", "2026-03"],
            "rows": [
                {
                    "cohort": "2026-02",
                    "size": 4,
                    "values": [{"retention_pct": 100, "retained": 4}, {"retention_pct": 75, "retained": 3}],
                }
            ],
        },
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "lifecycle_time_to_convert_distribution",
        lambda _db, _start_dt, _end_dt: [{"label": "0-7 days", "count": 2}],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "lifecycle_plan_migration_flow",
        lambda _db, _start_dt, _end_dt: [{"source": "Home 100", "target": "Home 200", "count": 2}],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "overview_plan_distribution",
        lambda _db, limit=8, subscriber_ids=None: [{"plan": "Home 200", "count": 10}],
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
    monkeypatch.setattr(
        subscriber_reports_service,
        "lifecycle_top_subscribers_by_value",
        lambda _db: [
            {
                "subscriber_id": "sub-3",
                "name": "Value Doe",
                "subscriber_number": "SUB-3",
                "plan": "Home 200",
                "status": "active",
                "activated_at": "2025-01-01",
                "tenure_months": 14.2,
                "order_count": 2,
                "total_paid": 25000.0,
                "avg_monthly_spend": 1760.56,
            }
        ],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "lifecycle_top_subscribers_by_tenure_proxy",
        lambda _db: [
            {
                "subscriber_number": "SUB-TENURE",
                "name": "Tenure Doe",
                "activated_at": "2017-09-19",
                "tenure_months": 102.0,
            }
        ],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "lifecycle_top_subscribers_by_estimated_plan_value",
        lambda _db: [
            {
                "subscriber_number": "SUB-PLAN",
                "name": "Plan Doe",
                "service_plan": "Business Fiber",
                "annualized_plan_estimate": 1080000.0,
            }
        ],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "lifecycle_top_subscribers_by_hybrid_score",
        lambda _db: [
            {
                "subscriber_number": "SUB-HYBRID",
                "name": "Hybrid Doe",
                "activated_at": "2018-01-01",
                "hybrid_score": 2500000.0,
            }
        ],
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
    assert "Avg Days To Convert" in body
    assert "Operational Churn" in body
    assert "Behavioral Churn (40d)" in body
    assert "Pipeline Value" in body
    assert "Cohort Retention" in body
    assert "Time To Convert Distribution" not in body
    assert "Plan Migration Flow" not in body
    assert "Top Subscribers By Value (All Time)" in body
    assert "Sorted by total paid across all subscriber histories." in body
    assert "Total Revenue" in body
    assert "By Tenure" in body
    assert "Plan Type" in body
    assert "Top By Tenure Proxy" not in body
    assert "Top By Estimated Annualized Value" not in body
    assert "Top By Hybrid Score" not in body
    assert "Recent Churn" in body


def test_subscriber_lifecycle_defaults_to_inception_when_days_is_zero(monkeypatch, db_session):
    first_person = Person(first_name="First", last_name="Lifecycle", email=f"first-life-{uuid4().hex}@example.com")
    db_session.add(first_person)
    db_session.flush()
    db_session.add(
        Subscriber(
            person_id=first_person.id,
            subscriber_number=f"SUB-{uuid4().hex[:8]}",
            status=SubscriberStatus.active,
            is_active=True,
            activated_at=datetime(2025, 1, 15, tzinfo=UTC),
        )
    )
    db_session.commit()

    captured: dict[str, datetime] = {}

    monkeypatch.setattr(reports_web, "get_sidebar_stats", lambda _db: {"open_tickets": 0, "dispatch_jobs": 0})

    def _capture_kpis(_db, start_dt, end_dt):
        captured["start_dt"] = start_dt
        captured["end_dt"] = end_dt
        return {
            "conversion_rate": 0,
            "churn_rate": 0,
            "terminated_in_period": 0,
            "avg_lifecycle_days": 0,
            "avg_lifecycle_months": 0,
            "upgrade_rate": 0,
            "downgrade_rate": 0,
            "engagement_score": 0,
        }

    monkeypatch.setattr(subscriber_reports_service, "lifecycle_kpis", _capture_kpis)
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_funnel", lambda _db: [])
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_churn_trend", lambda _db: [])
    monkeypatch.setattr(
        subscriber_reports_service, "lifecycle_conversion_by_source", lambda _db, _start_dt, _end_dt: []
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "lifecycle_retention_cohorts",
        lambda _db, _start_dt, _end_dt: {"months": [], "rows": []},
    )
    monkeypatch.setattr(
        subscriber_reports_service, "lifecycle_time_to_convert_distribution", lambda _db, _start_dt, _end_dt: []
    )
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_plan_migration_flow", lambda _db, _start_dt, _end_dt: [])
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_recent_churns", lambda _db: [])
    monkeypatch.setattr(subscriber_reports_service, "lifecycle_longest_tenure", lambda _db: [])

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/lifecycle",
            "headers": [],
            "query_string": b"days=0",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )

    response = reports_web.subscriber_lifecycle(
        request=request,
        db=db_session,
        days=0,
        start_date=None,
        end_date=None,
    )

    assert response.status_code == 200
    assert captured["start_dt"] == datetime(2025, 1, 15, tzinfo=UTC)


def test_get_churn_table_uses_splynx_status_due_date_and_balance(db_session):
    now = datetime.now(UTC)

    due_soon_person = Person(first_name="Due", last_name="Soon", email=f"duesoon-{uuid4().hex}@example.com")
    overdue_person = Person(first_name="Late", last_name="Payer", email=f"overdue-{uuid4().hex}@example.com")
    suspended_person = Person(first_name="Suspended", last_name="Account", email=f"suspended-{uuid4().hex}@example.com")
    current_person = Person(first_name="Current", last_name="Active", email=f"current-{uuid4().hex}@example.com")
    db_session.add_all([due_soon_person, overdue_person, suspended_person, current_person])
    overdue_person.phone = "+2348012345678"
    db_session.flush()

    due_soon_subscriber = Subscriber(
        person_id=due_soon_person.id,
        subscriber_number=f"SUB-{uuid4().hex[:8]}",
        status=SubscriberStatus.active,
        is_active=True,
        next_bill_date=now + timedelta(days=3),
        balance="50.00",
        billing_cycle="monthly",
    )
    overdue_subscriber = Subscriber(
        person_id=overdue_person.id,
        subscriber_number=f"SUB-{uuid4().hex[:8]}",
        status=SubscriberStatus.active,
        is_active=True,
        next_bill_date=now - timedelta(days=5),
        balance="250.00",
        billing_cycle="monthly",
        sync_metadata={
            "last_transaction_date": "2026-03-14",
            "expires_in": "2 days",
            "invoiced_until": (now - timedelta(days=10)).strftime("%Y-%m-%d"),
            "total_paid": "12345.67",
        },
    )
    suspended_subscriber = Subscriber(
        person_id=suspended_person.id,
        subscriber_number=f"SUB-{uuid4().hex[:8]}",
        status=SubscriberStatus.suspended,
        is_active=True,
        next_bill_date=now - timedelta(days=15),
        balance="120.00",
        billing_cycle="monthly",
    )
    current_subscriber = Subscriber(
        person_id=current_person.id,
        subscriber_number=f"SUB-{uuid4().hex[:8]}",
        status=SubscriberStatus.active,
        is_active=True,
        next_bill_date=now + timedelta(days=25),
        balance="10.00",
        billing_cycle="monthly",
    )
    db_session.add_all([due_soon_subscriber, overdue_subscriber, suspended_subscriber, current_subscriber])
    db_session.commit()

    rows = subscriber_reports_service.get_churn_table(db_session, due_soon_days=7, limit=20)

    assert [row["name"] for row in rows] == ["Late Payer", "Suspended Account", "Due Soon"]
    assert rows[0]["subscriber_id"] == str(overdue_subscriber.id)
    assert rows[0]["subscriber_status"] == "Active"
    assert rows[0]["phone"] == "+2348012345678"
    assert rows[0]["next_bill_date"] == (now - timedelta(days=5)).strftime("%Y-%m-%d")
    assert rows[0]["balance"] == 250.0
    assert rows[0]["billing_cycle"] == "monthly"
    assert rows[0]["last_transaction_date"] == "2026-03-14"
    assert rows[0]["expires_in"] == "2 days"
    assert rows[0]["invoiced_until"] == (now - timedelta(days=10)).strftime("%Y-%m-%d")
    assert rows[0]["days_since_last_payment"] == 10
    assert rows[0]["total_paid"] == 12345.67
    assert rows[0]["days_to_due"] <= -4
    assert rows[0]["risk_segment"] == "Overdue"
    assert rows[0]["is_high_balance_risk"] is True

    assert rows[1]["subscriber_id"] == str(suspended_subscriber.id)
    assert rows[1]["subscriber_status"] == "Suspended"
    assert rows[1]["risk_segment"] == "Suspended"
    assert rows[1]["is_high_balance_risk"] is False

    assert rows[2]["subscriber_id"] == str(due_soon_subscriber.id)
    assert rows[2]["risk_segment"] == "Due Soon"
    assert rows[2]["is_high_balance_risk"] is False

    churned_only = subscriber_reports_service.get_churn_table(
        db_session,
        due_soon_days=7,
        segment="overdue",
        limit=20,
    )
    assert [row["name"] for row in churned_only] == ["Late Payer"]

    overdue_and_suspended = subscriber_reports_service.get_churn_table(
        db_session,
        due_soon_days=7,
        segments=["overdue", "suspended"],
        limit=20,
    )
    assert [row["name"] for row in overdue_and_suspended] == ["Late Payer", "Suspended Account"]

    overdue_and_suspended_csv_style = subscriber_reports_service.get_churn_table(
        db_session,
        due_soon_days=7,
        segments=["overdue,suspended"],
        limit=20,
    )
    assert [row["name"] for row in overdue_and_suspended_csv_style] == ["Late Payer", "Suspended Account"]

    high_balance_only = subscriber_reports_service.get_churn_table(
        db_session,
        due_soon_days=7,
        high_balance_only=True,
        limit=20,
    )
    assert [row["name"] for row in high_balance_only] == ["Late Payer"]


def test_get_churn_table_splynx_live_uses_crm_contact_phone_fallback(db_session, monkeypatch):
    from app.services import splynx as splynx_service

    now = datetime.now(UTC)
    person = Person(
        first_name="Contact",
        last_name="Phone",
        email=f"live-phone-{uuid4().hex}@example.com",
        phone=None,
    )
    db_session.add(person)
    db_session.flush()
    db_session.add(
        PersonChannel(
            person_id=person.id,
            channel_type=ChannelType.whatsapp,
            address="+2348099991111",
            is_primary=True,
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        splynx_service,
        "fetch_customers",
        lambda _db: [
            {
                "id": "12345",
                "name": "Live Splynx Customer",
                "email": person.email,
                "phone": "",
                "city": "Abuja",
                "mrr_total": "42000.00",
                "status": "blocked",
                "blocking_date": "2024-03-01",
            }
        ],
    )
    monkeypatch.setattr(
        splynx_service,
        "map_customer_to_subscriber_data",
        lambda _db, _customer, include_remote_details=True: {
            "status": SubscriberStatus.suspended.value,
            "next_bill_date": now - timedelta(days=3),
            "balance": "150.00",
            "sync_metadata": {"invoiced_until": (now - timedelta(days=7)).strftime("%Y-%m-%d")},
        },
    )
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer_internet_services",
        lambda _db, _customer_id: [{"id": 1, "status": "active", "description": "Home Fiber 50Mbps"}],
    )
    monkeypatch.setattr(splynx_service, "fetch_customer_billing", lambda _db, _customer_id: {})

    rows = subscriber_reports_service.get_churn_table(
        db_session,
        due_soon_days=7,
        source="splynx_live",
        limit=20,
    )

    assert len(rows) == 1
    assert rows[0]["name"] == "Live Splynx Customer"
    assert rows[0]["phone"] == "+2348099991111"
    assert rows[0]["plan"] == "Home Fiber 50Mbps"
    assert rows[0]["city"] == "Abuja"
    assert rows[0]["mrr_total"] == 42000.0
    assert rows[0]["risk_segment"] == "Suspended"


def test_get_churn_table_splynx_live_formats_multiple_phone_numbers(db_session, monkeypatch):
    from app.services import splynx as splynx_service

    now = datetime.now(UTC)

    monkeypatch.setattr(
        splynx_service,
        "fetch_customers",
        lambda _db: [
            {
                "id": "12345",
                "name": "Multi Phone Customer",
                "email": "",
                "phone": "08091120830/08037052795",
                "status": "blocked",
                "blocking_date": "2024-03-01",
            }
        ],
    )
    monkeypatch.setattr(
        splynx_service,
        "map_customer_to_subscriber_data",
        lambda _db, _customer, include_remote_details=True: {
            "status": SubscriberStatus.suspended.value,
            "next_bill_date": now - timedelta(days=3),
            "balance": "150.00",
            "sync_metadata": {},
        },
    )
    monkeypatch.setattr(splynx_service, "fetch_customer_billing", lambda _db, _customer_id: {})

    rows = subscriber_reports_service.get_churn_table(
        db_session,
        due_soon_days=7,
        source="splynx_live",
        limit=20,
    )

    assert len(rows) == 1
    assert rows[0]["phone"] == "+2348091120830, +2348037052795"


def test_get_churn_table_splynx_live_uses_short_lived_sessions_for_remote_calls(db_session, monkeypatch):
    from app.services import splynx as splynx_service

    now = datetime.now(UTC)
    session_factory_calls = 0
    session_close_calls = 0

    class _FakeSession:
        def close(self):
            nonlocal session_close_calls
            session_close_calls += 1

    def _fake_session_factory():
        nonlocal session_factory_calls
        session_factory_calls += 1
        return _FakeSession()

    monkeypatch.setattr(subscriber_reports_service, "SessionLocal", _fake_session_factory)
    monkeypatch.setattr(
        splynx_service,
        "fetch_customers",
        lambda _db: [
            {
                "id": "12345",
                "name": "Blocked Customer",
                "email": "",
                "phone": "",
                "status": "blocked",
                "nas_name": "Maitama Access",
                "date_add": "2024-01-15",
                "blocking_date": "2024-03-02",
            }
        ],
    )
    monkeypatch.setattr(
        splynx_service,
        "map_customer_to_subscriber_data",
        lambda _db, _customer, include_remote_details=False: {
            "status": SubscriberStatus.suspended.value,
            "next_bill_date": now - timedelta(days=2),
            "balance": "50.00",
            "sync_metadata": {"invoiced_until": (now - timedelta(days=5)).strftime("%Y-%m-%d")},
        },
    )
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer_billing",
        lambda _db, _customer_id: {"last_transaction_date": "2024-03-20"},
    )

    rows = subscriber_reports_service.get_churn_table(
        db_session,
        due_soon_days=7,
        source="splynx_live",
        limit=20,
    )

    assert len(rows) == 1
    assert rows[0]["area"] == "Maitama"
    assert rows[0]["billing_start_date"] == "2024-01-15"
    assert rows[0]["last_transaction_date"] == "2024-03-20"
    assert session_factory_calls == 2
    assert session_close_calls == session_factory_calls


def test_get_churn_table_splynx_live_falls_back_to_local_blocked_date(db_session, monkeypatch):
    from app.services import splynx as splynx_service

    person = Person(first_name="Invoice", last_name="Fallback", email=f"invoice-{uuid4().hex}@example.com")
    db_session.add(person)
    db_session.flush()
    db_session.add(
        Subscriber(
            person_id=person.id,
            external_id="12345",
            external_system="splynx",
            subscriber_number="LIVE-12345",
            status=SubscriberStatus.suspended,
            is_active=True,
            suspended_at=datetime(2026, 3, 31, tzinfo=UTC),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        splynx_service,
        "fetch_customers",
        lambda _db: [
            {
                "id": "12345",
                "name": "Invoice Fallback",
                "email": person.email,
                "phone": "",
                "status": "blocked",
                "login": "LIVE-12345",
            }
        ],
    )
    monkeypatch.setattr(
        splynx_service,
        "map_customer_to_subscriber_data",
        lambda _db, _customer, include_remote_details=False: {
            "status": SubscriberStatus.suspended.value,
            "balance": "50.00",
            "sync_metadata": {},
        },
    )
    monkeypatch.setattr(splynx_service, "fetch_customer_internet_services", lambda _db, _customer_id: [])
    monkeypatch.setattr(splynx_service, "fetch_customer_billing", lambda _db, _customer_id: {})

    rows = subscriber_reports_service.get_churn_table(
        db_session,
        due_soon_days=7,
        source="splynx_live",
        limit=20,
    )

    assert len(rows) == 1
    assert rows[0]["blocked_date"] == "2026-03-31"


def test_get_churn_table_splynx_live_uses_customer_blocking_date(db_session, monkeypatch):
    from app.services import splynx as splynx_service

    monkeypatch.setattr(
        splynx_service,
        "fetch_customers",
        lambda _db: [
            {
                "id": "12345",
                "name": "Billing Date Fallback",
                "email": "",
                "phone": "",
                "status": "blocked",
                "blocking_date": "2024-02-20",
            }
        ],
    )
    monkeypatch.setattr(
        splynx_service,
        "map_customer_to_subscriber_data",
        lambda _db, _customer, include_remote_details=False: {
            "status": SubscriberStatus.suspended.value,
            "balance": "50.00",
            "sync_metadata": {},
        },
    )
    monkeypatch.setattr(splynx_service, "fetch_customer_internet_services", lambda _db, _customer_id: [])
    monkeypatch.setattr(splynx_service, "fetch_customer_billing", lambda _db, _customer_id: {})

    rows = subscriber_reports_service.get_churn_table(
        db_session,
        due_soon_days=7,
        source="splynx_live",
        limit=20,
    )

    assert len(rows) == 1
    assert rows[0]["blocked_date"] == "2024-02-20"


def test_get_churn_table_splynx_live_uses_mapped_service_plan_only(db_session, monkeypatch):
    from app.services import splynx as splynx_service

    monkeypatch.setattr(
        splynx_service,
        "fetch_customers",
        lambda _db: [
            {
                "id": "12345",
                "name": "Plan Customer",
                "email": "",
                "phone": "",
                "status": "blocked",
                "tariff_name": "Raw Tariff Name",
                "plan_name": "Raw Plan Name",
                "package": "Raw Package Name",
            }
        ],
    )
    monkeypatch.setattr(
        splynx_service,
        "map_customer_to_subscriber_data",
        lambda _db, _customer, include_remote_details=False: {
            "status": SubscriberStatus.suspended.value,
            "balance": "50.00",
            "service_plan": "Mapped Service Plan",
            "sync_metadata": {},
        },
    )
    monkeypatch.setattr(splynx_service, "fetch_customer_billing", lambda _db, _customer_id: {})

    rows = subscriber_reports_service.get_churn_table(
        db_session,
        due_soon_days=7,
        source="splynx_live",
        limit=20,
    )

    assert len(rows) == 1
    assert rows[0]["plan"] == "Mapped Service Plan"


def test_get_churn_table_splynx_live_uses_live_billing_last_transaction_date(db_session, monkeypatch):
    from app.services import splynx as splynx_service

    monkeypatch.setattr(
        splynx_service,
        "fetch_customers",
        lambda _db: [
            {
                "id": "12345",
                "name": "Billing History Customer",
                "email": "",
                "phone": "",
                "status": "blocked",
            }
        ],
    )
    monkeypatch.setattr(
        splynx_service,
        "map_customer_to_subscriber_data",
        lambda _db, _customer, include_remote_details=False: {
            "status": SubscriberStatus.suspended.value,
            "balance": "50.00",
            "sync_metadata": {"last_transaction_date": "2024-01-01"},
        },
    )
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer_billing",
        lambda _db, _customer_id: {"last_transaction_date": "2024-04-18"},
    )
    monkeypatch.setattr(splynx_service, "fetch_customer_internet_services", lambda _db, _customer_id: [])

    rows = subscriber_reports_service.get_churn_table(
        db_session,
        due_soon_days=7,
        source="splynx_live",
        limit=20,
    )

    assert len(rows) == 1
    assert rows[0]["last_transaction_date"] == "2024-04-18"


def test_get_churn_table_splynx_live_uses_live_billing_blocked_date_for_non_suspended_rows(db_session, monkeypatch):
    from app.services import splynx as splynx_service

    monkeypatch.setattr(
        splynx_service,
        "fetch_customers",
        lambda _db: [
            {
                "id": "12345",
                "name": "Overdue Customer",
                "email": "",
                "phone": "",
                "status": "active",
            }
        ],
    )
    monkeypatch.setattr(
        splynx_service,
        "map_customer_to_subscriber_data",
        lambda _db, _customer, include_remote_details=False: {
            "status": SubscriberStatus.active.value,
            "next_bill_date": datetime.now(UTC) - timedelta(days=3),
            "balance": "50.00",
            "sync_metadata": {"last_transaction_date": "2024-01-01"},
        },
    )
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer_billing",
        lambda _db, _customer_id: {"blocking_date": "2024-04-11"},
    )

    rows = subscriber_reports_service.get_churn_table(
        db_session,
        due_soon_days=7,
        source="splynx_live",
        limit=20,
    )

    assert len(rows) == 1
    assert rows[0]["risk_segment"] == "Overdue"
    assert rows[0]["blocked_date"] == "2024-04-11"
    assert rows[0]["last_transaction_date"] == "2024-01-01"


def test_get_live_blocked_dates_fetches_billing_blocking_date(monkeypatch):
    from app.services import splynx as splynx_service

    session_factory_calls = 0
    session_close_calls = 0

    class _FakeSession:
        def close(self):
            nonlocal session_close_calls
            session_close_calls += 1

    def _fake_session_factory():
        nonlocal session_factory_calls
        session_factory_calls += 1
        return _FakeSession()

    monkeypatch.setattr(subscriber_reports_service, "SessionLocal", _fake_session_factory)

    def _fake_fetch_customer_billing(_db, customer_id):
        if str(customer_id) == "12345":
            return {"blocking_date": "2024-04-18"}
        return {"blocking_date": "2024-04-01"}

    monkeypatch.setattr(splynx_service, "fetch_customer_billing", _fake_fetch_customer_billing)

    blocked_dates = subscriber_reports_service.get_live_blocked_dates(["12345", "99999", "12345"])

    assert blocked_dates == {"12345": "2024-04-18", "99999": "2024-04-01"}
    assert session_factory_calls == 2
    assert session_close_calls == 2


def test_get_churn_table_splynx_live_reuses_cached_remote_payloads(db_session, monkeypatch):
    from app.services import splynx as splynx_service

    subscriber_reports_service._clear_live_splynx_cache()
    calls = {"customers": 0, "billing": 0, "services": 0}

    def _fetch_customers(_db):
        calls["customers"] += 1
        return [
            {
                "id": "12345",
                "name": "Cached Customer",
                "email": "",
                "phone": "",
                "status": "blocked",
            }
        ]

    def _map_customer(_db, _customer, include_remote_details=False):
        return {
            "status": SubscriberStatus.suspended.value,
            "balance": "50.00",
            "service_plan": "",
            "sync_metadata": {},
        }

    def _fetch_customer_billing(_db, _customer_id):
        calls["billing"] += 1
        return {"blocking_date": "2024-04-11"}

    def _fetch_customer_services(_db, _customer_id):
        calls["services"] += 1
        return [{"description": "Business Fiber"}]

    monkeypatch.setattr(splynx_service, "fetch_customers", _fetch_customers)
    monkeypatch.setattr(splynx_service, "map_customer_to_subscriber_data", _map_customer)
    monkeypatch.setattr(splynx_service, "fetch_customer_billing", _fetch_customer_billing)
    monkeypatch.setattr(splynx_service, "fetch_customer_internet_services", _fetch_customer_services)

    rows_first = subscriber_reports_service.get_churn_table(
        db_session,
        due_soon_days=7,
        source="splynx_live",
        limit=20,
    )
    rows_second = subscriber_reports_service.get_churn_table(
        db_session,
        due_soon_days=7,
        source="splynx_live",
        limit=20,
    )

    assert rows_first[0]["plan"] == "Business Fiber"
    assert rows_second[0]["plan"] == "Business Fiber"
    assert calls == {"customers": 1, "billing": 1, "services": 1}
    subscriber_reports_service._clear_live_splynx_cache()


def test_get_churn_table_splynx_live_falls_back_to_request_auto_next_for_blocked_date(db_session, monkeypatch):
    from app.services import splynx as splynx_service

    subscriber_reports_service._clear_live_splynx_cache()
    monkeypatch.setattr(
        splynx_service,
        "fetch_customers",
        lambda _db: [
            {
                "id": "12345",
                "name": "Fallback Blocked Date Customer",
                "email": "",
                "phone": "",
                "status": "blocked",
            }
        ],
    )
    monkeypatch.setattr(
        splynx_service,
        "map_customer_to_subscriber_data",
        lambda _db, _customer, include_remote_details=False: {
            "status": SubscriberStatus.suspended.value,
            "balance": "50.00",
            "sync_metadata": {},
        },
    )
    monkeypatch.setattr(splynx_service, "fetch_customer_internet_services", lambda _db, _customer_id: [])
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer_billing",
        lambda _db, _customer_id: {"blocking_date": "0000-00-00", "request_auto_next": "2024-04-22"},
    )

    rows = subscriber_reports_service.get_churn_table(
        db_session,
        due_soon_days=7,
        source="splynx_live",
        limit=20,
    )

    assert len(rows) == 1
    assert rows[0]["blocked_date"] == "2024-04-22"


def test_get_churn_table_splynx_live_falls_back_to_last_update_for_blocked_date(db_session, monkeypatch):
    from app.services import splynx as splynx_service

    subscriber_reports_service._clear_live_splynx_cache()
    monkeypatch.setattr(
        splynx_service,
        "fetch_customers",
        lambda _db: [
            {
                "id": "12345",
                "name": "Last Update Blocked Date Customer",
                "email": "",
                "phone": "",
                "status": "blocked",
                "last_update": "2024-04-25",
            }
        ],
    )
    monkeypatch.setattr(
        splynx_service,
        "map_customer_to_subscriber_data",
        lambda _db, _customer, include_remote_details=False: {
            "status": SubscriberStatus.suspended.value,
            "balance": "50.00",
            "sync_metadata": {},
        },
    )
    monkeypatch.setattr(splynx_service, "fetch_customer_internet_services", lambda _db, _customer_id: [])
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer_billing",
        lambda _db, _customer_id: {"blocking_date": "0000-00-00", "request_auto_next": ""},
    )

    rows = subscriber_reports_service.get_churn_table(
        db_session,
        due_soon_days=7,
        source="splynx_live",
        limit=20,
    )

    assert len(rows) == 1
    assert rows[0]["blocked_date"] == "2024-04-25"


def test_get_overdue_invoices_table_returns_30_day_past_due_customers(db_session):
    now = datetime.now(UTC)
    person = Person(first_name="Overdue", last_name="Customer", email=f"overdue-{uuid4().hex}@example.com")
    db_session.add(person)
    db_session.flush()

    overdue_order = SalesOrder(
        person_id=person.id,
        status=SalesOrderStatus.confirmed,
        payment_status=SalesOrderPaymentStatus.pending,
        total=Decimal("5000.00"),
        amount_paid=Decimal("0.00"),
        balance_due=Decimal("5000.00"),
        payment_due_date=now - timedelta(days=45),
    )
    not_overdue_order = SalesOrder(
        person_id=person.id,
        status=SalesOrderStatus.confirmed,
        payment_status=SalesOrderPaymentStatus.pending,
        total=Decimal("1000.00"),
        amount_paid=Decimal("0.00"),
        balance_due=Decimal("1000.00"),
        payment_due_date=now - timedelta(days=10),
    )
    db_session.add_all([overdue_order, not_overdue_order])
    db_session.commit()

    rows = subscriber_reports_service.get_overdue_invoices_table(db_session, min_days_past_due=30, limit=50)
    assert len(rows) == 1
    assert rows[0]["name"] == "Overdue Customer"
    assert rows[0]["overdue_invoices"] == 1
    assert rows[0]["total_balance_due"] == 5000.0
    assert rows[0]["max_days_past_due"] >= 30


def test_churned_subscribers_page_renders(monkeypatch):
    monkeypatch.setattr(
        reports_web,
        "_resolve_lifecycle_date_range",
        lambda _db, _days, _start, _end: (
            datetime(2026, 3, 1, tzinfo=UTC),
            datetime(2026, 3, 31, 23, 59, 59, tzinfo=UTC),
        ),
    )
    monkeypatch.setattr(reports_web, "get_sidebar_stats", lambda _db: {"open_tickets": 0, "dispatch_jobs": 0})
    monkeypatch.setattr(
        subscriber_reports_service,
        "churned_subscribers_kpis",
        lambda _db, _start, _end, behavioral_days=60: {
            "churned_count": 4,
            "churn_rate": 2.7,
            "revenue_lost_to_churn": 180000.0,
            "avg_lifetime_before_churn_days": 312,
            "impacted_plans": 2,
            "impacted_regions": 1,
        },
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "churned_subscribers_trend",
        lambda _db, _start, _end, behavioral_days=60: [
            {"date": "2026-03-28", "count": 1},
            {"date": "2026-03-29", "count": 2},
        ],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "churned_subscribers_rows",
        lambda _db, _start, _end, limit=100, behavioral_days=60: [
            {
                "name": "Former Customer",
                "subscriber_number": "SUB-9",
                "plan": "Premium",
                "region": "Ikeja",
                "activated_at": "2025-05-21",
                "terminated_at": "2026-03-28",
                "tenure_days": 312,
            }
        ],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "churned_failed_payment_rows",
        lambda _db, _start, _end, limit=50, behavioral_days=60: [
            {
                "name": "Payment Risk",
                "subscriber_number": "SUB-10",
                "plan": "Standard",
                "outstanding_balance": 6400.0,
                "due_date": "2026-03-15",
            }
        ],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "churned_cancelled_rows",
        lambda _db, _start, _end, limit=50: [
            {
                "name": "Cancelled User",
                "subscriber_number": "SUB-11",
                "plan": "Premium",
                "region": "Abuja",
                "terminated_at": "2026-03-27",
            }
        ],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "churned_inactive_usage_rows",
        lambda _db, _end, limit=50: [
            {
                "name": "Dormant User",
                "subscriber_number": "SUB-12",
                "plan": "SME",
                "status": "suspended",
                "last_usage_at": "2025-12-20",
                "days_since_use": 100,
                "total_paid": 50000.0,
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
        days=30,
        start_date=None,
        end_date=None,
    )

    assert response.status_code == 200
    body = response.body.decode()
    assert "Churned Subscribers" in body
    assert "Churn Rate" in body
    assert "Revenue Lost" in body
    assert "Churn Trend" in body
    assert "Behavioral Churn: Failed Payment" in body
    assert "Operational Churn: Explicit Cancellation" in body
    assert "At-Risk Inactive Usage (90+ days no activity)" in body
    assert "Outstanding" in body
    assert "Former Customer" in body
    assert "Payment Risk" in body
    assert "Cancelled User" in body
    assert "Dormant User" in body


def test_churn_risk_summary_rolls_up_balances_and_recent_churn():
    summary = subscriber_reports_service.churn_risk_summary(
        [
            {"balance": 1000.0, "risk_segment": "Overdue", "is_high_balance_risk": True},
            {"balance": 500.0, "risk_segment": "Due Soon", "is_high_balance_risk": False},
            {"balance": 250.0, "risk_segment": "Overdue", "is_high_balance_risk": False},
        ],
        [{"total_balance_due": 3000.0}],
        {"churned_count": 2, "churn_rate": 1.5, "revenue_lost_to_churn": 12000.0},
    )

    assert summary["total_at_risk"] == 3
    assert summary["total_balance_exposure"] == 1750.0
    assert summary["high_balance_risk_count"] == 1
    assert summary["overdue_count"] == 2
    assert summary["overdue_balance_exposure"] == 1250.0
    assert summary["overdue_invoice_balance"] == 3000.0
    assert summary["recent_churned_count"] == 2
    assert summary["recent_churn_rate"] == 1.5


def test_churn_risk_segment_breakdown_groups_and_orders_rows():
    rows = subscriber_reports_service.churn_risk_segment_breakdown(
        [
            {
                "risk_segment": "Due Soon",
                "balance": 100.0,
                "is_high_balance_risk": False,
                "billing_cycle": "monthly",
                "invoiced_until": "2026-03-20",
            },
            {
                "risk_segment": "Overdue",
                "balance": 500.0,
                "is_high_balance_risk": True,
                "billing_cycle": "monthly",
                "days_since_last_payment": 12,
            },
            {
                "risk_segment": "Overdue",
                "balance": 300.0,
                "is_high_balance_risk": False,
                "billing_cycle": "quarterly",
                "days_since_last_payment": 8,
            },
        ]
    )

    assert [row["segment"] for row in rows] == ["Overdue", "Due Soon"]
    assert rows[0]["count"] == 2
    assert rows[0]["balance"] == 800.0
    assert rows[0]["high_balance_count"] == 1
    assert rows[0]["avg_balance"] == 400.0
    assert "Avg 10d since payment (2 accounts)" in rows[0]["billing_mix"]
    assert "Monthly (1), Quarterly (1)" in rows[0]["billing_mix"]
    assert "Avg" in rows[1]["billing_mix"]
    assert "Monthly (1)" in rows[1]["billing_mix"]


def test_churn_risk_aging_buckets_categorizes_blocked_date_age():
    today = datetime.now(UTC).date()
    rows = subscriber_reports_service.churn_risk_aging_buckets(
        [
            {"blocked_date": (today - timedelta(days=3)).strftime("%Y-%m-%d")},
            {"blocked_date": (today - timedelta(days=15)).strftime("%Y-%m-%d")},
            {"blocked_date": (today - timedelta(days=45)).strftime("%Y-%m-%d")},
            {"blocked_date": (today - timedelta(days=90)).strftime("%Y-%m-%d")},
            {"blocked_date": ""},
        ],
        due_soon_days=7,
    )

    bucket_map = {row["label"]: row["count"] for row in rows}
    assert bucket_map["Blocked 0-7 Days"] == 1
    assert bucket_map["Blocked 8-30 Days"] == 1
    assert bucket_map["Blocked 31-60 Days"] == 1
    assert bucket_map["Blocked 61+ Days"] == 1
    assert bucket_map["No Blocked Date"] == 1


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


def test_subscriber_billing_risk_page_renders(monkeypatch):
    monkeypatch.setattr(reports_web, "get_sidebar_stats", lambda _db: {"open_tickets": 0, "dispatch_jobs": 0})
    monkeypatch.setattr(
        subscriber_reports_service,
        "get_churn_table",
        lambda _db,
        due_soon_days=7,
        high_balance_only=False,
        segment=None,
        segments=None,
        days_past_due=None,
        source="local",
        limit=500,
        page=1,
        page_size=None,
        search=None,
        overdue_bucket=None,
        enrich_visible_rows=True: [
            {
                "name": "Blocked Customer",
                "email": "blocked@example.com",
                "subscriber_status": "suspended",
                "risk_segment": "Suspended",
                "next_bill_date": "2026-04-12",
                "days_to_due": 5,
                "balance": 9200.0,
                "billing_cycle": "monthly",
                "last_transaction_date": "2026-03-01",
                "expires_in": 12,
                "invoiced_until": "2026-04-30",
                "total_paid": 50000.0,
                "is_high_balance_risk": True,
            }
        ],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "get_overdue_invoices_table",
        lambda _db, min_days_past_due=30, limit=250: [
            {
                "name": "Blocked Customer",
                "overdue_invoices": 2,
                "total_balance_due": 9200.0,
                "max_days_past_due": 44,
                "oldest_due_day": "2026-02-23",
            }
        ],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "churn_risk_summary",
        lambda _churn_rows, _overdue_invoices: {
            "total_at_risk": 1,
            "total_balance_exposure": 9200.0,
            "high_balance_risk_count": 1,
            "overdue_invoice_balance": 9200.0,
        },
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "churn_risk_segment_breakdown",
        lambda _churn_rows: [
            {
                "segment": "Suspended",
                "count": 1,
                "share_pct": 100.0,
                "balance": 9200.0,
                "high_balance_count": 1,
            }
        ],
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "churn_risk_aging_buckets",
        lambda _churn_rows, due_soon_days=7: [{"label": "Due In 0-7 Days", "count": 1}],
    )
    monkeypatch.setattr(reports_web, "_latest_subscriber_sync_at", lambda _db: None)
    monkeypatch.setattr(reports_web, "get_csrf_token", lambda _request: "test-csrf-token")

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/billing-risk",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )

    response = reports_web.subscriber_billing_risk(
        request=request,
        db=None,
        due_soon_days=7,
        overdue_invoice_days=30,
        high_balance_only=False,
        segment=None,
    )

    assert response.status_code == 200
    body = response.body.decode()
    assert "Subscriber Billing Risk" in body
    assert "At-Risk Subscribers" in body
    assert "Blocked Date" in body
    assert "Blocked Customer" in body


def test_subscriber_billing_risk_export_returns_csv(monkeypatch):
    monkeypatch.setattr(
        subscriber_reports_service,
        "get_churn_table",
        lambda _db,
        due_soon_days=7,
        high_balance_only=False,
        segment=None,
        segments=None,
        days_past_due=None,
        source="local",
        limit=2000,
        page=1,
        page_size=None,
        search=None,
        overdue_bucket=None,
        enrich_visible_rows=True: [
            {
                "name": "Blocked Customer",
                "email": "blocked@example.com",
                "subscriber_status": "suspended",
                "risk_segment": "Suspended",
                "next_bill_date": "2026-04-12",
                "days_to_due": 5,
                "balance": 9200.0,
                "billing_cycle": "monthly",
                "last_transaction_date": "2026-03-01",
                "expires_in": 12,
                "invoiced_until": "2026-04-30",
                "total_paid": 50000.0,
                "is_high_balance_risk": True,
            }
        ],
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/billing-risk/export",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )

    response = reports_web.subscriber_billing_risk_export(
        request=request,
        db=None,
        due_soon_days=7,
        high_balance_only=False,
        segment=None,
    )

    assert response.status_code == 200
    assert response.media_type == "text/csv"
    assert "attachment; filename=subscriber_billing_risk_" in response.headers["Content-Disposition"]


def test_subscriber_billing_risk_blocked_dates_returns_json(monkeypatch):
    monkeypatch.setattr(reports_web, "get_current_user", lambda _request: {"id": "test-user"})
    monkeypatch.setattr(
        subscriber_reports_service,
        "get_live_blocked_dates",
        lambda external_ids: {"12345": "2024-04-18"} if external_ids == ["12345"] else {},
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/billing-risk/blocked-dates",
            "headers": [],
            "query_string": b"external_id=12345",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )

    response = reports_web.subscriber_billing_risk_blocked_dates(
        request=request,
        external_id=["12345"],
    )

    assert response.status_code == 200
    assert response.body == b'{"blocked_dates":{"12345":"2024-04-18"}}'


def test_subscriber_billing_risk_rows_returns_html(monkeypatch):
    monkeypatch.setattr(reports_web, "get_current_user", lambda _request: {"id": "test-user"})
    monkeypatch.setattr(
        reports_web,
        "_billing_risk_page_rows",
        lambda *_args, **_kwargs: (
            [
                {
                    "name": "Blocked Customer",
                    "phone": "+2348099991111",
                    "city": "Abuja",
                    "area": "Maitama",
                    "plan": "Home Fiber 50Mbps",
                    "mrr_total": 42000.0,
                    "subscriber_status": "Suspended",
                    "risk_segment": "Suspended",
                    "billing_start_date": "2024-01-15",
                    "last_transaction_date": "2024-03-01",
                    "blocked_date": "2024-04-18",
                    "balance": 9200.0,
                }
            ],
            {"total_count": 1, "total_balance": 9200.0, "avg_days_overdue": 48},
            True,
        ),
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/billing-risk/rows",
            "headers": [],
            "query_string": b"page=1&page_size=50&bucket=all",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )

    response = reports_web.subscriber_billing_risk_rows(
        request=request,
        db=None,
        page=1,
        page_size=50,
        bucket="all",
    )

    assert response.status_code == 200
    body = response.body.decode()
    assert "Blocked Customer" in body
    assert "Page 1" in body
    assert "Total Blocked Customers" in body


def test_subscriber_billing_risk_blocked_date_cell_returns_html(monkeypatch):
    monkeypatch.setattr(reports_web, "get_current_user", lambda _request: {"id": "test-user"})
    monkeypatch.setattr(
        subscriber_reports_service,
        "get_live_blocked_dates",
        lambda external_ids: {"12345": "2024-04-18"} if external_ids == ["12345"] else {},
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/billing-risk/blocked-date-cell",
            "headers": [],
            "query_string": b"external_id=12345",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )

    response = reports_web.subscriber_billing_risk_blocked_date_cell(
        request=request,
        external_id="12345",
    )

    assert response.status_code == 200
    assert response.body == b"2024-04-18"
