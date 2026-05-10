import contextlib
import datetime as _datetime
import enum
import os
import sqlite3
import typing as _typing
import uuid
from datetime import timezone
from importlib.util import find_spec

import pytest
from dotenv import load_dotenv
from sqlalchemy import String, TypeDecorator, create_engine, event, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

load_dotenv(os.path.join(os.getcwd(), ".env"))

# In minimal local test environments, psycopg may be absent.
# Force SQLite so importing app.db does not require PostgreSQL drivers.
if (os.getenv("DATABASE_URL") or "").startswith("postgresql") and find_spec("psycopg") is None:
    os.environ["DATABASE_URL"] = "sqlite+pysqlite://"

# Python 3.10 compatibility for modules that import `UTC` from datetime.
if not hasattr(_datetime, "UTC"):
    _datetime.UTC = timezone.utc  # noqa: UP017

if not hasattr(enum, "StrEnum"):

    class _StrEnum(str, enum.Enum):  # noqa: UP042
        pass

    enum.StrEnum = _StrEnum

if not hasattr(_typing, "Self"):
    from typing_extensions import Self as _TypingSelf  # noqa: UP035

    _typing.Self = _TypingSelf

from app.db import Base  # noqa: E402


class _JoseDateTimeProxy:
    @staticmethod
    def utcnow():
        from datetime import datetime

        return datetime.now(timezone.utc)  # noqa: UP017

    @staticmethod
    def now(tz=None):
        from datetime import datetime

        return datetime.now(tz)

    def __getattr__(self, name: str):
        from datetime import datetime

        return getattr(datetime, name)


@pytest.fixture(autouse=True)
def _patch_jose_datetime(monkeypatch):
    import jose.jwt as jose_jwt

    monkeypatch.setattr(jose_jwt, "datetime", _JoseDateTimeProxy, raising=False)


# Register UUID adapter for SQLite - store as string
sqlite3.register_adapter(uuid.UUID, lambda u: str(u))


class SQLiteUUID(TypeDecorator):
    """UUID type that works with SQLite by storing as string."""

    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if isinstance(value, uuid.UUID):
                return str(value)
            return value
        return None

    def process_result_value(self, value, dialect):
        if value is not None:
            if not isinstance(value, uuid.UUID):
                return uuid.UUID(value)
            return value
        return None


# Monkey-patch SQLAlchemy's UUID type for SQLite compatibility
# This must happen before any models are imported
from sqlalchemy.sql import sqltypes  # noqa: E402

_original_uuid_bind_processor = sqltypes.Uuid.bind_processor
_original_uuid_result_processor = sqltypes.Uuid.result_processor


def _sqlite_uuid_bind_processor(self, dialect):
    if dialect.name == "sqlite":

        def process(value):
            if value is not None:
                if isinstance(value, uuid.UUID):
                    return str(value)
                return str(uuid.UUID(value)) if value else None
            return None

        return process
    return _original_uuid_bind_processor(self, dialect)


def _sqlite_uuid_result_processor(self, dialect, coltype):
    if dialect.name == "sqlite":

        def process(value):
            if value is not None:
                if isinstance(value, uuid.UUID):
                    return value
                return uuid.UUID(value) if value else None
            return None

        return process
    return _original_uuid_result_processor(self, dialect, coltype)


sqltypes.Uuid.bind_processor = _sqlite_uuid_bind_processor
sqltypes.Uuid.result_processor = _sqlite_uuid_result_processor


# Monkey-patch PostgreSQL JSONB type for SQLite compatibility
# SQLite uses JSON instead of JSONB

_original_jsonb_compile = None


def _patch_jsonb_for_sqlite():
    """Make JSONB compile as JSON for SQLite dialect."""
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

    if not hasattr(SQLiteTypeCompiler, "_original_visit_JSONB"):
        # Store original if it exists, otherwise create a fallback
        if hasattr(SQLiteTypeCompiler, "visit_JSONB"):
            SQLiteTypeCompiler._original_visit_JSONB = SQLiteTypeCompiler.visit_JSONB

        def visit_JSONB(self, type_, **kw):
            return self.visit_JSON(type_, **kw)

        SQLiteTypeCompiler.visit_JSONB = visit_JSONB


_patch_jsonb_for_sqlite()

