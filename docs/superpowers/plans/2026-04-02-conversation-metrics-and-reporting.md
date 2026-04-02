# Conversation Metrics, SLA Tracking & Agent Performance Reporting — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `first_response_at`/`resolved_at` fields to conversations, fix CSAT survey flow + add inline prompts, enforce tag nudging before resolution, build SLA breach detection, daily data quality alerts, and a weekly agent performance report page.

**Architecture:** New columns on the `Conversation` model with indexes. Metric population happens in existing service hooks (outbound message creation, status transition). Two new Celery tasks for SLA monitoring and data quality checks. One new admin report page reusing the existing `crm_reports_service` pattern. Frontend tag nudge via Alpine.js on the resolve flow.

**Tech Stack:** SQLAlchemy 2.0, Alembic, Celery, Pydantic v2, Jinja2/HTMX/Alpine.js, pytest

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `alembic/versions/xxxx_add_conversation_metric_fields.py` | Migration: add 4 columns + 3 indexes to `crm_conversations` |
| `app/services/crm/inbox/sla.py` | SLA target loading, breach detection queries, breach alerting logic |
| `app/services/crm/inbox/data_quality.py` | Daily data quality check logic (missing fields detection) |
| `templates/admin/crm/_tag_nudge_modal.html` | Alpine.js confirmation modal for tagless resolution |
| `templates/admin/reports/agent_performance.html` | Weekly agent performance report page |
| `tests/test_conversation_metrics.py` | Tests for first_response_at, resolved_at population |
| `tests/test_sla_breach.py` | Tests for SLA breach detection and alerting |
| `tests/test_data_quality_check.py` | Tests for daily data quality task |
| `tests/test_agent_performance_report.py` | Tests for report service function |

### Modified Files
| File | Changes |
|------|---------|
| `app/models/crm/conversation.py:26-67` | Add 4 new columns to `Conversation` model |
| `app/services/crm/conversations/service.py:218-252` | Hook `first_response_at` population into `Messages.create()` |
| `app/services/crm/inbox/conversation_status.py:87-141` | Set `resolved_at`/`resolution_time_seconds` on resolve; clear on reopen |
| `app/services/crm/inbox/csat.py:185-254` | Fix existing flow + add inline CSAT message |
| `app/tasks/crm_inbox.py` | Add 2 new Celery tasks (SLA breach check, data quality check) |
| `app/services/scheduler_config.py:411-472` | Register 2 new scheduled tasks |
| `app/services/crm/reports.py:459+` | Add `agent_weekly_performance()` function |
| `app/web/admin/reports.py:925+` | Add agent performance report route |
| `app/web/admin/crm_inbox_status.py:130-142` | Integrate tag nudge check before resolve gate |
| `app/services/crm/inbox/queries.py` | Add `missing` filter parameter |
| `app/services/settings_seed.py` | Seed default SLA target settings |

---

## Task 1: Add Metric Columns to Conversation Model + Migration

**Files:**
- Modify: `app/models/crm/conversation.py:26-67`
- Create: `alembic/versions/xxxx_add_conversation_metric_fields.py`

- [ ] **Step 1: Add columns to Conversation model**

In `app/models/crm/conversation.py`, add these columns to the `Conversation` class after line 45 (`is_muted`):

```python
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_time_seconds: Mapped[int | None] = mapped_column(Integer)
    resolution_time_seconds: Mapped[int | None] = mapped_column(Integer)
```

- [ ] **Step 2: Generate the Alembic migration**

Run:
```bash
poetry run alembic revision --autogenerate -m "add conversation metric fields"
```

- [ ] **Step 3: Edit migration to add indexes**

Open the generated migration file. In the `upgrade()` function, after the column additions, add:

```python
    op.create_index(
        "ix_crm_conversations_first_response_at",
        "crm_conversations",
        ["first_response_at"],
    )
    op.create_index(
        "ix_crm_conversations_resolved_at",
        "crm_conversations",
        ["resolved_at"],
    )
    op.create_index(
        "ix_crm_conversations_status_first_response",
        "crm_conversations",
        ["status", "first_response_at"],
    )
```

In the `downgrade()` function, drop indexes before dropping columns:

```python
    op.drop_index("ix_crm_conversations_status_first_response", table_name="crm_conversations")
    op.drop_index("ix_crm_conversations_resolved_at", table_name="crm_conversations")
    op.drop_index("ix_crm_conversations_first_response_at", table_name="crm_conversations")
```

- [ ] **Step 4: Run the migration**

Run:
```bash
poetry run alembic upgrade head
```

- [ ] **Step 5: Verify columns exist**

Run:
```bash
poetry run python -c "from app.models.crm.conversation import Conversation; print([c.name for c in Conversation.__table__.columns if 'response' in c.name or 'resolved' in c.name])"
```

Expected: `['first_response_at', 'resolved_at', 'response_time_seconds', 'resolution_time_seconds']`

- [ ] **Step 6: Commit**

```bash
git add app/models/crm/conversation.py alembic/versions/*add_conversation_metric_fields*
git commit -m "feat: add first_response_at, resolved_at, response/resolution time columns to Conversation"
```

---

## Task 2: Populate first_response_at on Outbound Agent Messages

**Files:**
- Test: `tests/test_conversation_metrics.py`
- Modify: `app/services/crm/conversations/service.py:218-252`

- [ ] **Step 1: Write failing tests for first_response_at**

Create `tests/test_conversation_metrics.py`:

```python
"""Tests for conversation metric field population."""

import uuid
from datetime import UTC, datetime

from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.crm.team import CrmAgent
from app.models.person import Person
from app.schemas.crm.conversation import MessageCreate


def _make_person(db) -> Person:
    person = Person(
        first_name="Test",
        last_name="User",
        display_name="Test User",
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        is_active=True,
    )
    db.add(person)
    db.flush()
    return person


def _make_conversation(db, person_id) -> Conversation:
    conv = Conversation(
        person_id=person_id,
        status=ConversationStatus.open,
    )
    db.add(conv)
    db.flush()
    return conv


def _make_agent(db, person_id) -> CrmAgent:
    agent = CrmAgent(person_id=person_id, is_active=True)
    db.add(agent)
    db.flush()
    return agent


class TestFirstResponseAt:
    def test_set_on_first_agent_outbound_message(self, db_session):
        """first_response_at is set when the first agent-authored outbound message is created."""
        person = _make_person(db_session)
        agent_person = _make_person(db_session)
        agent = _make_agent(db_session, agent_person.id)
        conv = _make_conversation(db_session, person.id)

        assert conv.first_response_at is None

        from app.services.crm.conversations.service import Messages

        payload = MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body="Hello, how can I help?",
            author_id=agent_person.id,
        )
        Messages.create(db_session, payload)

        db_session.refresh(conv)
        assert conv.first_response_at is not None
        assert conv.response_time_seconds is not None
        assert conv.response_time_seconds >= 0

    def test_not_set_on_inbound_message(self, db_session):
        """first_response_at stays None for inbound messages."""
        person = _make_person(db_session)
        conv = _make_conversation(db_session, person.id)

        from app.services.crm.conversations.service import Messages

        payload = MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.inbound,
            status=MessageStatus.received,
            body="I need help",
            author_id=person.id,
        )
        Messages.create(db_session, payload)

        db_session.refresh(conv)
        assert conv.first_response_at is None

    def test_not_set_on_non_agent_outbound(self, db_session):
        """first_response_at stays None when outbound author is not a CRM agent."""
        person = _make_person(db_session)
        non_agent_person = _make_person(db_session)
        conv = _make_conversation(db_session, person.id)

        from app.services.crm.conversations.service import Messages

        payload = MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body="Auto-reply: we received your message",
            author_id=non_agent_person.id,
        )
        Messages.create(db_session, payload)

        db_session.refresh(conv)
        assert conv.first_response_at is None

    def test_not_overwritten_on_second_outbound(self, db_session):
        """first_response_at is only set once — second agent message does not overwrite."""
        person = _make_person(db_session)
        agent_person = _make_person(db_session)
        agent = _make_agent(db_session, agent_person.id)
        conv = _make_conversation(db_session, person.id)

        from app.services.crm.conversations.service import Messages

        payload1 = MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body="First reply",
            author_id=agent_person.id,
        )
        Messages.create(db_session, payload1)
        db_session.refresh(conv)
        first_time = conv.first_response_at

        payload2 = MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body="Second reply",
            author_id=agent_person.id,
        )
        Messages.create(db_session, payload2)
        db_session.refresh(conv)

        assert conv.first_response_at == first_time

    def test_no_author_id_does_not_set(self, db_session):
        """first_response_at stays None when outbound message has no author_id (system message)."""
        person = _make_person(db_session)
        conv = _make_conversation(db_session, person.id)

        from app.services.crm.conversations.service import Messages

        payload = MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body="System notification",
            author_id=None,
        )
        Messages.create(db_session, payload)

        db_session.refresh(conv)
        assert conv.first_response_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
poetry run pytest tests/test_conversation_metrics.py -v -x
```

