"""Proximity-aware nearest-tech ranking and day routing (task #47)."""

import uuid

from app.models.person import Person
from app.models.workforce import WorkOrderStatus
from app.schemas.workforce import WorkOrderCreate
from app.services import workforce as workforce_service
from app.services.field import routing
from app.services.field.location_tracking import field_location_tracking as loc_svc

# A job in central Lagos.
JOB_LAT, JOB_LNG = 6.5244, 3.3792


def _make_person(db, name):
    person = Person(first_name=name, last_name="Tech", email=f"{name.lower()}-{uuid.uuid4().hex[:6]}@example.com")
    db.add(person)
    db.commit()
    db.refresh(person)
    return person


def _place_tech(db, person, lat, lng, *, sharing=True, status="on_shift"):
    loc_svc.set_sharing(db, str(person.id), enabled=sharing, status=status)
    loc_svc.record_ping(db, str(person.id), latitude=lat, longitude=lng)


def _locate_job(db, work_order, lat=JOB_LAT, lng=JOB_LNG):
    work_order.metadata_ = {"resolved_location": {"latitude": lat, "longitude": lng, "address_text": "site"}}
    db.commit()
    db.refresh(work_order)


def test_nearest_techs_ranked_by_distance(db_session, work_order):
    _locate_job(db_session, work_order)
    near = _make_person(db_session, "Near")
    far = _make_person(db_session, "Far")
    _place_tech(db_session, near, JOB_LAT + 0.001, JOB_LNG)  # ~111 m
    _place_tech(db_session, far, JOB_LAT + 0.05, JOB_LNG)  # ~5.5 km

    ranked = routing.nearest_techs_for_job(db_session, str(work_order.id), limit=5)
    ids = [r["person_id"] for r in ranked]
    assert ids[0] == str(near.id)
    assert ids[1] == str(far.id)
    assert ranked[0]["distance_km"] < ranked[1]["distance_km"]


def test_excludes_non_sharing_off_shift(db_session, work_order):
    _locate_job(db_session, work_order)
    sharing_off = _make_person(db_session, "Hidden")
    off_shift = _make_person(db_session, "Resting")
    _place_tech(db_session, sharing_off, JOB_LAT, JOB_LNG, sharing=False)
    _place_tech(db_session, off_shift, JOB_LAT, JOB_LNG, status="on_break")

    ranked = routing.nearest_techs_for_job(db_session, str(work_order.id))
    assert ranked == []


def test_max_km_filter(db_session, work_order):
    _locate_job(db_session, work_order)
    far = _make_person(db_session, "Distant")
    _place_tech(db_session, far, JOB_LAT + 0.05, JOB_LNG)  # ~5.5 km

    assert routing.nearest_techs_for_job(db_session, str(work_order.id), max_km=1.0) == []
    assert len(routing.nearest_techs_for_job(db_session, str(work_order.id), max_km=10.0)) == 1


def test_candidate_restriction(db_session, work_order):
    _locate_job(db_session, work_order)
    a = _make_person(db_session, "Aaa")
    b = _make_person(db_session, "Bbb")
    _place_tech(db_session, a, JOB_LAT + 0.001, JOB_LNG)
    _place_tech(db_session, b, JOB_LAT + 0.002, JOB_LNG)

    ranked = routing.nearest_techs_for_job(db_session, str(work_order.id), candidate_person_ids=[str(b.id)])
    assert [r["person_id"] for r in ranked] == [str(b.id)]


def test_suggest_returns_none_without_location(db_session, work_order):
    # Job has no resolved location → no suggestion.
    assert routing.suggest_nearest_tech(db_session, str(work_order.id)) is None


def test_day_route_orders_by_nearest_neighbour(db_session, person, project, ticket):
    # Three jobs east of the start; greedy should visit them west→east.
    coords = [(JOB_LAT, JOB_LNG + 0.03), (JOB_LAT, JOB_LNG + 0.01), (JOB_LAT, JOB_LNG + 0.02)]
    work_orders = []
    for i, (lat, lng) in enumerate(coords):
        wo = workforce_service.work_orders.create(
            db_session, WorkOrderCreate(title=f"Job {i}", project_id=project.id, ticket_id=ticket.id)
        )
        wo.assigned_to_person_id = person.id
        wo.status = WorkOrderStatus.scheduled
        wo.metadata_ = {"resolved_location": {"latitude": lat, "longitude": lng}}
        db_session.commit()
        work_orders.append(wo)

    route = routing.order_day_route(db_session, str(person.id), start_latitude=JOB_LAT, start_longitude=JOB_LNG)
    # Nearest-first: +0.01, then +0.02, then +0.03.
    assert [r["sequence"] for r in route] == [1, 2, 3]
    assert route[0]["work_order_id"] == str(work_orders[1].id)
    assert route[1]["work_order_id"] == str(work_orders[2].id)
    assert route[2]["work_order_id"] == str(work_orders[0].id)
    assert route[2]["distance_km"] >= route[1]["distance_km"]


def test_day_route_puts_unlocated_jobs_last(db_session, person, project, ticket):
    located = workforce_service.work_orders.create(
        db_session, WorkOrderCreate(title="Located", project_id=project.id, ticket_id=ticket.id)
    )
    located.assigned_to_person_id = person.id
    located.status = WorkOrderStatus.scheduled
    located.metadata_ = {"resolved_location": {"latitude": JOB_LAT, "longitude": JOB_LNG}}
    unlocated = workforce_service.work_orders.create(
        db_session, WorkOrderCreate(title="Unlocated", project_id=project.id, ticket_id=ticket.id)
    )
    unlocated.assigned_to_person_id = person.id
    unlocated.status = WorkOrderStatus.scheduled
    db_session.commit()

    route = routing.order_day_route(db_session, str(person.id), start_latitude=JOB_LAT, start_longitude=JOB_LNG)
    assert route[-1]["work_order_id"] == str(unlocated.id)
    assert route[-1]["distance_km"] is None