from app.models import automation_rule as _automation_rule  # noqa: F401,E402
from app.models.person import Person  # noqa: E402
from app.schemas.gis import GeoLayerCreate  # noqa: E402
from app.schemas.network import OLTDeviceCreate  # noqa: E402
from app.schemas.projects import ProjectCreate, ProjectTaskCreate  # noqa: E402
from app.schemas.tickets import TicketCreate  # noqa: E402
from app.schemas.workforce import WorkOrderCreate  # noqa: E402
from app.services import gis as gis_service  # noqa: E402
from app.services import network as network_service  # noqa: E402
from app.services import projects as projects_service  # noqa: E402
from app.services import tickets as tickets_service  # noqa: E402
from app.services import workforce as workforce_service  # noqa: E402


def _resolve_test_database_url() -> str | None:
    def _running_in_container() -> bool:
        return os.path.exists("/.dockerenv") or os.getenv("RUNNING_IN_DOCKER") == "1"

    raw_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not raw_url:
        return None

    url = make_url(raw_url)
    if url.drivername.startswith("postgresql"):
        if url.database != "crm_test":
            url = url.set(database="crm_test")
        if url.host == "db" and not _running_in_container():
            url = url.set(host="localhost")
        return url.render_as_string(hide_password=False)

    return raw_url


def _backfill_enum_values(engine):
    """Ensure all PG enum values are present after create_all.

    Both app.models.person.ChannelType and app.models.crm.enums.ChannelType map
    to a single PG enum called 'channeltype'. Depending on table creation order,
    some values may be missing.  ALTER TYPE … ADD VALUE cannot run inside a
    transaction, so we use AUTOCOMMIT isolation.
    """
    from app.models.crm.enums import ChannelType as CrmChannelType
    from app.models.person import ChannelType as PersonChannelType

    all_values = {m.value for m in PersonChannelType} | {m.value for m in CrmChannelType}

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        existing = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT enumlabel FROM pg_enum "
                    "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
                    "WHERE typname = 'channeltype'"
                )
            )
        }
        for val in sorted(all_values - existing):
            conn.execute(text(f"ALTER TYPE channeltype ADD VALUE IF NOT EXISTS '{val}'"))


@pytest.fixture(scope="session")
def engine():
    database_url = _resolve_test_database_url()
    if database_url:
        # Prefer PostgreSQL when available; fall back to SQLite when unavailable.
        with contextlib.suppress(SQLAlchemyError):
            engine = create_engine(database_url)
            with engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            Base.metadata.create_all(engine)
            # Both app.models.person.ChannelType and app.models.crm.enums.ChannelType
            # map to the same PG enum 'channeltype'. Whichever create_all processes
            # first wins; add any missing values so both enums are fully represented.
            _backfill_enum_values(engine)
            return engine

    # Fall back to SQLite with Spatialite
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={
            "check_same_thread": False,
        },
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _load_spatialite(dbapi_connection, _connection_record):
        dbapi_connection.enable_load_extension(True)
        spatialite_loaded = False
        with contextlib.suppress(Exception):
            dbapi_connection.load_extension("mod_spatialite")
            spatialite_loaded = True
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        if spatialite_loaded:
            with contextlib.suppress(Exception):
                cursor.execute("SELECT InitSpatialMetaData(1)")
        else:
            # GeoAlchemy2 calls these during table DDL; provide no-op shims when
            # SpatiaLite is unavailable so non-spatial tests can still run.
            dbapi_connection.create_function("InitSpatialMetaData", 1, lambda _x: 1)
            dbapi_connection.create_function("RecoverGeometryColumn", 5, lambda *_args: 1)
            dbapi_connection.create_function("DiscardGeometryColumn", 2, lambda *_args: 1)
            dbapi_connection.create_function("CreateSpatialIndex", 2, lambda *_args: 1)
            dbapi_connection.create_function("DisableSpatialIndex", 2, lambda *_args: 1)
            dbapi_connection.create_function("GeomFromEWKT", 1, lambda value: value)
            dbapi_connection.create_function("AsEWKB", 1, lambda value: value)
        cursor.close()

    # Create a connection first to initialize spatialite
    with engine.connect() as conn:
        pass

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def db_session(engine):
    connection = engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(bind=connection, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


@pytest.fixture()
def person(db_session):
    person = Person(
        first_name="Test",
        last_name="User",
        email=_unique_email(),
    )
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)
    return person


class _StubSubscriber:
    """Stub subscriber for tests that expect subscriber fixture."""

    def __init__(self, person):
        self.id = uuid.uuid4()
        self.person_id = person.id
        self.person = person


class _StubSubscriberAccount:
    """Stub subscriber account for tests that expect subscriber_account fixture."""

    def __init__(self, subscriber):
        self.id = uuid.uuid4()
        self.subscriber_id = subscriber.id
        self.subscriber = subscriber