Expected: Tests fail because `first_response_at` is never set.

- [ ] **Step 3: Implement first_response_at in Messages.create()**

In `app/services/crm/conversations/service.py`, modify `Messages.create()`. After line 233 (`conversation.updated_at = timestamp`), add:

```python
        # Populate first_response_at for the first agent-authored outbound message.
        if (
            message.direction == MessageDirection.outbound
            and conversation.first_response_at is None
            and message.author_id is not None
        ):
            from app.models.crm.team import CrmAgent

            is_agent = (
                db.query(CrmAgent.id)
                .filter(CrmAgent.person_id == message.author_id, CrmAgent.is_active.is_(True))
                .first()
            ) is not None
            if is_agent:
                conversation.first_response_at = timestamp
                conversation.response_time_seconds = int(
                    (timestamp - conversation.created_at).total_seconds()
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
poetry run pytest tests/test_conversation_metrics.py -v -x
```

Expected: All 5 tests PASS.

- [ ] **Step 5: Run existing test suite to check for regressions**

Run:
```bash
poetry run pytest tests/ -x -q --timeout=120
```

Expected: No failures.

- [ ] **Step 6: Commit**

```bash
git add app/services/crm/conversations/service.py tests/test_conversation_metrics.py
git commit -m "feat: populate first_response_at on first agent-authored outbound message"
```

---

## Task 3: Set resolved_at on Status Transition

**Files:**
- Test: `tests/test_conversation_metrics.py` (append)
- Modify: `app/services/crm/inbox/conversation_status.py:87-141`

- [ ] **Step 1: Write failing tests for resolved_at**

Append to `tests/test_conversation_metrics.py`:

```python
from app.services.crm.inbox.conversation_status import update_conversation_status


class TestResolvedAt:
    def test_set_on_resolve(self, db_session):
        """resolved_at and resolution_time_seconds are set when conversation is resolved."""
        person = _make_person(db_session)
        conv = _make_conversation(db_session, person.id)
        conv_id = str(conv.id)

        result = update_conversation_status(
            db_session,
            conversation_id=conv_id,
            new_status="resolved",
        )
        assert result.kind == "updated"

        db_session.refresh(conv)
        assert conv.resolved_at is not None
        assert conv.resolution_time_seconds is not None
        assert conv.resolution_time_seconds >= 0

    def test_cleared_on_reopen(self, db_session):
        """resolved_at is cleared when a resolved conversation is reopened."""
        person = _make_person(db_session)
        conv = _make_conversation(db_session, person.id)
        conv_id = str(conv.id)

        update_conversation_status(db_session, conversation_id=conv_id, new_status="resolved")
        db_session.refresh(conv)
        assert conv.resolved_at is not None

        update_conversation_status(db_session, conversation_id=conv_id, new_status="open")
        db_session.refresh(conv)
        assert conv.resolved_at is None
        assert conv.resolution_time_seconds is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
poetry run pytest tests/test_conversation_metrics.py::TestResolvedAt -v -x
```

Expected: Tests fail because `resolved_at` is never set.

- [ ] **Step 3: Implement resolved_at in update_conversation_status()**

In `app/services/crm/inbox/conversation_status.py`, modify the `update_conversation_status()` function.

After line 115 (`db.commit()`), before the `inbox_cache.invalidate_inbox_list()` call on line 116, add:

```python
        # Populate resolved_at / resolution_time_seconds
        if conversation is None:
            conversation = db.get(Conversation, coerce_uuid(conversation_id))
        if conversation:
            if status_enum == ConversationStatus.resolved:
                now = datetime.now(UTC)
                conversation.resolved_at = now
                conversation.resolution_time_seconds = int(
                    (now - conversation.created_at).total_seconds()
                )
                db.commit()
            elif previous_status == ConversationStatus.resolved:
                conversation.resolved_at = None
                conversation.resolution_time_seconds = None
                db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
poetry run pytest tests/test_conversation_metrics.py -v -x
```

Expected: All tests PASS including TestResolvedAt.

- [ ] **Step 5: Commit**

```bash
git add app/services/crm/inbox/conversation_status.py tests/test_conversation_metrics.py
git commit -m "feat: set resolved_at on conversation resolution, clear on reopen"
```

---

## Task 4: Seed Default SLA Settings

**Files:**
- Modify: `app/services/settings_seed.py`

- [ ] **Step 1: Add SLA defaults to settings_seed.py**

In `app/services/settings_seed.py`, add a new function after the existing seed functions:

```python
def seed_sla_defaults(db: Session) -> None:
    """Seed default per-priority SLA targets for CRM conversations."""
    from app.models.domain_settings import SettingValueType
    from app.services.domain_settings import notification_settings
    from app.schemas.settings import DomainSettingUpdate

    sla_defaults = {
        "crm_sla_response_urgent_minutes": 60,
        "crm_sla_response_high_minutes": 240,
        "crm_sla_response_medium_minutes": 480,
        "crm_sla_response_low_minutes": 1440,
        "crm_sla_resolution_urgent_minutes": 240,
        "crm_sla_resolution_high_minutes": 1440,
        "crm_sla_resolution_medium_minutes": 2880,
        "crm_sla_resolution_low_minutes": 4320,
    }
    for key, default_value in sla_defaults.items():
        existing = notification_settings.get_by_key(db, key)
        if existing is None:
            notification_settings.upsert_by_key(
                db,
                key,
                DomainSettingUpdate(
                    value_type=SettingValueType.number,
                    value_text=str(default_value),
                ),
            )
```

Then ensure this function is called from the main seed entry point. Find the function that calls other seed functions (e.g., `seed_all` or `run_seeds`) and add `seed_sla_defaults(db)` to it.

- [ ] **Step 2: Verify seed runs without errors**

Run:
```bash
poetry run python -c "from app.db import SessionLocal; from app.services.settings_seed import seed_sla_defaults; db = SessionLocal(); seed_sla_defaults(db); db.close(); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/services/settings_seed.py
git commit -m "feat: seed default per-priority SLA targets for CRM conversations"
```

---

## Task 5: SLA Breach Detection Service

**Files:**
- Create: `app/services/crm/inbox/sla.py`
- Test: `tests/test_sla_breach.py`

- [ ] **Step 1: Write failing tests for SLA service**

Create `tests/test_sla_breach.py`:

