"""Seed rich, edge-case-oriented test data into the target database.

Intended for the `crm_test` QA database. Idempotent: re-running detects the
sentinel person and skips. Each module section is wrapped so one failure does
not abort the rest; a summary is printed at the end.

Run (against crm_test):
    docker exec -e DATABASE_URL=<crm_test url> -e PYTHONPATH=/app -w /app \
        dotmac_omni_app python scripts/seed_test_data.py
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.db import SessionLocal
from app.models.person import ChannelType, Person, PersonChannel

SENTINEL_EMAIL = "ada.seed@test.local"

summary: dict[str, int] = {}
errors: list[str] = []


def _count(label: str, n: int) -> None:
    summary[label] = summary.get(label, 0) + n


def _section(label: str, fn) -> None:
    db = SessionLocal()
    try:
        n = fn(db)
        db.commit()
        _count(label, n or 0)
        print(f"[ok]   {label}: +{n}")
    except Exception as exc:  # noqa: BLE001 - we want to keep going
        db.rollback()
        errors.append(f"{label}: {type(exc).__name__}: {exc}")
        print(f"[FAIL] {label}: {type(exc).__name__}: {exc}")
    finally:
        db.close()


# --------------------------------------------------------------------------
# People / contacts (with deliberate edge cases)
# --------------------------------------------------------------------------
PEOPLE = [
    # (first, last, display, email, phone)
    ("Ada", "Lovelace", "Ada Lovelace", SENTINEL_EMAIL, "+15550000001"),
    ("Grace", "Hopper", "Grace Hopper", "grace.seed@test.local", "+15550000002"),
    ("Alan", "Turing", "Alan Turing", "alan.seed@test.local", "+15550000003"),
    # Unicode / accents
    ("José", "Müller", "José Müller", "jose.seed@test.local", "+49170000004"),
    ("李", "伟", "李伟", "liwei.seed@test.local", "+8613800000005"),
    ("Søren", "Kierkegaard", "Søren Kierkegaard", "soren.seed@test.local", None),
    # Minimal-contact (DB requires email; phone omitted) edge case
    ("MinimalA", "Person", "Minimal A Person", "minimala.seed@test.local", "+15550000007"),
    # Missing phone (edge case)
    ("NoPhone", "Person", "No Phone Person", "nophone.seed@test.local", None),
    # Very long name (overflow edge case)
    (
        "Maximiliana",
        "Featherstonehaugh-Worthington-Beauchamp",
        "Maximiliana Featherstonehaugh-Worthington-Beauchamp III, Esq.",
        "longname.seed@test.local",
        "+15550000009",
    ),
    # Special characters / injection-ish (should be escaped)
    ("Robert", "'); DROP TABLE--", "Bobby <b>Tables</b>", "bobby.seed@test.local", "+15550000010"),
    ("Emoji", "User", "🚀 Rocket User 😀", "emoji.seed@test.local", "+15550000011"),
    ("Whitespace", "   ", "  Padded  Name  ", "ws.seed@test.local", "+15550000012"),
]


def seed_people(db) -> int:
    made = 0
    for first, last, display, email, phone in PEOPLE:
        p = Person(first_name=first, last_name=last, display_name=display, email=email, phone=phone)
        db.add(p)
        db.flush()
        if email:
            db.add(
                PersonChannel(
                    person_id=p.id,
                    channel_type=ChannelType.email,
                    address=email,
                    is_primary=True,
                )
            )
        made += 1
    return made


# --------------------------------------------------------------------------
# Tickets (every status + priority, with/without description)
# --------------------------------------------------------------------------
def seed_tickets(db) -> int:
    from app.models.tickets import TicketPriority, TicketStatus
    from app.schemas.tickets import TicketCreate
    from app.services import tickets as tickets_service

    statuses = list(TicketStatus)
    priorities = list(TicketPriority)
    made = 0
    for i, status in enumerate(statuses):
        prio = priorities[i % len(priorities)]
        payload = TicketCreate(
            title=f"[{status.value}] Connectivity issue #{i + 1}",
            description=(None if i % 3 == 0 else f"Customer reports {status.value} state. Priority {prio.value}."),
            status=status,
            priority=prio,
            region=("Lagos" if i % 2 == 0 else "Abuja"),
            tags=["seed", status.value],
        )
        tickets_service.tickets.create(db, payload)
        made += 1
    # A couple extra high/urgent open tickets to make queues look real
    for n in range(3):
        tickets_service.tickets.create(
            db,
            TicketCreate(
                title=f"Urgent fiber cut - segment {chr(65 + n)}",
                description="Fiber cut detected, customers offline.",
                status=TicketStatus.open,
                priority=TicketPriority.urgent,
                region="Lagos",
                tags=["seed", "outage"],
            ),
        )
        made += 1
    return made


# --------------------------------------------------------------------------
# Projects + tasks + work orders
# --------------------------------------------------------------------------
def seed_projects(db) -> int:
    from app.schemas.projects import ProjectCreate, ProjectTaskCreate
    from app.schemas.workforce import WorkOrderCreate
    from app.services import projects as projects_service
    from app.services import workforce as workforce_service

    made = 0
    names = ["Fiber rollout - Ikeja", "Backbone upgrade", "FTTH Phase 2", "Tower build - Lekki"]
    for name in names:
        proj = projects_service.projects.create(db, ProjectCreate(name=name))
        made += 1
        for t in ["Survey site", "Trench & duct", "Splice & test"]:
            task = projects_service.project_tasks.create(
                db, ProjectTaskCreate(project_id=proj.id, title=f"{t} ({name})")
            )
            made += 1
        wo = workforce_service.work_orders.create(
            db, WorkOrderCreate(title=f"Install ONT for {name}", project_id=proj.id)
        )
        made += 1
        _ = task, wo
    return made


# --------------------------------------------------------------------------
# CRM: leads, conversations, teams, agents
# --------------------------------------------------------------------------
def seed_crm(db) -> int:
    from decimal import Decimal

    from app.models.crm.conversation import Conversation
    from app.models.crm.enums import ConversationStatus, LeadStatus
    from app.models.crm.sales import Lead
    from app.models.crm.team import CrmAgent, CrmTeam

    made = 0

    # Teams
    teams = []
    for tname in ["Support Team", "Sales Team", "Onboarding"]:
        team = CrmTeam(name=tname)
        db.add(team)
        teams.append(team)
        made += 1
    db.flush()

    # Agents (need person)
    agent_people = (
        db.query(Person).filter(Person.email.in_(["grace.seed@test.local", "alan.seed@test.local"])).all()
    )
    for ap in agent_people:
        db.add(CrmAgent(person_id=ap.id, title="Support Agent"))
        made += 1

    # Leads across every status with varied value/probability
    contacts = db.query(Person).filter(Person.email.like("%.seed@test.local")).limit(8).all()
    for i, status in enumerate(LeadStatus):
        contact = contacts[i % len(contacts)] if contacts else None
        if contact is None:
            break
        db.add(
            Lead(
                person_id=contact.id,
                title=f"{status.value.title()} lead - {contact.display_name}",
                status=status,
                estimated_value=Decimal(str(1000 * (i + 1))),
                currency="USD",
                probability=min(10 * (i + 1), 100),
                region=("Lagos" if i % 2 else "Abuja"),
                lead_source="seed",
            )
        )
        made += 1

    # Conversations across statuses
    for i, status in enumerate(ConversationStatus):
        contact = contacts[i % len(contacts)] if contacts else None
        if contact is None:
            break
        db.add(
            Conversation(
                person_id=contact.id,
                status=status,
                subject=f"{status.value} conversation - {contact.display_name}",
                last_message_at=datetime.now(UTC) - timedelta(hours=i + 1),
                metadata_={"last_inbound_at": (datetime.now(UTC) - timedelta(hours=i + 1)).isoformat()},
            )
        )
        made += 1

    return made


# --------------------------------------------------------------------------
# Inventory + service teams + material requests
# --------------------------------------------------------------------------
def seed_inventory_and_requests(db) -> int:
    from app.models.inventory import InventoryItem
    from app.models.material_request import MaterialRequest, MaterialRequestItem
    from app.models.service_team import (
        ServiceTeam,
        ServiceTeamMember,
        ServiceTeamMemberRole,
        ServiceTeamType,
    )

    made = 0
    items = [
        ("Fiber Splice Closure", "FIB-SC-001"),
        ("ONT Router GPON", "ONT-GP-100"),
        ("Drop Cable 100m", "CAB-DR-100"),
        ("SC/APC Connector", "CON-SCAPC-1"),
        ("Patch Panel 24p", "PP-24-001"),
    ]
    item_objs = []
    for name, sku in items:
        it = InventoryItem(name=name, sku=sku)
        db.add(it)
        item_objs.append(it)
        made += 1
    db.flush()

    team = ServiceTeam(name="Field Team Alpha", team_type=ServiceTeamType.field_service, region="Lagos")
    db.add(team)
    db.flush()
    made += 1
    members = db.query(Person).filter(Person.email.like("%.seed@test.local")).limit(3).all()
    for m in members:
        db.add(ServiceTeamMember(team_id=team.id, person_id=m.id, role=ServiceTeamMemberRole.member))
        made += 1

    # Material request linked to first seeded ticket + requester
    from app.models.tickets import Ticket

    a_ticket = db.query(Ticket).first()
    requester = db.query(Person).filter(Person.email == SENTINEL_EMAIL).first()
    if a_ticket and requester:
        mr = MaterialRequest(ticket_id=a_ticket.id, requested_by_person_id=requester.id)
        db.add(mr)
        db.flush()
        made += 1
        db.add(MaterialRequestItem(material_request_id=mr.id, item_id=item_objs[0].id, quantity=5))
        db.add(MaterialRequestItem(material_request_id=mr.id, item_id=item_objs[1].id, quantity=2))
        made += 2

    return made


# --------------------------------------------------------------------------
# Network OLT + GIS layer
# --------------------------------------------------------------------------
def seed_network_gis(db) -> int:
    from app.models.gis import GeoLayer
    from app.models.network import OLTDevice
    from app.schemas.gis import GeoLayerCreate
    from app.schemas.network import OLTDeviceCreate
    from app.services import gis as gis_service
    from app.services import network as network_service

    made = 0
    for n in range(2):
        host = f"olt-core-{n + 1:02d}.test.local"
        if db.query(OLTDevice).filter(OLTDevice.hostname == host).first():
            continue
        network_service.olt_devices.create(
            db,
            OLTDeviceCreate(name=f"OLT-Core-{n + 1}", hostname=host),
        )
        made += 1
    for lname, lkey, ltype in [
        ("Service Boundaries", "service_boundaries", "polygons"),
        ("Fiber Routes", "fiber_routes", "lines"),
        ("Customer Points", "customer_points", "points"),
    ]:
        if db.query(GeoLayer).filter(GeoLayer.layer_key == lkey).first():
            continue
        gis_service.geo_layers.create(
            db, GeoLayerCreate(name=lname, layer_key=lkey, layer_type=ltype)
        )
        made += 1
    return made


def main() -> None:
    db = SessionLocal()
    try:
        exists = db.query(Person).filter(Person.email == SENTINEL_EMAIL).first()
    finally:
        db.close()
    if exists:
        print(f"Sentinel {SENTINEL_EMAIL} already present — test data already seeded. Skipping.")
        return

    _section("people", seed_people)
    _section("tickets", seed_tickets)
    _section("projects+tasks+workorders", seed_projects)
    _section("crm(leads/convos/teams/agents)", seed_crm)
    _section("inventory+teams+material_requests", seed_inventory_and_requests)
    _section("network_olt+gis", seed_network_gis)

    print("\n=== SEED SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    if errors:
        print("\n=== ERRORS ===")
        for e in errors:
            print(f"  - {e}")


if __name__ == "__main__":
    main()