class _StubSubscription:
    """Stub subscription for tests that expect subscription fixture."""

    def __init__(self, account):
        self.id = uuid.uuid4()
        self.account_id = account.id
        self.offer_id = uuid.uuid4()


@pytest.fixture()
def subscriber(person):
    """Stub subscriber fixture (Subscriber model removed)."""
    return _StubSubscriber(person)


@pytest.fixture()
def subscriber_account(subscriber):
    """Stub subscriber account fixture (SubscriberAccount model removed)."""
    return _StubSubscriberAccount(subscriber)


@pytest.fixture()
def subscription(subscriber_account):
    """Stub subscription fixture (Subscription model removed)."""
    return _StubSubscription(subscriber_account)


@pytest.fixture()
def network_device():
    """Stub network device fixture (NetworkDevice model removed)."""

    class _StubNetworkDevice:
        def __init__(self):
            self.id = uuid.uuid4()

    return _StubNetworkDevice()


@pytest.fixture()
def ticket(db_session):
    ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(
            title="Connectivity issue",
        ),
    )
    return ticket


@pytest.fixture()
def project(db_session):
    project = projects_service.projects.create(
        db_session,
        ProjectCreate(
            name="Fiber rollout",
        ),
    )
    return project


@pytest.fixture()
def project_task(db_session, project):
    task = projects_service.project_tasks.create(
        db_session,
        ProjectTaskCreate(
            project_id=project.id,
            title="Splice segment A",
        ),
    )
    return task


@pytest.fixture()
def work_order(db_session, project, ticket):
    work_order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(
            title="Install ONT",
            project_id=project.id,
            ticket_id=ticket.id,
        ),
    )
    return work_order


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", os.getenv("JWT_SECRET", "test-secret"))
    monkeypatch.setenv("JWT_ALGORITHM", os.getenv("JWT_ALGORITHM", "HS256"))


@pytest.fixture()
def olt_device(db_session):
    """OLT device for fiber tests."""
    olt = network_service.olt_devices.create(
        db_session,
        OLTDeviceCreate(
            name="Test OLT",
            hostname="olt-01.test.local",
        ),
    )
    return olt


@pytest.fixture()
def geo_layer(db_session):
    """GIS layer for geo tests."""
    layer = gis_service.geo_layers.create(
        db_session,
        GeoLayerCreate(
            name="Test Layer",
            layer_type="boundary",
        ),
    )
    return layer


# ============================================================================
# CRM Fixtures
# ============================================================================

from app.models.crm.team import CrmAgent, CrmAgentTeam, CrmTeam  # noqa: E402
from app.models.person import ChannelType, PersonChannel  # noqa: E402


@pytest.fixture()
def crm_contact(db_session):
    """CRM contact for conversation tests."""
    person = Person(
        first_name="Test",
        last_name="Contact",
        display_name="Test Contact",
        email=_unique_email(),
        phone="+1555123456",
    )
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)
    return person