```python
"""Tests for SLA breach detection."""

import uuid
from datetime import UTC, datetime, timedelta

from app.models.crm.conversation import Conversation, ConversationAssignment
from app.models.crm.enums import ConversationPriority, ConversationStatus
from app.models.crm.team import CrmAgent
from app.models.person import Person


def _make_person(db) -> Person:
    person = Person(
        first_name="Test",
        last_name="User",
        display_name="Test User",
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        is_active=True,
    )
    db.add(person)
    db.flush()
    return person


def _make_agent(db, person_id) -> CrmAgent:
    agent = CrmAgent(person_id=person_id, is_active=True)
    db.add(agent)
    db.flush()
    return agent


def _make_conversation(db, person_id, *, priority=ConversationPriority.medium, created_at=None) -> Conversation:
    conv = Conversation(
        person_id=person_id,
        status=ConversationStatus.open,
        priority=priority,
    )
    if created_at:
        conv.created_at = created_at
    db.add(conv)
    db.flush()
    return conv


class TestGetSlaTargets:
    def test_returns_default_targets(self, db_session):
        """SLA targets are returned with sensible defaults when no settings exist."""
        from app.services.crm.inbox.sla import get_sla_targets

        targets = get_sla_targets(db_session)
        assert "response" in targets
        assert "resolution" in targets
        assert "urgent" in targets["response"]
        assert "high" in targets["response"]
        assert "medium" in targets["response"]
        assert "low" in targets["response"]
        assert all(isinstance(v, int) for v in targets["response"].values())
        assert all(isinstance(v, int) for v in targets["resolution"].values())


class TestFindResponseBreaches:
    def test_detects_overdue_response(self, db_session):
        """Conversations past response SLA with no first_response_at are flagged."""
        from app.services.crm.inbox.sla import find_response_breaches

        person = _make_person(db_session)
        # Created 10 hours ago, medium SLA is 480 min (8 hours)
        conv = _make_conversation(
            db_session,
            person.id,
            priority=ConversationPriority.medium,
            created_at=datetime.now(UTC) - timedelta(hours=10),
        )
        db_session.commit()

        breaches = find_response_breaches(db_session, targets_minutes={"medium": 480, "low": 1440, "high": 240, "urgent": 60, "none": 1440})
        breach_ids = [str(b.id) for b in breaches]
        assert str(conv.id) in breach_ids

    def test_ignores_responded_conversations(self, db_session):
        """Conversations with first_response_at set are not flagged."""
        from app.services.crm.inbox.sla import find_response_breaches

        person = _make_person(db_session)
        conv = _make_conversation(
            db_session,
            person.id,
            priority=ConversationPriority.medium,
            created_at=datetime.now(UTC) - timedelta(hours=10),
        )
        conv.first_response_at = datetime.now(UTC) - timedelta(hours=5)
        db_session.commit()

        breaches = find_response_breaches(db_session, targets_minutes={"medium": 480, "low": 1440, "high": 240, "urgent": 60, "none": 1440})
        breach_ids = [str(b.id) for b in breaches]
        assert str(conv.id) not in breach_ids

    def test_ignores_resolved_conversations(self, db_session):
        """Resolved conversations are not flagged for response breaches."""
        from app.services.crm.inbox.sla import find_response_breaches

        person = _make_person(db_session)
        conv = _make_conversation(
            db_session,
            person.id,
            priority=ConversationPriority.medium,
            created_at=datetime.now(UTC) - timedelta(hours=10),
        )
        conv.status = ConversationStatus.resolved
        db_session.commit()

        breaches = find_response_breaches(db_session, targets_minutes={"medium": 480, "low": 1440, "high": 240, "urgent": 60, "none": 1440})
        breach_ids = [str(b.id) for b in breaches]
        assert str(conv.id) not in breach_ids


class TestFindResolutionBreaches:
    def test_detects_overdue_resolution(self, db_session):
        """Conversations past resolution SLA that are still open are flagged."""
        from app.services.crm.inbox.sla import find_resolution_breaches

        person = _make_person(db_session)
        # Created 50 hours ago, medium resolution SLA is 2880 min (48 hours)
        conv = _make_conversation(
            db_session,
            person.id,
            priority=ConversationPriority.medium,
            created_at=datetime.now(UTC) - timedelta(hours=50),
        )
        conv.first_response_at = datetime.now(UTC) - timedelta(hours=49)
        db_session.commit()

        breaches = find_resolution_breaches(db_session, targets_minutes={"medium": 2880, "low": 4320, "high": 1440, "urgent": 240, "none": 4320})
        breach_ids = [str(b.id) for b in breaches]
        assert str(conv.id) in breach_ids
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
poetry run pytest tests/test_sla_breach.py -v -x
```

Expected: ImportError — `app.services.crm.inbox.sla` does not exist yet.

- [ ] **Step 3: Implement the SLA service**

Create `app/services/crm/inbox/sla.py`:

```python
"""SLA breach detection for CRM inbox conversations."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation
from app.models.crm.enums import ConversationPriority, ConversationStatus
from app.models.domain_settings import SettingDomain
from app.services import settings_spec

logger = logging.getLogger(__name__)

# Default SLA targets in minutes (used when settings are not configured)
_DEFAULT_RESPONSE = {"urgent": 60, "high": 240, "medium": 480, "low": 1440, "none": 1440}
_DEFAULT_RESOLUTION = {"urgent": 240, "high": 1440, "medium": 2880, "low": 4320, "none": 4320}

_PRIORITY_KEYS = ("urgent", "high", "medium", "low", "none")


def get_sla_targets(db: Session) -> dict[str, dict[str, int]]:
    """Load per-priority SLA targets from settings, falling back to defaults."""
    response: dict[str, int] = {}
    resolution: dict[str, int] = {}
    for priority in _PRIORITY_KEYS:
        resp_key = f"crm_sla_response_{priority}_minutes"
        res_key = f"crm_sla_resolution_{priority}_minutes"
        resp_val = settings_spec.resolve_value(db, SettingDomain.notification, resp_key)
        res_val = settings_spec.resolve_value(db, SettingDomain.notification, res_key)
        response[priority] = int(resp_val) if resp_val is not None else _DEFAULT_RESPONSE[priority]
        resolution[priority] = int(res_val) if res_val is not None else _DEFAULT_RESOLUTION[priority]
    return {"response": response, "resolution": resolution}


def _priority_value(conv: Conversation) -> str:
    """Get the priority string key for a conversation."""
    if conv.priority is None:
        return "none"
    return conv.priority.value


def find_response_breaches(
    db: Session,
    targets_minutes: dict[str, int],
) -> list[Conversation]:
    """Find open/pending conversations that have breached response SLA."""
    now = datetime.now(UTC)
    candidates = (
        db.query(Conversation)
        .filter(
            Conversation.status.in_([ConversationStatus.open, ConversationStatus.pending]),
            Conversation.first_response_at.is_(None),
            Conversation.is_active.is_(True),
        )
        .all()
    )
    breached: list[Conversation] = []
    for conv in candidates:
        priority = _priority_value(conv)
        threshold_minutes = targets_minutes.get(priority, 1440)
        deadline = conv.created_at + timedelta(minutes=threshold_minutes)
        if now > deadline:
            breached.append(conv)
    return breached


def find_resolution_breaches(
    db: Session,
    targets_minutes: dict[str, int],
) -> list[Conversation]:
    """Find open/pending conversations that have breached resolution SLA."""
    now = datetime.now(UTC)
    candidates = (
        db.query(Conversation)
        .filter(
            Conversation.status.in_([ConversationStatus.open, ConversationStatus.pending]),
            Conversation.resolved_at.is_(None),
            Conversation.is_active.is_(True),
        )
        .all()
    )
    breached: list[Conversation] = []
    for conv in candidates:
        priority = _priority_value(conv)
        threshold_minutes = targets_minutes.get(priority, 4320)
        deadline = conv.created_at + timedelta(minutes=threshold_minutes)
        if now > deadline:
            breached.append(conv)
    return breached


def check_and_alert_breaches(db: Session) -> dict:
    """Run SLA breach check and create in-app alerts. Returns stats dict."""
    from app.models.notification import Notification, NotificationChannel, NotificationStatus

    targets = get_sla_targets(db)
    response_breaches = find_response_breaches(db, targets["response"])
    resolution_breaches = find_resolution_breaches(db, targets["resolution"])

    alerted_response = 0
    alerted_resolution = 0

    for conv in response_breaches:
        metadata = conv.metadata_ if isinstance(conv.metadata_, dict) else {}
        if metadata.get("sla_response_breach_alerted_at"):
            continue
        metadata["sla_response_breach_alerted_at"] = datetime.now(UTC).isoformat()
        conv.metadata_ = metadata

        # Find assigned agent's person_id for notification
        recipient = _resolve_notification_recipient(db, conv)
        if recipient:
            elapsed = datetime.now(UTC) - conv.created_at
            elapsed_hours = round(elapsed.total_seconds() / 3600, 1)
            db.add(
                Notification(
                    channel=NotificationChannel.push,
                    recipient=recipient,
                    subject=f"SLA Breach: Response overdue ({elapsed_hours}h)",
                    body=(
                        f"Conversation \"{conv.subject or 'No subject'}\" "
                        f"(priority: {_priority_value(conv)}) has no first response "
                        f"after {elapsed_hours} hours.\n"
                        f"Open: /admin/crm/inbox?conversation_id={conv.id}"
                    ),
                    status=NotificationStatus.delivered,
                    sent_at=datetime.now(UTC),
                )
            )
            alerted_response += 1

    for conv in resolution_breaches:
        metadata = conv.metadata_ if isinstance(conv.metadata_, dict) else {}
        if metadata.get("sla_resolution_breach_alerted_at"):
            continue
        metadata["sla_resolution_breach_alerted_at"] = datetime.now(UTC).isoformat()
        conv.metadata_ = metadata

        recipient = _resolve_notification_recipient(db, conv)
        if recipient:
            elapsed = datetime.now(UTC) - conv.created_at
            elapsed_hours = round(elapsed.total_seconds() / 3600, 1)
            db.add(
                Notification(
                    channel=NotificationChannel.push,
                    recipient=recipient,
                    subject=f"SLA Breach: Resolution overdue ({elapsed_hours}h)",
                    body=(
                        f"Conversation \"{conv.subject or 'No subject'}\" "
                        f"(priority: {_priority_value(conv)}) has been open "
                        f"for {elapsed_hours} hours without resolution.\n"
                        f"Open: /admin/crm/inbox?conversation_id={conv.id}"
                    ),
                    status=NotificationStatus.delivered,
                    sent_at=datetime.now(UTC),
                )
            )
            alerted_resolution += 1

    if alerted_response or alerted_resolution:
        db.commit()

    return {
        "response_breaches": len(response_breaches),
        "resolution_breaches": len(resolution_breaches),
        "alerted_response": alerted_response,
        "alerted_resolution": alerted_resolution,
    }


def _resolve_notification_recipient(db: Session, conv: Conversation) -> str | None:
    """Resolve the email or identifier for the assigned agent."""
    from app.models.crm.conversation import ConversationAssignment
    from app.models.crm.team import CrmAgent
    from app.models.person import Person

    assignment = (
        db.query(ConversationAssignment)
        .filter(
            ConversationAssignment.conversation_id == conv.id,
            ConversationAssignment.is_active.is_(True),
        )
        .first()
    )
    if not assignment or not assignment.agent_id:
        return None
    agent = db.get(CrmAgent, assignment.agent_id)
    if not agent:
        return None
    person = db.get(Person, agent.person_id)
    if not person:
        return None
    return person.email
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
poetry run pytest tests/test_sla_breach.py -v -x
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/crm/inbox/sla.py tests/test_sla_breach.py
git commit -m "feat: add SLA breach detection service with per-priority targets"
```