@pytest.fixture()
def crm_contact_channel(db_session, crm_contact):
    """CRM contact channel for messaging tests."""
    channel = PersonChannel(
        person_id=crm_contact.id,
        channel_type=ChannelType.email,
        address=crm_contact.email,
        is_primary=True,
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


from app.models.inventory import InventoryItem  # noqa: E402
from app.models.material_request import MaterialRequest, MaterialRequestItem  # noqa: E402
from app.models.service_team import (  # noqa: E402
    ServiceTeam,
    ServiceTeamMember,
    ServiceTeamMemberRole,
    ServiceTeamType,
)


@pytest.fixture()
def inventory_item(db_session):
    """Inventory item for material request tests."""
    item = InventoryItem(name="Fiber Splice Closure", sku="FIB-SC-001")
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


@pytest.fixture()
def service_team(db_session):
    """Service team for team/member tests."""
    team = ServiceTeam(
        name="Test Field Team",
        team_type=ServiceTeamType.field_service,
        region="Western Cape",
    )
    db_session.add(team)
    db_session.commit()
    db_session.refresh(team)
    return team


@pytest.fixture()
def service_team_member(db_session, service_team, person):
    """Service team member linking person to team."""
    member = ServiceTeamMember(
        team_id=service_team.id,
        person_id=person.id,
        role=ServiceTeamMemberRole.member,
    )
    db_session.add(member)
    db_session.commit()
    db_session.refresh(member)
    return member


@pytest.fixture()
def material_request(db_session, person, ticket):
    """Draft material request linked to a ticket."""
    mr = MaterialRequest(
        ticket_id=ticket.id,
        requested_by_person_id=person.id,
    )
    db_session.add(mr)
    db_session.commit()
    db_session.refresh(mr)
    return mr


@pytest.fixture()
def material_request_with_item(db_session, material_request, inventory_item):
    """Material request with one line item."""
    item = MaterialRequestItem(
        material_request_id=material_request.id,
        item_id=inventory_item.id,
        quantity=5,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(material_request)
    return material_request


@pytest.fixture()
def crm_team(db_session):
    """CRM team for routing tests."""
    team = CrmTeam(
        name="Support Team",
    )
    db_session.add(team)
    db_session.commit()
    db_session.refresh(team)
    return team


@pytest.fixture()
def crm_agent(db_session, person):
    """CRM agent for team tests."""
    agent = CrmAgent(
        person_id=person.id,
        title="Support Agent",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    return agent


@pytest.fixture()
def crm_agent_team(db_session, crm_agent, crm_team):
    """Agent-team link for routing tests."""
    link = CrmAgentTeam(
        agent_id=crm_agent.id,
        team_id=crm_team.id,
    )
    db_session.add(link)
    db_session.commit()
    db_session.refresh(link)
    return link


@pytest.fixture()
def crm_conversation_factory(db_session):
    """Factory for building CRM conversations in workqueue tests.

    The CRM `Conversation` model has no `sla_due_at` / `last_inbound_at`
    columns; we stash them in `metadata_` (JSON) since that is exactly how the
    Workqueue conversations provider reads them.

    `assignee_person_id` may be:
      * ``None`` — leave the conversation unassigned.
      * A ``UUID`` — create (or reuse) a `Person` + `CrmAgent` for that id and
        attach an active `ConversationAssignment` to the conversation.

    `last_inbound_at` defaults to ~5h ago so a conversation without explicit
    SLA still classifies as ``awaiting_reply_long`` (otherwise the provider
    would skip it and tests that don't care about SLA would see no items).
    """
    from datetime import UTC as _UTC
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from app.models.crm.conversation import Conversation, ConversationAssignment
    from app.models.crm.enums import ConversationStatus

    def _factory(
        *,
        assignee_person_id: uuid.UUID | None = None,
        sla_due_at: _dt | None = None,
        last_inbound_at: _dt | None = None,
        status: ConversationStatus = ConversationStatus.open,
        subject: str | None = None,
    ) -> Conversation:
        # Contact (the conversation's "person")
        contact = Person(
            first_name="WQ",
            last_name="Contact",
            email=_unique_email(),
        )
        db_session.add(contact)
        db_session.flush()

        # Default last_inbound to >4h ago so plain conversations still classify.
        effective_last_inbound = last_inbound_at
        if effective_last_inbound is None and sla_due_at is None:
            effective_last_inbound = _dt.now(_UTC) - _td(hours=5)

        meta: dict = {}
        if sla_due_at is not None:
            meta["sla_due_at"] = sla_due_at.isoformat()
        if effective_last_inbound is not None:
            meta["last_inbound_at"] = effective_last_inbound.isoformat()

        conv = Conversation(
            person_id=contact.id,
            status=status,
            subject=subject or "Workqueue test conversation",
            last_message_at=effective_last_inbound,
            metadata_=meta or None,
        )
        db_session.add(conv)
        db_session.flush()

        if assignee_person_id is not None:
            # Ensure a Person exists for the assignee id (CrmAgent.person_id
            # is FK to people.id with nullable=False).
            assignee_person = db_session.get(Person, assignee_person_id)
            if assignee_person is None:
                assignee_person = Person(
                    id=assignee_person_id,
                    first_name="WQ",
                    last_name="Agent",
                    email=_unique_email(),
                )
                db_session.add(assignee_person)
                db_session.flush()

            # Reuse an existing CrmAgent for this person if one exists.
            agent = (
                db_session.query(CrmAgent)
                .filter(CrmAgent.person_id == assignee_person_id)
                .one_or_none()
            )
            if agent is None:
                agent = CrmAgent(person_id=assignee_person_id, title="Agent")
                db_session.add(agent)
                db_session.flush()

            assignment = ConversationAssignment(
                conversation_id=conv.id,
                agent_id=agent.id,
                is_active=True,
            )
            db_session.add(assignment)
            db_session.flush()

        db_session.commit()
        db_session.refresh(conv)
        return conv

    return _factory