---

## Task 6: Daily Data Quality Check Service

**Files:**
- Create: `app/services/crm/inbox/data_quality.py`
- Test: `tests/test_data_quality_check.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_data_quality_check.py`:

```python
"""Tests for conversation data quality checks."""

import uuid
from datetime import UTC, datetime, timedelta

from app.models.crm.conversation import Conversation, ConversationTag
from app.models.crm.enums import ConversationStatus
from app.models.person import Person


def _make_person(db) -> Person:
    person = Person(
        first_name="Test",
        last_name="User",
        display_name="Test User",
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        is_active=True,
    )
    db.add(person)
    db.flush()
    return person


class TestCheckDataQuality:
    def test_flags_resolved_without_first_response(self, db_session):
        """Resolved conversations missing first_response_at are flagged."""
        from app.services.crm.inbox.data_quality import check_data_quality

        person = _make_person(db_session)
        conv = Conversation(
            person_id=person.id,
            status=ConversationStatus.resolved,
            resolved_at=datetime.now(UTC) - timedelta(hours=2),
        )
        db_session.add(conv)
        db_session.commit()

        result = check_data_quality(db_session)
        assert result["missing_first_response"] >= 1

    def test_flags_resolved_without_tags(self, db_session):
        """Resolved conversations with no tags are flagged."""
        from app.services.crm.inbox.data_quality import check_data_quality

        person = _make_person(db_session)
        conv = Conversation(
            person_id=person.id,
            status=ConversationStatus.resolved,
            resolved_at=datetime.now(UTC) - timedelta(hours=2),
            first_response_at=datetime.now(UTC) - timedelta(hours=3),
        )
        db_session.add(conv)
        db_session.commit()

        result = check_data_quality(db_session)
        assert result["missing_tags"] >= 1

    def test_does_not_flag_tagged_conversation(self, db_session):
        """Resolved conversations with tags are not flagged for missing tags."""
        from app.services.crm.inbox.data_quality import check_data_quality

        person = _make_person(db_session)
        conv = Conversation(
            person_id=person.id,
            status=ConversationStatus.resolved,
            resolved_at=datetime.now(UTC) - timedelta(hours=2),
            first_response_at=datetime.now(UTC) - timedelta(hours=3),
        )
        db_session.add(conv)
        db_session.flush()
        tag = ConversationTag(conversation_id=conv.id, tag="support")
        db_session.add(tag)
        db_session.commit()

        result = check_data_quality(db_session)
        assert result["missing_tags"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
poetry run pytest tests/test_data_quality_check.py -v -x
```

Expected: ImportError — `app.services.crm.inbox.data_quality` does not exist yet.

- [ ] **Step 3: Implement data quality service**

Create `app/services/crm/inbox/data_quality.py`:

```python
"""Daily data quality checks for CRM conversations."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, ConversationTag
from app.models.crm.enums import ConversationStatus

logger = logging.getLogger(__name__)


def check_data_quality(db: Session, *, lookback_hours: int = 24) -> dict:
    """Check conversations resolved in the last N hours for missing data.

    Returns counts of conversations missing key fields.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)

    # Resolved conversations in the lookback window
    resolved_base = (
        db.query(Conversation)
        .filter(
            Conversation.status == ConversationStatus.resolved,
            Conversation.is_active.is_(True),
            Conversation.resolved_at >= cutoff,
        )
    )

    # Missing first_response_at
    missing_first_response = resolved_base.filter(
        Conversation.first_response_at.is_(None),
    ).count()

    # Missing tags — resolved conversations with zero tags
    tagged_conv_ids = (
        db.query(ConversationTag.conversation_id)
        .distinct()
        .subquery()
    )
    missing_tags = (
        resolved_base.filter(
            ~Conversation.id.in_(db.query(tagged_conv_ids.c.conversation_id)),
        ).count()
    )

    return {
        "missing_first_response": missing_first_response,
        "missing_tags": missing_tags,
        "lookback_hours": lookback_hours,
    }


def run_data_quality_check_and_notify(db: Session) -> dict:
    """Run data quality check and create in-app notification for team leads."""
    from app.models.notification import Notification, NotificationChannel, NotificationStatus

    result = check_data_quality(db)
    total_issues = result["missing_first_response"] + result["missing_tags"]

    if total_issues == 0:
        logger.info("DATA_QUALITY_CHECK_COMPLETE no_issues=true")
        return result

    parts = []
    if result["missing_first_response"] > 0:
        parts.append(f"{result['missing_first_response']} missing first response")
    if result["missing_tags"] > 0:
        parts.append(f"{result['missing_tags']} missing tags")

    summary = ", ".join(parts)
    body = (
        f"Conversations resolved in the last 24 hours with data gaps: {summary}.\n"
        f"Open: /admin/crm/inbox?status=resolved&missing=first_response,tags"
    )

    # Create a single summary notification (recipient is a system-wide admin address)
    db.add(
        Notification(
            channel=NotificationChannel.push,
            recipient="system:team_leads",
            subject=f"Data Quality: {total_issues} conversations with missing fields",
            body=body,
            status=NotificationStatus.delivered,
            sent_at=datetime.now(UTC),
        )
    )
    db.commit()

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
poetry run pytest tests/test_data_quality_check.py -v -x
```

Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/crm/inbox/data_quality.py tests/test_data_quality_check.py
git commit -m "feat: add daily data quality check service for CRM conversations"
```

---

## Task 7: Add Celery Tasks and Schedule Registration

**Files:**
- Modify: `app/tasks/crm_inbox.py`
- Modify: `app/services/scheduler_config.py`

- [ ] **Step 1: Add SLA breach check task to crm_inbox.py**

Append to `app/tasks/crm_inbox.py`:

```python
@celery_app.task(name="app.tasks.crm_inbox.check_sla_breaches")
def check_sla_breaches_task():
    """Check for SLA breaches and alert assigned agents."""
    import logging
    import time

    from app.metrics import observe_job

    logger = logging.getLogger(__name__)
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger.info("SLA_BREACH_CHECK_START")
    try:
        from app.services.crm.inbox.sla import check_and_alert_breaches

        result = check_and_alert_breaches(session)
        logger.info(
            "SLA_BREACH_CHECK_COMPLETE response_breaches=%s resolution_breaches=%s alerted=%s",
            result.get("response_breaches", 0),
            result.get("resolution_breaches", 0),
            result.get("alerted_response", 0) + result.get("alerted_resolution", 0),
        )
        return result
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        observe_job("check_sla_breaches", status, time.monotonic() - start)


@celery_app.task(name="app.tasks.crm_inbox.check_conversation_data_quality")
def check_conversation_data_quality_task():
    """Daily check for conversations with missing data fields."""
    import logging
    import time

    from app.metrics import observe_job

    logger = logging.getLogger(__name__)
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger.info("DATA_QUALITY_CHECK_START")
    try:
        from app.services.crm.inbox.data_quality import run_data_quality_check_and_notify

        result = run_data_quality_check_and_notify(session)
        logger.info(
            "DATA_QUALITY_CHECK_COMPLETE missing_first_response=%s missing_tags=%s",
            result.get("missing_first_response", 0),
            result.get("missing_tags", 0),
        )
        return result
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        observe_job("check_conversation_data_quality", status, time.monotonic() - start)
```

- [ ] **Step 2: Register tasks in scheduler_config.py**

In `app/services/scheduler_config.py`, after the existing CRM inbox task registrations (after the `crm_inbox_outbox_retention_cleanup` block around line 472), add:

```python
        # CRM inbox SLA breach check — every 15 minutes
        _sync_scheduled_task(
            session,
            name="crm_inbox_sla_breach_check",
            task_name="app.tasks.crm_inbox.check_sla_breaches",
            enabled=True,
            interval_seconds=900,
        )

        # CRM inbox data quality check — daily
        _sync_scheduled_task(
            session,
            name="crm_inbox_data_quality_check",
            task_name="app.tasks.crm_inbox.check_conversation_data_quality",
            enabled=True,
            interval_seconds=86400,
        )
```

- [ ] **Step 3: Verify tasks register without import errors**

Run:
```bash
poetry run python -c "from app.tasks.crm_inbox import check_sla_breaches_task, check_conversation_data_quality_task; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add app/tasks/crm_inbox.py app/services/scheduler_config.py
git commit -m "feat: add Celery tasks for SLA breach check and data quality monitoring"
```

---

## Task 8: Tag Nudge Modal on Resolve

**Files:**
- Create: `templates/admin/crm/_tag_nudge_modal.html`
- Modify: `app/web/admin/crm_inbox_status.py:130-142`

- [ ] **Step 1: Create the tag nudge modal template**

Create `templates/admin/crm/_tag_nudge_modal.html`:

```html
{# ── Tag Nudge Modal ─────────────────────────────────────────────
   Shown when resolving a conversation that has no tags.
   Soft nudge: agent can confirm or cancel.
   ────────────────────────────────────────────────────────────── #}

<div class="flex flex-col items-center justify-center min-h-[40vh] px-6 py-12 animate-fade-in-up">
    <div class="w-full max-w-sm space-y-5">
        <div class="text-center space-y-2">
            <div class="mx-auto w-12 h-12 rounded-xl bg-amber-500/10 flex items-center justify-center border border-amber-500/20 dark:bg-amber-500/15 dark:border-amber-400/20">
                <svg class="w-6 h-6 text-amber-500 dark:text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A2 2 0 013 12V7a4 4 0 014-4z"/>
                </svg>
            </div>
            <h3 class="text-base font-semibold text-slate-900 dark:text-white font-display">No tags on this conversation</h3>
            <p class="text-xs text-slate-500 dark:text-slate-400">Adding tags helps with reporting and analytics. Are you sure you want to resolve without tagging?</p>
        </div>

        {# Resolve Anyway #}
        <button
            hx-post="/admin/crm/inbox/conversation/{{ conversation_id }}/status?new_status=resolved&skip_tag_check=1"
            hx-target="#message-thread"
            hx-swap="innerHTML"
            hx-on::after-request="htmx.ajax('GET', '/admin/crm/inbox/conversations', {target:'#conversation-list', swap:'innerHTML'})"
            class="w-full rounded-xl bg-amber-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-amber-500 transition-colors focus:ring-2 focus:ring-amber-500/20 focus:outline-none dark:bg-amber-500 dark:hover:bg-amber-400">
            Resolve Without Tags
        </button>

        {# Cancel #}
        <a hx-get="/admin/crm/inbox/conversation/{{ conversation_id }}"
           hx-target="#message-thread"
           hx-swap="innerHTML"
           class="block w-full text-center rounded-xl border border-slate-200/60 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50 cursor-pointer transition-colors dark:border-slate-700/40 dark:bg-slate-800/30 dark:text-slate-300 dark:hover:bg-slate-800/50">
            Cancel &mdash; Add Tags First
        </a>
    </div>
</div>
```

- [ ] **Step 2: Integrate tag check into the resolve route**

In `app/web/admin/crm_inbox_status.py`, modify the `update_conversation_status` route. After the existing resolve gate check (line 130-142), add a tag nudge check. The modified section should look like:

```python
    if new_status == "resolved" and request.headers.get("HX-Target") == "message-thread":
        skip_tag_check = request.query_params.get("skip_tag_check") == "1"

        # Tag nudge: soft warning if no tags
        if not skip_tag_check:
            from app.models.crm.conversation import ConversationTag
            from app.services.common import coerce_uuid

            tag_count = (
                db.query(ConversationTag)
                .filter(ConversationTag.conversation_id == coerce_uuid(conversation_id))
                .count()
            )
            if tag_count == 0:
                return templates.TemplateResponse(
                    "admin/crm/_tag_nudge_modal.html",
                    {
                        "request": request,
                        "conversation_id": conversation_id,
                        "csrf_token": get_csrf_token(request),
                    },
                )

        from app.services.crm.inbox.resolve_gate import check_resolve_gate

        gate = check_resolve_gate(db, conversation_id)
        if gate.kind == "needs_gate":
            return templates.TemplateResponse(
                "admin/crm/_resolve_gate.html",
                {
                    "request": request,
                    "conversation_id": conversation_id,
                    "csrf_token": get_csrf_token(request),
                },
            )
```

- [ ] **Step 3: Run existing tests to verify no regressions**

Run:
```bash
poetry run pytest tests/ -x -q --timeout=120
```

Expected: No failures.

- [ ] **Step 4: Commit**

```bash
git add templates/admin/crm/_tag_nudge_modal.html app/web/admin/crm_inbox_status.py
git commit -m "feat: add soft tag nudge modal before resolving untagged conversations"
```

---

## Task 9: Fix CSAT Survey Flow

**Files:**
- Modify: `app/services/crm/inbox/csat.py:152-167`

- [ ] **Step 1: Investigate the CSAT survey picker**

The `_pick_active_survey()` function (csat.py:152-167) prioritizes surveys with `trigger_type == SurveyTriggerType.manual`. For conversation-resolved CSAT, it should also match `ticket_closed` trigger type. Read `app/models/comms.py` to check the `SurveyTriggerType` enum values.

- [ ] **Step 2: Fix the survey picker to match conversation resolution**

In `app/services/crm/inbox/csat.py`, modify `_pick_active_survey()` to also look for a `ticket_closed` trigger type before falling back to manual:

```python
def _pick_active_survey(db: Session) -> Survey | None:
    # Prefer a survey explicitly configured for ticket/conversation closure
    survey = (
        db.query(Survey)
        .filter(Survey.is_active.is_(True), Survey.status == CustomerSurveyStatus.active)
        .filter(Survey.trigger_type == SurveyTriggerType.ticket_closed)
        .order_by(Survey.updated_at.desc())
        .first()
    )
    if survey:
        return survey
    # Fall back to manual trigger type
    survey = (
        db.query(Survey)
        .filter(Survey.is_active.is_(True), Survey.status == CustomerSurveyStatus.active)
        .filter(Survey.trigger_type == SurveyTriggerType.manual)
        .order_by(Survey.updated_at.desc())
        .first()
    )
    if survey:
        return survey
    # Last resort: any active survey
    return (
        db.query(Survey)
        .filter(Survey.is_active.is_(True), Survey.status == CustomerSurveyStatus.active)
        .order_by(Survey.updated_at.desc())
        .first()
    )
```

- [ ] **Step 3: Improve the inline CSAT message body**

In `queue_for_resolved_conversation()` (line 239), the message body is a plain URL. Make it more engaging and channel-aware. Replace the `outbound_payload` construction (lines 234-240):

```python
        survey_url = _resolve_survey_link(db, invitation.token)

        if channel_type == ChannelType.email:
            csat_body = (
                "Your conversation has been resolved. We'd love to hear how we did!\n\n"
                f"Please take a moment to rate your experience: {survey_url}\n\n"
                "Your feedback helps us improve our service. Thank you!"
            )
        else:
            csat_body = (
                "Your conversation has been resolved. "
                f"How was your experience? Rate us here: {survey_url}"
            )

        outbound_payload = InboxSendRequest(
            conversation_id=conversation.id,
            channel_type=channel_type,
            channel_target_id=coerce_uuid(target_id) if target_id else None,
            reply_to_message_id=last_inbound.id if last_inbound else None,
            body=csat_body,
        )
```

- [ ] **Step 4: Run existing tests**

Run:
```bash
poetry run pytest tests/ -x -q --timeout=120
```

Expected: No failures.

- [ ] **Step 5: Commit**

```bash
git add app/services/crm/inbox/csat.py
git commit -m "fix: improve CSAT survey picker priority and inline message content"
```

---

## Task 10: Add Missing Fields Filter to Inbox Queries

**Files:**
- Modify: `app/services/crm/inbox/queries.py`
- Modify: `app/web/admin/crm_inbox_conversations.py`

- [ ] **Step 1: Add `missing` filter to list_inbox_conversations**

In `app/services/crm/inbox/queries.py`, find the `list_inbox_conversations()` function signature and add a `missing: str | None = None` parameter. Then add filtering logic after the existing filters:

```python
    # Missing data filter for data quality alerts
    if missing:
        missing_fields = [f.strip() for f in missing.split(",")]
        if "first_response" in missing_fields:
            query = query.filter(Conversation.first_response_at.is_(None))
        if "tags" in missing_fields:
            from app.models.crm.conversation import ConversationTag

            tagged_ids = db.query(ConversationTag.conversation_id).distinct().subquery()
            query = query.filter(~Conversation.id.in_(db.query(tagged_ids.c.conversation_id)))
```

- [ ] **Step 2: Pass `missing` parameter from the route**

In `app/web/admin/crm_inbox_conversations.py`, in the `inbox_conversations_partial` route, add `missing` as a query parameter and pass it through to the query function.

- [ ] **Step 3: Run existing tests**

Run:
```bash
poetry run pytest tests/ -x -q --timeout=120
```

Expected: No failures.

- [ ] **Step 4: Commit**

```bash
git add app/services/crm/inbox/queries.py app/web/admin/crm_inbox_conversations.py
git commit -m "feat: add missing-field filter to inbox conversation list for data quality links"
```

---

## Task 11: Agent Weekly Performance Report Service

**Files:**
- Test: `tests/test_agent_performance_report.py`
- Modify: `app/services/crm/reports.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_agent_performance_report.py`:

```python
"""Tests for weekly agent performance report."""

import uuid
from datetime import UTC, datetime, timedelta

from app.models.crm.conversation import Conversation, ConversationAssignment
from app.models.crm.enums import ConversationPriority, ConversationStatus
from app.models.crm.team import CrmAgent
from app.models.person import Person


def _make_person(db, name="Test User") -> Person:
    person = Person(
        first_name=name.split()[0],
        last_name=name.split()[-1],
        display_name=name,
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        is_active=True,
    )
    db.add(person)
    db.flush()
    return person


def _make_agent(db, person_id) -> CrmAgent:
    agent = CrmAgent(person_id=person_id, is_active=True)
    db.add(agent)
    db.flush()
    return agent


class TestAgentWeeklyPerformance:
    def test_returns_metrics_per_agent(self, db_session):
        """Returns a list of agent metric dicts with expected keys."""
        from app.services.crm.reports import agent_weekly_performance

        agent_person = _make_person(db_session, name="Agent One")
        agent = _make_agent(db_session, agent_person.id)
        customer = _make_person(db_session, name="Customer One")

        now = datetime.now(UTC)
        conv = Conversation(
            person_id=customer.id,
            status=ConversationStatus.resolved,
            priority=ConversationPriority.medium,
            first_response_at=now - timedelta(hours=2),
            resolved_at=now - timedelta(hours=1),
            response_time_seconds=3600,
            resolution_time_seconds=7200,
        )
        db_session.add(conv)
        db_session.flush()

        assignment = ConversationAssignment(
            conversation_id=conv.id,
            agent_id=agent.id,
            is_active=True,
            assigned_at=now - timedelta(hours=3),
        )
        db_session.add(assignment)
        db_session.commit()

        start = now - timedelta(days=7)
        result = agent_weekly_performance(db_session, start_at=start, end_at=now)

        assert len(result) >= 1
        agent_row = next((r for r in result if r["agent_id"] == str(agent.id)), None)
        assert agent_row is not None
        assert agent_row["resolved_count"] == 1
        assert "median_response_seconds" in agent_row
        assert "median_resolution_seconds" in agent_row
        assert "open_backlog" in agent_row
        assert "sla_breach_count" in agent_row

    def test_empty_when_no_agents(self, db_session):
        """Returns empty list when no agents exist."""
        from app.services.crm.reports import agent_weekly_performance

        now = datetime.now(UTC)
        result = agent_weekly_performance(db_session, start_at=now - timedelta(days=7), end_at=now)
        assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
poetry run pytest tests/test_agent_performance_report.py -v -x
```

Expected: ImportError or AttributeError — `agent_weekly_performance` does not exist.

- [ ] **Step 3: Implement agent_weekly_performance()**

In `app/services/crm/reports.py`, add this function after the existing `agent_performance_metrics()`:

```python
def agent_weekly_performance(
    db: Session,
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
    """Compute weekly performance metrics per agent.

    Returns a list of dicts with: agent_id, agent_name, resolved_count,
    median_response_seconds, median_resolution_seconds, open_backlog,
    csat_avg, sla_breach_count.
    """
    import statistics

    from app.models.comms import SurveyInvitation, SurveyResponse

    agents = db.query(CrmAgent).filter(CrmAgent.is_active.is_(True)).limit(200).all()
    if not agents:
        return []

    person_ids = [a.person_id for a in agents if a.person_id]
    persons = db.query(Person).filter(Person.id.in_(person_ids)).all() if person_ids else []
    person_map = {p.id: p for p in persons}

    # Load SLA targets for breach counting
    from app.services.crm.inbox.sla import get_sla_targets

    sla_targets = get_sla_targets(db)

    results = []
    for agent in agents:
        person = person_map.get(agent.person_id)
        agent_name = person.display_name if person else "Unknown"

        # Conversations assigned to this agent that were resolved in the period
        assigned_conv_ids = [
            row[0]
            for row in db.query(ConversationAssignment.conversation_id)
            .filter(ConversationAssignment.agent_id == agent.id)
            .all()
        ]

        if not assigned_conv_ids:
            results.append({
                "agent_id": str(agent.id),
                "agent_name": agent_name,
                "resolved_count": 0,
                "median_response_seconds": None,
                "median_resolution_seconds": None,
                "open_backlog": 0,
                "csat_avg": None,
                "sla_breach_count": 0,
            })
            continue

        resolved_convs = (
            db.query(Conversation)
            .filter(
                Conversation.id.in_(assigned_conv_ids),
                Conversation.status == ConversationStatus.resolved,
                Conversation.resolved_at >= start_at,
                Conversation.resolved_at <= end_at,
            )
            .all()
        )

        response_times = [c.response_time_seconds for c in resolved_convs if c.response_time_seconds is not None]
        resolution_times = [c.resolution_time_seconds for c in resolved_convs if c.resolution_time_seconds is not None]

        # Open backlog: assigned and still open/pending
        open_backlog = (
            db.query(Conversation)
            .filter(
                Conversation.id.in_(assigned_conv_ids),
                Conversation.status.in_([ConversationStatus.open, ConversationStatus.pending]),
                Conversation.is_active.is_(True),
            )
            .count()
        )

        # CSAT average from survey responses linked to this agent's resolved conversations
        resolved_conv_ids = [c.id for c in resolved_convs]
        csat_avg = None
        if resolved_conv_ids:
            # Find person_ids for resolved conversations, then match survey responses
            conv_person_ids = [c.person_id for c in resolved_convs]
            ratings = (
                db.query(SurveyResponse.rating)
                .join(SurveyInvitation, SurveyInvitation.id == SurveyResponse.invitation_id)
                .filter(
                    SurveyInvitation.person_id.in_(conv_person_ids),
                    SurveyResponse.rating.isnot(None),
                    SurveyResponse.completed_at >= start_at,
                    SurveyResponse.completed_at <= end_at,
                )
                .all()
            )
            rating_values = [r[0] for r in ratings if r[0] is not None]
            if rating_values:
                csat_avg = round(sum(rating_values) / len(rating_values), 2)

        # SLA breach count
        breach_count = 0
        for conv in resolved_convs:
            priority = conv.priority.value if conv.priority else "none"
            resp_target = sla_targets["response"].get(priority, 1440)
            res_target = sla_targets["resolution"].get(priority, 4320)
            if conv.response_time_seconds and conv.response_time_seconds > resp_target * 60:
                breach_count += 1
            if conv.resolution_time_seconds and conv.resolution_time_seconds > res_target * 60:
                breach_count += 1

        results.append({
            "agent_id": str(agent.id),
            "agent_name": agent_name,
            "resolved_count": len(resolved_convs),
            "median_response_seconds": int(statistics.median(response_times)) if response_times else None,
            "median_resolution_seconds": int(statistics.median(resolution_times)) if resolution_times else None,
            "open_backlog": open_backlog,
            "csat_avg": csat_avg,
            "sla_breach_count": breach_count,
        })

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
poetry run pytest tests/test_agent_performance_report.py -v -x
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/crm/reports.py tests/test_agent_performance_report.py
git commit -m "feat: add agent_weekly_performance service function for performance report"
```

---

## Task 12: Agent Performance Report Route and Template

**Files:**
- Modify: `app/web/admin/reports.py`
- Create: `templates/admin/reports/agent_performance.html`

- [ ] **Step 1: Add the report route**

In `app/web/admin/reports.py`, add after the existing CRM performance report route:

```python
@router.get("/agent-performance", response_class=HTMLResponse)
def agent_performance_report(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(7, ge=7, le=90),
):
    """Weekly agent performance report with trend comparisons."""
    user = get_current_user(request)
    now = datetime.now(UTC)
    current_start = now - timedelta(days=days)
    previous_start = current_start - timedelta(days=days)
    previous_end = current_start

    current_metrics = crm_reports_service.agent_weekly_performance(
        db, start_at=current_start, end_at=now,
    )
    previous_metrics = crm_reports_service.agent_weekly_performance(
        db, start_at=previous_start, end_at=previous_end,
    )

    # Build previous-period lookup for trend calculation
    prev_map = {m["agent_id"]: m for m in previous_metrics}

    # Calculate trends and medians for threshold flagging
    all_resolved = [m["resolved_count"] for m in current_metrics]
    team_median_resolved = sorted(all_resolved)[len(all_resolved) // 2] if all_resolved else 0

    for m in current_metrics:
        prev = prev_map.get(m["agent_id"], {})
        m["prev_resolved_count"] = prev.get("resolved_count", 0)
        m["prev_median_response_seconds"] = prev.get("median_response_seconds")
        m["prev_median_resolution_seconds"] = prev.get("median_resolution_seconds")
        m["prev_open_backlog"] = prev.get("open_backlog", 0)
        m["prev_csat_avg"] = prev.get("csat_avg")
        m["prev_sla_breach_count"] = prev.get("sla_breach_count", 0)
        m["below_median"] = m["resolved_count"] < team_median_resolved

    return templates.TemplateResponse(
        "admin/reports/agent_performance.html",
        {
            "request": request,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "agent-performance",
            "active_menu": "reports",
            "days": days,
            "agents": current_metrics,
            "team_median_resolved": team_median_resolved,
        },
    )
```

- [ ] **Step 2: Create the report template**

Create `templates/admin/reports/agent_performance.html`:

```html
{% extends "layouts/admin.html" %}
{% from "components/ui/macros.html" import page_header, stats_card, data_table, table_head, table_row %}

{% block title %}Agent Performance - Admin{% endblock %}

{% block content %}
<div class="space-y-6">
    <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
            <h1 class="text-2xl font-bold text-slate-900 dark:text-white font-display">Agent Performance</h1>
            <p class="mt-1 text-sm text-slate-500 dark:text-slate-400">Weekly metrics with trend comparison</p>
        </div>
        <form method="GET" action="/admin/reports/agent-performance" class="flex items-center gap-2">
            <select name="days" class="rounded-xl border border-slate-300 px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-700 dark:text-white">
                <option value="7" {% if days == 7 %}selected{% endif %}>Last 7 Days</option>
                <option value="14" {% if days == 14 %}selected{% endif %}>Last 14 Days</option>
                <option value="30" {% if days == 30 %}selected{% endif %}>Last 30 Days</option>
            </select>
            <button type="submit" class="inline-flex items-center gap-2 rounded-xl bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 transition-colors">
                <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z"/></svg>
                Filter
            </button>
        </form>
    </div>

    {# Summary Cards #}
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {% set total_resolved = agents | map(attribute='resolved_count') | sum %}
        {% set total_backlog = agents | map(attribute='open_backlog') | sum %}
        {% set total_breaches = agents | map(attribute='sla_breach_count') | sum %}
        {% set csat_values = agents | selectattr('csat_avg') | map(attribute='csat_avg') | list %}

        {{ stats_card("Conversations Resolved", total_resolved, color="emerald") }}
        {{ stats_card("Open Backlog", total_backlog, color="amber") }}
        {{ stats_card("SLA Breaches", total_breaches, color="rose") }}
        {{ stats_card("Avg CSAT", "%.1f" | format(csat_values | sum / csat_values | length) if csat_values else "N/A", color="cyan") }}
    </div>

    {# Agent Table #}
    <div class="rounded-2xl border border-slate-200/60 bg-white dark:border-slate-700/60 dark:bg-slate-800 overflow-x-auto">
        <table class="min-w-full divide-y divide-slate-200 dark:divide-slate-700">
            <thead class="bg-slate-50 dark:bg-slate-900/50">
                <tr>
                    <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Agent</th>
                    <th class="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Resolved</th>
                    <th class="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Med. Response</th>
                    <th class="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Med. Resolution</th>
                    <th class="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Backlog</th>
                    <th class="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">CSAT</th>
                    <th class="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">SLA Breaches</th>
                </tr>
            </thead>
            <tbody class="divide-y divide-slate-100 dark:divide-slate-700/50">
                {% for agent in agents %}
                <tr class="{{ 'bg-red-50/50 dark:bg-red-900/10' if agent.below_median else '' }}">
                    <td class="px-4 py-3 text-sm font-medium text-slate-900 dark:text-white">
                        {{ agent.agent_name }}
                        {% if agent.below_median %}
                        <span class="ml-1 inline-flex items-center rounded-lg bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-900/30 dark:text-amber-400">Below median</span>
                        {% endif %}
                    </td>
                    <td class="px-4 py-3 text-sm text-right text-slate-700 dark:text-slate-300">
                        {{ agent.resolved_count }}
                        {% if agent.prev_resolved_count is not none %}
                            {% if agent.resolved_count > agent.prev_resolved_count %}
                                <span class="text-emerald-500 text-xs ml-1">&#9650;</span>
                            {% elif agent.resolved_count < agent.prev_resolved_count %}
                                <span class="text-red-500 text-xs ml-1">&#9660;</span>
                            {% endif %}
                        {% endif %}
                    </td>
                    <td class="px-4 py-3 text-sm text-right text-slate-700 dark:text-slate-300">
                        {% if agent.median_response_seconds is not none %}
                            {{ "%.1f" | format(agent.median_response_seconds / 60) }}m
                            {% if agent.prev_median_response_seconds is not none %}
                                {% if agent.median_response_seconds < agent.prev_median_response_seconds %}
                                    <span class="text-emerald-500 text-xs ml-1">&#9650;</span>
                                {% elif agent.median_response_seconds > agent.prev_median_response_seconds %}
                                    <span class="text-red-500 text-xs ml-1">&#9660;</span>
                                {% endif %}
                            {% endif %}
                        {% else %}
                            <span class="text-slate-400">—</span>
                        {% endif %}
                    </td>
                    <td class="px-4 py-3 text-sm text-right text-slate-700 dark:text-slate-300">
                        {% if agent.median_resolution_seconds is not none %}
                            {{ "%.1f" | format(agent.median_resolution_seconds / 3600) }}h
                            {% if agent.prev_median_resolution_seconds is not none %}
                                {% if agent.median_resolution_seconds < agent.prev_median_resolution_seconds %}
                                    <span class="text-emerald-500 text-xs ml-1">&#9650;</span>
                                {% elif agent.median_resolution_seconds > agent.prev_median_resolution_seconds %}
                                    <span class="text-red-500 text-xs ml-1">&#9660;</span>
                                {% endif %}
                            {% endif %}
                        {% else %}
                            <span class="text-slate-400">—</span>
                        {% endif %}
                    </td>
                    <td class="px-4 py-3 text-sm text-right text-slate-700 dark:text-slate-300">
                        {{ agent.open_backlog }}
                        {% if agent.prev_open_backlog is not none %}
                            {% if agent.open_backlog < agent.prev_open_backlog %}
                                <span class="text-emerald-500 text-xs ml-1">&#9650;</span>
                            {% elif agent.open_backlog > agent.prev_open_backlog %}
                                <span class="text-red-500 text-xs ml-1">&#9660;</span>
                            {% endif %}
                        {% endif %}
                    </td>
                    <td class="px-4 py-3 text-sm text-right text-slate-700 dark:text-slate-300">
                        {% if agent.csat_avg is not none %}
                            {{ "%.1f" | format(agent.csat_avg) }}
                            {% if agent.prev_csat_avg is not none %}
                                {% if agent.csat_avg > agent.prev_csat_avg %}
                                    <span class="text-emerald-500 text-xs ml-1">&#9650;</span>
                                {% elif agent.csat_avg < agent.prev_csat_avg %}
                                    <span class="text-red-500 text-xs ml-1">&#9660;</span>
                                {% endif %}
                            {% endif %}
                        {% else %}
                            <span class="text-slate-400">—</span>
                        {% endif %}
                    </td>
                    <td class="px-4 py-3 text-sm text-right text-slate-700 dark:text-slate-300">
                        {{ agent.sla_breach_count }}
                        {% if agent.prev_sla_breach_count is not none %}
                            {% if agent.sla_breach_count < agent.prev_sla_breach_count %}
                                <span class="text-emerald-500 text-xs ml-1">&#9650;</span>
                            {% elif agent.sla_breach_count > agent.prev_sla_breach_count %}
                                <span class="text-red-500 text-xs ml-1">&#9660;</span>
                            {% endif %}
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
                {% if not agents %}
                <tr>
                    <td colspan="7" class="px-4 py-8 text-center text-sm text-slate-500 dark:text-slate-400">No agent data for this period.</td>
                </tr>
                {% endif %}
            </tbody>
        </table>
    </div>
</div>
{% endblock %}
```

- [ ] **Step 3: Run existing tests to check for regressions**

Run:
```bash
poetry run pytest tests/ -x -q --timeout=120
```

Expected: No failures.

- [ ] **Step 4: Commit**

```bash
git add app/web/admin/reports.py templates/admin/reports/agent_performance.html
git commit -m "feat: add agent performance report page with weekly trends and SLA tracking"
```

---

## Task 13: Backfill Migration Script

**Files:**
- Create: `scripts/backfill_conversation_metrics.py`

- [ ] **Step 1: Write backfill script**

Create `scripts/backfill_conversation_metrics.py`:

```python
"""One-time backfill for conversation metric fields.

Populates first_response_at and resolved_at from existing message and status data.

Usage:
    poetry run python scripts/backfill_conversation_metrics.py [--dry-run]
"""

import sys
from datetime import UTC, datetime

from sqlalchemy import func

from app.db import SessionLocal
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ConversationStatus, MessageDirection
from app.models.crm.team import CrmAgent


def backfill(dry_run: bool = False) -> dict:
    db = SessionLocal()
    stats = {"first_response_filled": 0, "resolved_at_filled": 0, "errors": []}

    try:
        # 1. Backfill first_response_at
        # Find conversations missing first_response_at
        convs_missing_frt = (
            db.query(Conversation)
            .filter(Conversation.first_response_at.is_(None), Conversation.is_active.is_(True))
            .all()
        )

        # Build set of all agent person_ids
        agent_person_ids = set(
            row[0] for row in db.query(CrmAgent.person_id).filter(CrmAgent.is_active.is_(True)).all()
        )

        for conv in convs_missing_frt:
            # Find earliest outbound message by an agent
            first_agent_msg = (
                db.query(Message)
                .filter(
                    Message.conversation_id == conv.id,
                    Message.direction == MessageDirection.outbound,
                    Message.author_id.in_(agent_person_ids),
                )
                .order_by(Message.created_at.asc())
                .first()
            )
            if first_agent_msg:
                timestamp = first_agent_msg.sent_at or first_agent_msg.created_at
                conv.first_response_at = timestamp
                conv.response_time_seconds = int((timestamp - conv.created_at).total_seconds())
                stats["first_response_filled"] += 1

        # 2. Backfill resolved_at
        convs_missing_resolved = (
            db.query(Conversation)
            .filter(
                Conversation.status == ConversationStatus.resolved,
                Conversation.resolved_at.is_(None),
                Conversation.is_active.is_(True),
            )
            .all()
        )

        for conv in convs_missing_resolved:
            # Use updated_at as best approximation
            conv.resolved_at = conv.updated_at
            conv.resolution_time_seconds = int((conv.updated_at - conv.created_at).total_seconds())
            stats["resolved_at_filled"] += 1

        if dry_run:
            print(f"DRY RUN — would fill {stats['first_response_filled']} first_response_at, "
                  f"{stats['resolved_at_filled']} resolved_at")
            db.rollback()
        else:
            db.commit()
            print(f"Backfilled {stats['first_response_filled']} first_response_at, "
                  f"{stats['resolved_at_filled']} resolved_at")

    except Exception as exc:
        db.rollback()
        stats["errors"].append(str(exc))
        print(f"ERROR: {exc}")
        raise
    finally:
        db.close()

    return stats


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    backfill(dry_run=dry_run)
```

- [ ] **Step 2: Verify script loads without errors**

Run:
```bash
poetry run python -c "from scripts.backfill_conversation_metrics import backfill; print('OK')"
```

Expected: `OK` (or an import path adjustment needed).

- [ ] **Step 3: Commit**

```bash
git add scripts/backfill_conversation_metrics.py
git commit -m "feat: add one-time backfill script for conversation metric fields"
```

---

## Task 14: Final Integration Test

**Files:**
- All files from previous tasks

- [ ] **Step 1: Run the full test suite**

Run:
```bash
poetry run pytest tests/ -x -q --timeout=120
```

Expected: All tests pass.

- [ ] **Step 2: Run linting**

Run:
```bash
poetry run ruff check app/ --fix
```

Expected: No errors (or only auto-fixed).

- [ ] **Step 3: Run type checking**

Run:
```bash
poetry run mypy app/models/crm/conversation.py app/services/crm/inbox/sla.py app/services/crm/inbox/data_quality.py app/services/crm/reports.py
```

Expected: No critical errors.

- [ ] **Step 4: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: address lint and type check issues from conversation metrics feature"
```
