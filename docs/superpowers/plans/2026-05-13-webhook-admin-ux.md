# Webhook Admin UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-05-13-webhook-admin-ux-design.md`

**Goal:** Consolidate webhook admin under `/admin/integrations/webhooks` and add subscription editor, delivery log with replay, test-fire, secret rotation, and health panel. Remove the duplicate `/admin/system/webhooks*` surface.

**Architecture:** Service-only additions to the existing `WebhookEndpoints` / `WebhookDeliveries` managers (rotate_secret, send_test, replay, list_with_stats, list_filtered). New routes in `app/web/admin/integrations.py`. New templates under `templates/admin/integrations/webhooks/`. Old `/admin/system/webhooks*` routes become 308 redirects. No DB migration. All state-changing actions emit `AuditEvent` via `log_audit_event`.

**Tech Stack:** FastAPI · SQLAlchemy 2.0 · Jinja2 + HTMX · Celery · PostgreSQL · `httpx` · `pytest` · Playwright.

---

## File Structure (locked in before tasks)

**New files**
- `app/services/webhook_events.py` — `WEBHOOK_EVENT_DESCRIPTIONS: dict[WebhookEventType, str]` + `EVENT_GROUPS` ordered grouping for the picker.
- `templates/admin/integrations/webhooks/edit.html` — endpoint edit form.
- `templates/admin/integrations/webhooks/deliveries.html` — paginated delivery log with filters.
- `templates/admin/integrations/webhooks/rotate_success.html` — one-time secret reveal.
- `templates/admin/integrations/webhooks/_subscription_picker.html` — partial used inside detail page.
- `tests/test_webhook_admin_services.py` — service-layer tests.
- `tests/test_webhook_admin_web.py` — route tests.
- `tests/playwright/e2e/test_webhook_admin.py` — happy-path E2E.

**Modified files**
- `app/services/webhook.py` — append `rotate_secret`, `send_test`, `list_with_stats` to `WebhookEndpoints`; `replay`, `list_filtered` to `WebhookDeliveries`; add `EndpointStats` dataclass.
- `app/web/admin/integrations.py` — add edit/rotate/test/subscription add+remove/deliveries/replay routes.
- `app/web/admin/system.py` — replace the six `/admin/system/webhooks*` route bodies with 308 redirects.
- `templates/admin/integrations/webhooks/index.html` — add health panel + status badge columns.
- `templates/admin/integrations/webhooks/detail.html` — action row + subscription editor + recent-deliveries link.

**Deleted files**
- `templates/admin/system/webhooks.html` — no longer needed (route now 308 redirects).
- `templates/admin/system/webhook_form.html` — same.

---

## Implementation Order

Tasks are ordered so each leaves the tree green and committable. TDD where it makes sense (service helpers, route handlers); template tasks pair with their service/route prerequisites.

---

### Task 1: WEBHOOK_EVENT_DESCRIPTIONS lookup

**Files:**
- Create: `app/services/webhook_events.py`
- Test: `tests/test_webhook_admin_services.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_webhook_admin_services.py
from app.models.webhook import WebhookEventType
from app.services.webhook_events import EVENT_GROUPS, WEBHOOK_EVENT_DESCRIPTIONS


def test_every_event_type_has_a_description():
    missing = [et for et in WebhookEventType if et not in WEBHOOK_EVENT_DESCRIPTIONS]
    assert missing == [], f"Missing descriptions for: {missing}"


def test_event_groups_cover_every_event_type():
    grouped = {et for _label, items in EVENT_GROUPS for et in items}
    assert grouped == set(WebhookEventType)


def test_event_groups_preserve_order():
    labels = [label for label, _items in EVENT_GROUPS]
    assert labels[0] == "Subscriber"
    assert labels[-1] == "Custom"
```

- [ ] **Step 2: Run test to verify it fails**

```
poetry run pytest tests/test_webhook_admin_services.py -x -q
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.webhook_events'`.

- [ ] **Step 3: Implement the module**

```python
# app/services/webhook_events.py
"""Human-friendly descriptions and grouping for WebhookEventType.

Used by the admin subscription picker so admins see what each event means
without consulting source code.
"""

from app.models.webhook import WebhookEventType

WEBHOOK_EVENT_DESCRIPTIONS: dict[WebhookEventType, str] = {
    WebhookEventType.subscriber_created: "New subscriber record created.",
    WebhookEventType.subscriber_updated: "Subscriber profile fields changed.",
    WebhookEventType.subscriber_suspended: "Subscriber marked as suspended.",
    WebhookEventType.subscriber_reactivated: "Suspended subscriber re-activated.",
    WebhookEventType.subscription_created: "A subscription record was created.",
    WebhookEventType.subscription_activated: "A subscription went live.",
    WebhookEventType.subscription_suspended: "A subscription was paused.",
    WebhookEventType.subscription_resumed: "A paused subscription resumed.",
    WebhookEventType.subscription_canceled: "A subscription was cancelled.",
    WebhookEventType.subscription_upgraded: "A subscription moved to a higher tier.",
    WebhookEventType.subscription_downgraded: "A subscription moved to a lower tier.",
    WebhookEventType.subscription_expiring: "A subscription is approaching its end date.",
    WebhookEventType.invoice_created: "A new invoice was issued.",
    WebhookEventType.invoice_sent: "An invoice was emailed to the customer.",
    WebhookEventType.invoice_paid: "An invoice has been settled.",
    WebhookEventType.invoice_overdue: "An invoice is past its due date.",
    WebhookEventType.payment_received: "A payment was successfully captured.",
    WebhookEventType.payment_failed: "A payment attempt failed.",
    WebhookEventType.payment_refunded: "A payment was refunded.",
    WebhookEventType.usage_recorded: "A usage sample was ingested.",
    WebhookEventType.usage_warning: "Usage crossed a warning threshold.",
    WebhookEventType.usage_exhausted: "A usage allowance is depleted.",
    WebhookEventType.usage_topped_up: "Usage allowance was topped up.",
    WebhookEventType.provisioning_started: "Service provisioning started.",
    WebhookEventType.provisioning_completed: "Service provisioning finished.",
    WebhookEventType.provisioning_failed: "Service provisioning errored.",
    WebhookEventType.device_offline: "A monitored device went offline.",
    WebhookEventType.device_online: "A monitored device came online.",
    WebhookEventType.session_started: "A network session began.",
    WebhookEventType.session_ended: "A network session ended.",
    WebhookEventType.network_alert: "A network monitoring alert fired.",
    WebhookEventType.ticket_created: "A new support ticket was opened.",
    WebhookEventType.ticket_escalated: "A ticket was escalated.",
    WebhookEventType.ticket_resolved: "A ticket was resolved.",
    WebhookEventType.custom: "Synthetic / test event payload.",
}

EVENT_GROUPS: list[tuple[str, list[WebhookEventType]]] = [
    (
        "Subscriber",
        [
            WebhookEventType.subscriber_created,
            WebhookEventType.subscriber_updated,
            WebhookEventType.subscriber_suspended,
            WebhookEventType.subscriber_reactivated,
        ],
    ),
    (
        "Subscription",
        [
            WebhookEventType.subscription_created,
            WebhookEventType.subscription_activated,
            WebhookEventType.subscription_suspended,
            WebhookEventType.subscription_resumed,
            WebhookEventType.subscription_canceled,
            WebhookEventType.subscription_upgraded,
            WebhookEventType.subscription_downgraded,
            WebhookEventType.subscription_expiring,
        ],
    ),
    (
        "Invoice",
        [
            WebhookEventType.invoice_created,
            WebhookEventType.invoice_sent,
            WebhookEventType.invoice_paid,
            WebhookEventType.invoice_overdue,
        ],
    ),
    (
        "Payment",
        [
            WebhookEventType.payment_received,
            WebhookEventType.payment_failed,
            WebhookEventType.payment_refunded,
        ],
    ),
    (
        "Usage",
        [
            WebhookEventType.usage_recorded,
            WebhookEventType.usage_warning,
            WebhookEventType.usage_exhausted,
            WebhookEventType.usage_topped_up,
        ],
    ),
    (
        "Provisioning",
        [
            WebhookEventType.provisioning_started,
            WebhookEventType.provisioning_completed,
            WebhookEventType.provisioning_failed,
        ],
    ),
    (
        "Network",
        [
            WebhookEventType.device_offline,
            WebhookEventType.device_online,
            WebhookEventType.session_started,
            WebhookEventType.session_ended,
            WebhookEventType.network_alert,
        ],
    ),
    (
        "Support",
        [
            WebhookEventType.ticket_created,
            WebhookEventType.ticket_escalated,
            WebhookEventType.ticket_resolved,
        ],
    ),
    ("Custom", [WebhookEventType.custom]),
]
```

- [ ] **Step 4: Run tests, expect green**

```
poetry run pytest tests/test_webhook_admin_services.py -x -q
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```
git add app/services/webhook_events.py tests/test_webhook_admin_services.py
git commit -m "feat(webhooks): add event-type descriptions and grouping lookup"
```

---

### Task 2: `WebhookEndpoints.rotate_secret`

**Files:**
- Modify: `app/services/webhook.py`
- Modify: `tests/test_webhook_admin_services.py`

- [ ] **Step 1: Append failing tests**

```python
# tests/test_webhook_admin_services.py — append
import pytest
from fastapi import HTTPException

from app.schemas.webhook import WebhookEndpointCreate
from app.services import webhook as webhook_service


def _make_endpoint(db, **overrides):
    payload = WebhookEndpointCreate(
        name=overrides.pop("name", "Endpoint A"),
        url=overrides.pop("url", "https://hooks.example.com/a"),
        secret=overrides.pop("secret", "old-secret"),
        **overrides,
    )
    return webhook_service.webhook_endpoints.create(db, payload)


def test_rotate_secret_replaces_value_and_returns_new(db_session):
    endpoint = _make_endpoint(db_session)
    original = endpoint.secret

    new_secret = webhook_service.webhook_endpoints.rotate_secret(db_session, str(endpoint.id))

    db_session.refresh(endpoint)
    assert new_secret
    assert new_secret != original
    assert endpoint.secret == new_secret
    assert len(new_secret) >= 32  # secrets.token_urlsafe(32) → ~43 chars


def test_rotate_secret_404_for_missing_endpoint(db_session):
    with pytest.raises(HTTPException) as exc:
        webhook_service.webhook_endpoints.rotate_secret(db_session, "00000000-0000-0000-0000-000000000000")
    assert exc.value.status_code == 404
```

- [ ] **Step 2: Run, expect failure**

```
poetry run pytest tests/test_webhook_admin_services.py::test_rotate_secret_replaces_value_and_returns_new -x -q
```
Expected: FAIL — `rotate_secret` attribute missing.

- [ ] **Step 3: Implement on the manager**

Add at the top of `app/services/webhook.py`:
```python
import secrets
```

Append the method inside `class WebhookEndpoints` (after `delete`):
```python
    @staticmethod
    def rotate_secret(db: Session, endpoint_id: str) -> str:
        endpoint = db.get(WebhookEndpoint, coerce_uuid(endpoint_id))
        if not endpoint:
            raise HTTPException(status_code=404, detail="Webhook endpoint not found")
        new_secret = secrets.token_urlsafe(32)
        endpoint.secret = new_secret
        db.commit()
        db.refresh(endpoint)
        return new_secret
```

- [ ] **Step 4: Run tests**

```
poetry run pytest tests/test_webhook_admin_services.py -x -q
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```
git add app/services/webhook.py tests/test_webhook_admin_services.py
git commit -m "feat(webhooks): rotate_secret on WebhookEndpoints"
```

---

### Task 3: `WebhookEndpoints.send_test`

**Files:**
- Modify: `app/services/webhook.py`
- Modify: `tests/test_webhook_admin_services.py`

- [ ] **Step 1: Append failing test**

```python
# tests/test_webhook_admin_services.py — append
from app.models.webhook import WebhookDeliveryStatus, WebhookEventType, WebhookSubscription


def test_send_test_creates_pending_delivery_with_custom_event(db_session, monkeypatch):
    endpoint = _make_endpoint(db_session, name="Test Fire", url="https://hooks.example.com/test")

    sent = []
    monkeypatch.setattr(
        "app.tasks.webhooks.deliver_webhook.delay",
        lambda delivery_id: sent.append(delivery_id),
    )

    delivery = webhook_service.webhook_endpoints.send_test(
        db_session, str(endpoint.id), actor_person_id=None
    )

    assert delivery.endpoint_id == endpoint.id
    assert delivery.status == WebhookDeliveryStatus.pending
    assert delivery.event_type == WebhookEventType.custom
    assert delivery.payload["test"] is True
    assert delivery.payload["endpoint_id"] == str(endpoint.id)
    assert sent == [str(delivery.id)]

    # Idempotent: re-running should reuse the same custom subscription row.
    second = webhook_service.webhook_endpoints.send_test(
        db_session, str(endpoint.id), actor_person_id=None
    )
    custom_subs = (
        db_session.query(WebhookSubscription)
        .filter(WebhookSubscription.endpoint_id == endpoint.id)
        .filter(WebhookSubscription.event_type == WebhookEventType.custom)
        .all()
    )
    assert len(custom_subs) == 1
    assert second.subscription_id == custom_subs[0].id
```

- [ ] **Step 2: Run, expect failure**

```
poetry run pytest tests/test_webhook_admin_services.py::test_send_test_creates_pending_delivery_with_custom_event -x -q
```
Expected: FAIL.

- [ ] **Step 3: Implement**

At the top of `app/services/webhook.py` add:
```python
from datetime import UTC, datetime
```

Append inside `class WebhookEndpoints` (after `rotate_secret`):
```python
    @staticmethod
    def send_test(
        db: Session,
        endpoint_id: str,
        *,
        actor_person_id: str | None,
    ) -> WebhookDelivery:
        endpoint = db.get(WebhookEndpoint, coerce_uuid(endpoint_id))
        if not endpoint:
            raise HTTPException(status_code=404, detail="Webhook endpoint not found")

        subscription = (
            db.query(WebhookSubscription)
            .filter(WebhookSubscription.endpoint_id == endpoint.id)
            .filter(WebhookSubscription.event_type == WebhookEventType.custom)
            .first()
        )
        if subscription is None:
            subscription = WebhookSubscription(
                endpoint_id=endpoint.id,
                event_type=WebhookEventType.custom,
                is_active=True,
            )
            db.add(subscription)
            db.flush()

        delivery = WebhookDelivery(
            subscription_id=subscription.id,
            endpoint_id=endpoint.id,
            event_type=WebhookEventType.custom,
            status=WebhookDeliveryStatus.pending,
            payload={
                "test": True,
                "fired_at": datetime.now(UTC).isoformat(),
                "fired_by_person_id": actor_person_id,
                "endpoint_id": str(endpoint.id),
            },
        )
        db.add(delivery)
        db.commit()
        db.refresh(delivery)

        from app.tasks.webhooks import deliver_webhook

        deliver_webhook.delay(str(delivery.id))
        return delivery
```

- [ ] **Step 4: Run tests**

```
poetry run pytest tests/test_webhook_admin_services.py -x -q
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```
git add app/services/webhook.py tests/test_webhook_admin_services.py
git commit -m "feat(webhooks): send_test enqueues a synthetic delivery"
```

---

### Task 4: `WebhookDeliveries.replay`

**Files:**
- Modify: `app/services/webhook.py`
- Modify: `tests/test_webhook_admin_services.py`

- [ ] **Step 1: Append failing test**

```python
# tests/test_webhook_admin_services.py — append
from app.models.webhook import WebhookDelivery


def test_replay_clones_source_delivery_and_enqueues(db_session, monkeypatch):
    endpoint = _make_endpoint(db_session, name="Replay", url="https://hooks.example.com/replay")
    # Seed via send_test (creates a custom subscription + delivery)
    monkeypatch.setattr("app.tasks.webhooks.deliver_webhook.delay", lambda *_args, **_kw: None)
    source = webhook_service.webhook_endpoints.send_test(
        db_session, str(endpoint.id), actor_person_id=None
    )
    source.status = WebhookDeliveryStatus.failed
    source.error = "boom"
    source.response_status = 502
    source.attempt_count = 3
    db_session.commit()

    sent = []
    monkeypatch.setattr(
        "app.tasks.webhooks.deliver_webhook.delay",
        lambda delivery_id: sent.append(delivery_id),
    )

    replayed = webhook_service.webhook_deliveries.replay(
        db_session, str(source.id), actor_person_id=None
    )

    assert replayed.id != source.id
    assert replayed.endpoint_id == source.endpoint_id
    assert replayed.subscription_id == source.subscription_id
    assert replayed.event_type == source.event_type
    assert replayed.payload == source.payload
    assert replayed.status == WebhookDeliveryStatus.pending
    assert replayed.attempt_count == 0
    assert replayed.response_status is None
    assert replayed.error is None
    assert sent == [str(replayed.id)]

    db_session.refresh(source)
    assert source.status == WebhookDeliveryStatus.failed
    assert source.attempt_count == 3
```

- [ ] **Step 2: Run, expect failure**

```
poetry run pytest tests/test_webhook_admin_services.py::test_replay_clones_source_delivery_and_enqueues -x -q
```
Expected: FAIL.

- [ ] **Step 3: Implement**

Append inside `class WebhookDeliveries` (after `update`):
```python
    @staticmethod
    def replay(
        db: Session,
        delivery_id: str,
        *,
        actor_person_id: str | None,
    ) -> WebhookDelivery:
        del actor_person_id
        source = db.get(WebhookDelivery, coerce_uuid(delivery_id))
        if not source:
            raise HTTPException(status_code=404, detail="Webhook delivery not found")
        replay = WebhookDelivery(
            subscription_id=source.subscription_id,
            endpoint_id=source.endpoint_id,
            event_type=source.event_type,
            status=WebhookDeliveryStatus.pending,
            payload=source.payload,
        )
        db.add(replay)
        db.commit()
        db.refresh(replay)

        from app.tasks.webhooks import deliver_webhook

        deliver_webhook.delay(str(replay.id))
        return replay
```

- [ ] **Step 4: Run tests**

```
poetry run pytest tests/test_webhook_admin_services.py -x -q
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```
git add app/services/webhook.py tests/test_webhook_admin_services.py
git commit -m "feat(webhooks): replay a delivery as a new pending row"
```

---

### Task 5: `WebhookDeliveries.list_filtered`

**Files:**
- Modify: `app/services/webhook.py`
- Modify: `tests/test_webhook_admin_services.py`

- [ ] **Step 1: Append failing test**

```python
# tests/test_webhook_admin_services.py — append
from datetime import UTC, datetime, timedelta


def test_list_filtered_applies_status_event_and_date_range(db_session, monkeypatch):
    monkeypatch.setattr("app.tasks.webhooks.deliver_webhook.delay", lambda *_a, **_k: None)
    endpoint = _make_endpoint(db_session, name="Logs", url="https://hooks.example.com/logs")

    base = webhook_service.webhook_endpoints.send_test(db_session, str(endpoint.id), actor_person_id=None)
    older = WebhookDelivery(
        subscription_id=base.subscription_id,
        endpoint_id=base.endpoint_id,
        event_type=WebhookEventType.custom,
        status=WebhookDeliveryStatus.failed,
        payload={"old": True},
    )
    older.created_at = datetime.now(UTC) - timedelta(days=10)
    db_session.add(older)
    db_session.commit()

    recent = webhook_service.webhook_deliveries.list_filtered(
        db_session,
        endpoint_id=str(endpoint.id),
        status=None,
        event_type=None,
        since=datetime.now(UTC) - timedelta(days=1),
        until=None,
        limit=50,
        offset=0,
    )
    assert {d.id for d in recent} == {base.id}

    failed = webhook_service.webhook_deliveries.list_filtered(
        db_session,
        endpoint_id=str(endpoint.id),
        status="failed",
        event_type=None,
        since=None,
        until=None,
        limit=50,
        offset=0,
    )
    assert {d.id for d in failed} == {older.id}

    by_event = webhook_service.webhook_deliveries.list_filtered(
        db_session,
        endpoint_id=str(endpoint.id),
        status=None,
        event_type="custom",
        since=None,
        until=None,
        limit=50,
        offset=0,
    )
    assert {d.id for d in by_event} == {base.id, older.id}
```

- [ ] **Step 2: Run, expect failure**

```
poetry run pytest tests/test_webhook_admin_services.py::test_list_filtered_applies_status_event_and_date_range -x -q
```
Expected: FAIL (`list_filtered` missing).

- [ ] **Step 3: Implement**

Append inside `class WebhookDeliveries` (after `replay`):
```python
    @staticmethod
    def list_filtered(
        db: Session,
        endpoint_id: str,
        *,
        status: str | None,
        event_type: str | None,
        since: datetime | None,
        until: datetime | None,
        limit: int,
        offset: int,
    ):
        query = db.query(WebhookDelivery).filter(
            WebhookDelivery.endpoint_id == coerce_uuid(endpoint_id)
        )
        if status:
            query = query.filter(
                WebhookDelivery.status == validate_enum(status, WebhookDeliveryStatus, "status")
            )
        if event_type:
            query = query.filter(
                WebhookDelivery.event_type == validate_enum(event_type, WebhookEventType, "event_type")
            )
        if since is not None:
            query = query.filter(WebhookDelivery.created_at >= since)
        if until is not None:
            query = query.filter(WebhookDelivery.created_at <= until)
        query = query.order_by(WebhookDelivery.created_at.desc())
        return apply_pagination(query, limit, offset).all()
```

Make sure the existing `from datetime import ...` covers `datetime`. (Added in Task 3 already.)

- [ ] **Step 4: Run tests**

```
poetry run pytest tests/test_webhook_admin_services.py -x -q
```
Expected: 8 passed.

- [ ] **Step 5: Commit**

```
git add app/services/webhook.py tests/test_webhook_admin_services.py
git commit -m "feat(webhooks): list_filtered for delivery log queries"
```

---

### Task 6: `WebhookEndpoints.list_with_stats` + `EndpointStats`

**Files:**
- Modify: `app/services/webhook.py`
- Modify: `tests/test_webhook_admin_services.py`

- [ ] **Step 1: Append failing test**

```python
# tests/test_webhook_admin_services.py — append
def test_list_with_stats_returns_per_endpoint_counts(db_session, monkeypatch):
    monkeypatch.setattr("app.tasks.webhooks.deliver_webhook.delay", lambda *_a, **_k: None)
    e1 = _make_endpoint(db_session, name="S1", url="https://hooks.example.com/s1")
    e2 = _make_endpoint(db_session, name="S2", url="https://hooks.example.com/s2")

    # e1: 1 delivered, 1 failed (24h), 1 failed (old)
    d_ok = webhook_service.webhook_endpoints.send_test(db_session, str(e1.id), actor_person_id=None)
    d_ok.status = WebhookDeliveryStatus.delivered
    d_ok.delivered_at = datetime.now(UTC)
    d_fail = WebhookDelivery(
        subscription_id=d_ok.subscription_id,
        endpoint_id=e1.id,
        event_type=WebhookEventType.custom,
        status=WebhookDeliveryStatus.failed,
        payload={},
    )
    d_old = WebhookDelivery(
        subscription_id=d_ok.subscription_id,
        endpoint_id=e1.id,
        event_type=WebhookEventType.custom,
        status=WebhookDeliveryStatus.failed,
        payload={},
    )
    d_old.created_at = datetime.now(UTC) - timedelta(days=10)
    db_session.add_all([d_fail, d_old])
    db_session.commit()

    stats = webhook_service.webhook_endpoints.list_with_stats(db_session, limit=50, offset=0)
    by_id = {row.endpoint.id: row for row in stats}

    assert by_id[e1.id].last_24h_delivered == 1
    assert by_id[e1.id].last_24h_failed == 1
    assert by_id[e1.id].pending_count == 0
    assert by_id[e1.id].last_delivery_at is not None

    assert by_id[e2.id].last_24h_delivered == 0
    assert by_id[e2.id].last_24h_failed == 0
    assert by_id[e2.id].last_delivery_at is None
```

- [ ] **Step 2: Run, expect failure**

```
poetry run pytest tests/test_webhook_admin_services.py::test_list_with_stats_returns_per_endpoint_counts -x -q
```
Expected: FAIL.

- [ ] **Step 3: Implement**

Add at top of `app/services/webhook.py`:
```python
from dataclasses import dataclass
from sqlalchemy import func
```

Add just below the imports (module-level):
```python
@dataclass(frozen=True)
class EndpointStats:
    endpoint: WebhookEndpoint
    last_24h_delivered: int
    last_24h_failed: int
    pending_count: int
    last_delivery_at: datetime | None
```

Append inside `class WebhookEndpoints` (after `send_test`):
```python
    @staticmethod
    def list_with_stats(db: Session, *, limit: int, offset: int) -> list["EndpointStats"]:
        endpoints = (
            db.query(WebhookEndpoint)
            .order_by(WebhookEndpoint.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
        if not endpoints:
            return []
        endpoint_ids = [e.id for e in endpoints]
        cutoff = datetime.now(UTC) - timedelta(hours=24)

        delivered_rows = (
            db.query(
                WebhookDelivery.endpoint_id,
                func.count(WebhookDelivery.id),
                func.max(WebhookDelivery.created_at),
            )
            .filter(WebhookDelivery.endpoint_id.in_(endpoint_ids))
            .filter(WebhookDelivery.status == WebhookDeliveryStatus.delivered)
            .filter(WebhookDelivery.created_at >= cutoff)
            .group_by(WebhookDelivery.endpoint_id)
            .all()
        )
        failed_rows = (
            db.query(WebhookDelivery.endpoint_id, func.count(WebhookDelivery.id))
            .filter(WebhookDelivery.endpoint_id.in_(endpoint_ids))
            .filter(WebhookDelivery.status == WebhookDeliveryStatus.failed)
            .filter(WebhookDelivery.created_at >= cutoff)
            .group_by(WebhookDelivery.endpoint_id)
            .all()
        )
        pending_rows = (
            db.query(WebhookDelivery.endpoint_id, func.count(WebhookDelivery.id))
            .filter(WebhookDelivery.endpoint_id.in_(endpoint_ids))
            .filter(WebhookDelivery.status == WebhookDeliveryStatus.pending)
            .group_by(WebhookDelivery.endpoint_id)
            .all()
        )
        last_rows = (
            db.query(WebhookDelivery.endpoint_id, func.max(WebhookDelivery.created_at))
            .filter(WebhookDelivery.endpoint_id.in_(endpoint_ids))
            .group_by(WebhookDelivery.endpoint_id)
            .all()
        )

        delivered_24h = {eid: count for eid, count, _ in delivered_rows}
        failed_24h = dict(failed_rows)
        pending_total = dict(pending_rows)
        last_seen = dict(last_rows)

        return [
            EndpointStats(
                endpoint=e,
                last_24h_delivered=delivered_24h.get(e.id, 0),
                last_24h_failed=failed_24h.get(e.id, 0),
                pending_count=pending_total.get(e.id, 0),
                last_delivery_at=last_seen.get(e.id),
            )
            for e in endpoints
        ]
```

Add `timedelta` to the existing `from datetime import ...` line (`from datetime import UTC, datetime, timedelta`).

- [ ] **Step 4: Run tests**

```
poetry run pytest tests/test_webhook_admin_services.py -x -q
```
Expected: 9 passed.

- [ ] **Step 5: Commit**

```
git add app/services/webhook.py tests/test_webhook_admin_services.py
git commit -m "feat(webhooks): list_with_stats aggregates 24h success/failure"
```

---

### Task 7: Edit route + template

**Files:**
- Create: `templates/admin/integrations/webhooks/edit.html`
- Modify: `app/web/admin/integrations.py`
- Create/append: `tests/test_webhook_admin_web.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_webhook_admin_web.py — new file
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.webhook import WebhookEndpointCreate
from app.services import webhook as webhook_service


def test_edit_form_renders_with_endpoint(db_session, monkeypatch):
    monkeypatch.setattr("app.tasks.webhooks.deliver_webhook.delay", lambda *_a, **_k: None)
    endpoint = webhook_service.webhook_endpoints.create(
        db_session,
        WebhookEndpointCreate(name="Edit Me", url="https://hooks.example.com/edit"),
    )
    client = TestClient(app)
    resp = client.get(f"/admin/integrations/webhooks/{endpoint.id}/edit")
    assert resp.status_code == 200
    assert "Edit Me" in resp.text
    assert "https://hooks.example.com/edit" in resp.text


def test_post_update_changes_name_and_redirects(db_session, monkeypatch):
    monkeypatch.setattr("app.tasks.webhooks.deliver_webhook.delay", lambda *_a, **_k: None)
    endpoint = webhook_service.webhook_endpoints.create(
        db_session,
        WebhookEndpointCreate(name="Old", url="https://hooks.example.com/old"),
    )
    client = TestClient(app)
    resp = client.post(
        f"/admin/integrations/webhooks/{endpoint.id}",
        data={"name": "New Name", "url": "https://hooks.example.com/new", "is_active": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].endswith(f"/admin/integrations/webhooks/{endpoint.id}")
```

- [ ] **Step 2: Run, expect failure**

```
poetry run pytest tests/test_webhook_admin_web.py -x -q
```
Expected: FAIL — routes return 404 (no edit/POST yet).

- [ ] **Step 3: Add the routes**

In `app/web/admin/integrations.py`, after the existing webhook `detail` route, append:

```python
@router.get("/webhooks/{endpoint_id}/edit", response_class=HTMLResponse)
def webhook_edit(request: Request, endpoint_id: str, db: Session = Depends(get_db)):
    endpoint = webhook_service.webhook_endpoints.get(db, endpoint_id)
    form = {
        "name": endpoint.name,
        "url": endpoint.url,
        "connector_config_id": str(endpoint.connector_config_id) if endpoint.connector_config_id else "",
        "is_active": endpoint.is_active,
    }
    context = _base_context(request, db, active_page="webhooks")
    context.update({"endpoint": endpoint, "form": form, "error": None})
    return templates.TemplateResponse("admin/integrations/webhooks/edit.html", context)


@router.post("/webhooks/{endpoint_id}", response_class=HTMLResponse)
def webhook_update(
    request: Request,
    endpoint_id: str,
    name: str = Form(...),
    url: str = Form(...),
    connector_config_id: str | None = Form(None),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
):
    endpoint = webhook_service.webhook_endpoints.get(db, endpoint_id)
    actor_id = _actor_person_id(request)
    try:
        from app.schemas.webhook import WebhookEndpointUpdate

        payload = WebhookEndpointUpdate(
            name=name.strip(),
            url=url.strip(),
            connector_config_id=UUID(connector_config_id) if connector_config_id else None,
            is_active=is_active,
        )
        original = {"name": endpoint.name, "url": endpoint.url, "is_active": endpoint.is_active}
        webhook_service.webhook_endpoints.update(db, endpoint_id, payload)
        changed = sorted(k for k in original if getattr(endpoint, k) != original[k])
    except Exception as exc:
        context = _base_context(request, db, active_page="webhooks")
        context.update(
            {
                "endpoint": endpoint,
                "form": {
                    "name": name,
                    "url": url,
                    "connector_config_id": connector_config_id or "",
                    "is_active": is_active,
                },
                "error": str(exc),
            }
        )
        return templates.TemplateResponse(
            "admin/integrations/webhooks/edit.html", context, status_code=400
        )

    log_audit_event(
        db,
        request,
        action="webhook_endpoint_updated",
        entity_type="webhook_endpoint",
        entity_id=str(endpoint.id),
        actor_id=actor_id,
        metadata={"changed_keys": changed, "is_active": endpoint.is_active},
    )
    return RedirectResponse(url=f"/admin/integrations/webhooks/{endpoint_id}", status_code=303)
```

Add the supporting helper near the top of the file (after the existing helpers):
```python
def _actor_person_id(request: Request) -> str | None:
    person = getattr(request.state, "user", None)
    return str(person.id) if person is not None else None
```

Add imports as needed at the top of `integrations.py`:
```python
from app.services.audit_helpers import log_audit_event
```

- [ ] **Step 4: Create the edit template**

`templates/admin/integrations/webhooks/edit.html`:
```html
{% extends "layouts/admin.html" %}
{% from "components/ui/macros.html" import submit_button %}
{% block title %}Edit {{ endpoint.name }} - Webhooks{% endblock %}
{% block content %}
<div class="space-y-6">
    <div class="flex items-center gap-4">
        <a href="/admin/integrations/webhooks/{{ endpoint.id }}" class="rounded-lg p-2 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800">
            <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"/></svg>
        </a>
        <div>
            <h1 class="text-2xl font-bold text-slate-900 dark:text-white">Edit Webhook Endpoint</h1>
            <p class="mt-1 text-sm text-slate-500 dark:text-slate-400">{{ endpoint.name }}</p>
        </div>
    </div>

    <form method="POST" action="/admin/integrations/webhooks/{{ endpoint.id }}" class="space-y-6">
        {% include "components/forms/csrf_input.html" %}
        <div class="rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800">
            <div class="border-b border-slate-200 px-6 py-4 dark:border-slate-700">
                <h2 class="font-semibold text-slate-900 dark:text-white">Endpoint Details</h2>
            </div>
            <div class="space-y-6 p-6">
                {% if error %}
                <div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">{{ error }}</div>
                {% endif %}

                <div>
                    <label class="block text-sm font-medium text-slate-700 dark:text-slate-300" for="name">Name</label>
                    <input id="name" name="name" type="text" required maxlength="160" value="{{ form.name }}" class="mt-1 block w-full rounded-xl border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-primary-500 focus:ring-primary-500 dark:border-slate-600 dark:bg-slate-700 dark:text-white" />
                </div>

                <div>
                    <label class="block text-sm font-medium text-slate-700 dark:text-slate-300" for="url">URL</label>
                    <input id="url" name="url" type="url" required maxlength="500" value="{{ form.url }}" class="mt-1 block w-full rounded-xl border border-slate-300 px-3 py-2 text-sm font-mono shadow-sm focus:border-primary-500 focus:ring-primary-500 dark:border-slate-600 dark:bg-slate-700 dark:text-white" />
                </div>

                <div>
                    <label class="flex items-center gap-2">
                        <input type="checkbox" name="is_active" value="true" {% if form.is_active %}checked{% endif %} class="h-4 w-4 rounded border-slate-300 text-primary-600 focus:ring-primary-500" />
                        <span class="text-sm font-medium text-slate-700 dark:text-slate-300">Active</span>
                    </label>
                </div>
            </div>
        </div>

        <div class="flex items-center justify-end gap-3">
            <a href="/admin/integrations/webhooks/{{ endpoint.id }}" class="inline-flex items-center rounded-xl border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300">Cancel</a>
            {{ submit_button("Save Webhook", loading_label="Saving...") }}
        </div>
    </form>
</div>
{% endblock %}
```

- [ ] **Step 5: Run tests**

```
poetry run pytest tests/test_webhook_admin_web.py -x -q
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```
git add app/web/admin/integrations.py templates/admin/integrations/webhooks/edit.html tests/test_webhook_admin_web.py
git commit -m "feat(webhooks): admin edit form + update route"
```

---

### Task 8: Rotate-secret route + success page

**Files:**
- Modify: `app/web/admin/integrations.py`
- Create: `templates/admin/integrations/webhooks/rotate_success.html`
- Modify: `tests/test_webhook_admin_web.py`

- [ ] **Step 1: Append failing test**

```python
# tests/test_webhook_admin_web.py — append
def test_post_rotate_secret_renders_new_value_once(db_session, monkeypatch):
    monkeypatch.setattr("app.tasks.webhooks.deliver_webhook.delay", lambda *_a, **_k: None)
    endpoint = webhook_service.webhook_endpoints.create(
        db_session,
        WebhookEndpointCreate(name="Rotate", url="https://hooks.example.com/rot", secret="old"),
    )
    client = TestClient(app)
    resp = client.post(f"/admin/integrations/webhooks/{endpoint.id}/rotate-secret", data={})
    assert resp.status_code == 200
    assert "new signing secret" in resp.text.lower()
    db_session.refresh(endpoint)
    assert endpoint.secret in resp.text
    assert endpoint.secret != "old"
```

- [ ] **Step 2: Run, expect failure**

```
poetry run pytest tests/test_webhook_admin_web.py::test_post_rotate_secret_renders_new_value_once -x -q
```
Expected: FAIL.

- [ ] **Step 3: Add the route**

In `app/web/admin/integrations.py`, after `webhook_update`:

```python
@router.post("/webhooks/{endpoint_id}/rotate-secret", response_class=HTMLResponse)
def webhook_rotate_secret(request: Request, endpoint_id: str, db: Session = Depends(get_db)):
    endpoint = webhook_service.webhook_endpoints.get(db, endpoint_id)
    new_secret = webhook_service.webhook_endpoints.rotate_secret(db, endpoint_id)
    log_audit_event(
        db,
        request,
        action="webhook_endpoint_secret_rotated",
        entity_type="webhook_endpoint",
        entity_id=str(endpoint.id),
        actor_id=_actor_person_id(request),
        metadata=None,
    )
    context = _base_context(request, db, active_page="webhooks")
    context.update({"endpoint": endpoint, "new_secret": new_secret})
    return templates.TemplateResponse("admin/integrations/webhooks/rotate_success.html", context)
```

- [ ] **Step 4: Create the success template**

`templates/admin/integrations/webhooks/rotate_success.html`:
```html
{% extends "layouts/admin.html" %}
{% block title %}Secret rotated - Webhooks{% endblock %}
{% block content %}
<div class="max-w-2xl space-y-6">
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white">New signing secret for {{ endpoint.name }}</h1>
    <div class="rounded-2xl border border-amber-200 bg-amber-50 px-5 py-4 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-200">
        Copy this secret now. For security, we will not display it again.
    </div>
    <div class="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-700 dark:bg-slate-800">
        <label class="block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Signing secret</label>
        <div class="mt-2 flex items-center gap-2">
            <code class="block flex-1 break-all rounded-lg bg-slate-50 px-3 py-2 font-mono text-sm text-slate-900 dark:bg-slate-900 dark:text-white">{{ new_secret }}</code>
            <button type="button" onclick="navigator.clipboard.writeText(this.previousElementSibling.innerText)" class="rounded-xl border border-primary-300 bg-primary-50 px-3 py-2 text-xs font-semibold text-primary-700 hover:bg-primary-100 dark:border-primary-700 dark:bg-primary-900/30 dark:text-primary-200">Copy</button>
        </div>
    </div>
    <div>
        <a href="/admin/integrations/webhooks/{{ endpoint.id }}" class="inline-flex items-center rounded-xl border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300">Back to endpoint</a>
    </div>
</div>
{% endblock %}
```

- [ ] **Step 5: Run tests**

```
poetry run pytest tests/test_webhook_admin_web.py -x -q
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```
git add app/web/admin/integrations.py templates/admin/integrations/webhooks/rotate_success.html tests/test_webhook_admin_web.py
git commit -m "feat(webhooks): admin secret rotation with one-time reveal"
```

---

### Task 9: Test-fire route

**Files:**
- Modify: `app/web/admin/integrations.py`
- Modify: `tests/test_webhook_admin_web.py`

- [ ] **Step 1: Append failing test**

```python
# tests/test_webhook_admin_web.py — append
from app.models.webhook import WebhookDelivery, WebhookEventType


def test_post_test_fire_creates_pending_delivery(db_session, monkeypatch):
    sent = []
    monkeypatch.setattr("app.tasks.webhooks.deliver_webhook.delay", lambda did: sent.append(did))
    endpoint = webhook_service.webhook_endpoints.create(
        db_session,
        WebhookEndpointCreate(name="Fire", url="https://hooks.example.com/fire"),
    )
    client = TestClient(app)
    resp = client.post(
        f"/admin/integrations/webhooks/{endpoint.id}/test",
        data={},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].endswith(
        f"/admin/integrations/webhooks/{endpoint.id}/deliveries"
    )
    rows = (
        db_session.query(WebhookDelivery)
        .filter(WebhookDelivery.endpoint_id == endpoint.id)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].event_type == WebhookEventType.custom
    assert sent == [str(rows[0].id)]
```

- [ ] **Step 2: Run, expect failure**

```
poetry run pytest tests/test_webhook_admin_web.py::test_post_test_fire_creates_pending_delivery -x -q
```
Expected: FAIL.

- [ ] **Step 3: Add the route**

In `app/web/admin/integrations.py`, after `webhook_rotate_secret`:

```python
@router.post("/webhooks/{endpoint_id}/test", response_class=HTMLResponse)
def webhook_test_fire(request: Request, endpoint_id: str, db: Session = Depends(get_db)):
    actor_id = _actor_person_id(request)
    delivery = webhook_service.webhook_endpoints.send_test(
        db, endpoint_id, actor_person_id=actor_id
    )
    log_audit_event(
        db,
        request,
        action="webhook_endpoint_test_fired",
        entity_type="webhook_endpoint",
        entity_id=endpoint_id,
        actor_id=actor_id,
        metadata={"delivery_id": str(delivery.id)},
    )
    return RedirectResponse(
        url=f"/admin/integrations/webhooks/{endpoint_id}/deliveries",
        status_code=303,
    )
```

- [ ] **Step 4: Run tests**

```
poetry run pytest tests/test_webhook_admin_web.py -x -q
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```
git add app/web/admin/integrations.py tests/test_webhook_admin_web.py
git commit -m "feat(webhooks): admin test-fire route"
```

---

### Task 10: Subscription add + remove routes

**Files:**
- Modify: `app/web/admin/integrations.py`
- Modify: `tests/test_webhook_admin_web.py`

- [ ] **Step 1: Append failing tests**

```python
# tests/test_webhook_admin_web.py — append
from app.models.webhook import WebhookSubscription
from app.schemas.webhook import WebhookSubscriptionCreate


def test_post_subscription_add_creates_row(db_session):
    endpoint = webhook_service.webhook_endpoints.create(
        db_session,
        WebhookEndpointCreate(name="Subs", url="https://hooks.example.com/subs"),
    )
    client = TestClient(app)
    resp = client.post(
        f"/admin/integrations/webhooks/{endpoint.id}/subscriptions",
        data={"event_type": "ticket.created"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    rows = (
        db_session.query(WebhookSubscription)
        .filter(WebhookSubscription.endpoint_id == endpoint.id)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].event_type.value == "ticket.created"


def test_post_subscription_delete_soft_deletes(db_session):
    endpoint = webhook_service.webhook_endpoints.create(
        db_session,
        WebhookEndpointCreate(name="Subs2", url="https://hooks.example.com/subs2"),
    )
    sub = webhook_service.webhook_subscriptions.create(
        db_session,
        WebhookSubscriptionCreate(endpoint_id=str(endpoint.id), event_type="ticket.created"),
    )
    client = TestClient(app)
    resp = client.post(
        f"/admin/integrations/webhooks/{endpoint.id}/subscriptions/{sub.id}/delete",
        data={},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    db_session.refresh(sub)
    assert sub.is_active is False
```

- [ ] **Step 2: Run, expect failure**

```
poetry run pytest tests/test_webhook_admin_web.py -x -q -k "subscription"
```
Expected: FAIL.

- [ ] **Step 3: Add the routes**

In `app/web/admin/integrations.py`, after `webhook_test_fire`:

```python
@router.post("/webhooks/{endpoint_id}/subscriptions", response_class=HTMLResponse)
def webhook_subscription_add(
    request: Request,
    endpoint_id: str,
    event_type: str = Form(...),
    db: Session = Depends(get_db),
):
    from app.schemas.webhook import WebhookSubscriptionCreate

    payload = WebhookSubscriptionCreate(endpoint_id=UUID(endpoint_id), event_type=event_type)
    subscription = webhook_service.webhook_subscriptions.create(db, payload)
    log_audit_event(
        db,
        request,
        action="webhook_subscription_added",
        entity_type="webhook_subscription",
        entity_id=str(subscription.id),
        actor_id=_actor_person_id(request),
        metadata={"endpoint_id": endpoint_id, "event_type": event_type},
    )
    return RedirectResponse(
        url=f"/admin/integrations/webhooks/{endpoint_id}",
        status_code=303,
    )


@router.post(
    "/webhooks/{endpoint_id}/subscriptions/{subscription_id}/delete",
    response_class=HTMLResponse,
)
def webhook_subscription_remove(
    request: Request,
    endpoint_id: str,
    subscription_id: str,
    db: Session = Depends(get_db),
):
    subscription = webhook_service.webhook_subscriptions.get(db, subscription_id)
    event_type_value = subscription.event_type.value if subscription.event_type else None
    webhook_service.webhook_subscriptions.delete(db, subscription_id)
    log_audit_event(
        db,
        request,
        action="webhook_subscription_removed",
        entity_type="webhook_subscription",
        entity_id=subscription_id,
        actor_id=_actor_person_id(request),
        metadata={"endpoint_id": endpoint_id, "event_type": event_type_value},
    )
    return RedirectResponse(
        url=f"/admin/integrations/webhooks/{endpoint_id}",
        status_code=303,
    )
```

- [ ] **Step 4: Run tests**

```
poetry run pytest tests/test_webhook_admin_web.py -x -q
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```
git add app/web/admin/integrations.py tests/test_webhook_admin_web.py
git commit -m "feat(webhooks): subscription add/remove admin routes"
```

---

### Task 11: Delivery log route + template + replay route

**Files:**
- Modify: `app/web/admin/integrations.py`
- Create: `templates/admin/integrations/webhooks/deliveries.html`
- Modify: `tests/test_webhook_admin_web.py`

- [ ] **Step 1: Append failing tests**

```python
# tests/test_webhook_admin_web.py — append
from app.models.webhook import WebhookDeliveryStatus


def test_deliveries_page_renders_with_filter(db_session, monkeypatch):
    monkeypatch.setattr("app.tasks.webhooks.deliver_webhook.delay", lambda *_a, **_k: None)
    endpoint = webhook_service.webhook_endpoints.create(
        db_session,
        WebhookEndpointCreate(name="Log", url="https://hooks.example.com/log"),
    )
    delivery = webhook_service.webhook_endpoints.send_test(
        db_session, str(endpoint.id), actor_person_id=None
    )
    client = TestClient(app)
    resp = client.get(f"/admin/integrations/webhooks/{endpoint.id}/deliveries?status=pending")
    assert resp.status_code == 200
    assert str(delivery.id) in resp.text


def test_post_replay_creates_new_delivery(db_session, monkeypatch):
    sent = []
    monkeypatch.setattr("app.tasks.webhooks.deliver_webhook.delay", lambda did: sent.append(did))
    endpoint = webhook_service.webhook_endpoints.create(
        db_session,
        WebhookEndpointCreate(name="Rep", url="https://hooks.example.com/rep"),
    )
    source = webhook_service.webhook_endpoints.send_test(
        db_session, str(endpoint.id), actor_person_id=None
    )
    source.status = WebhookDeliveryStatus.failed
    db_session.commit()
    sent.clear()

    client = TestClient(app)
    resp = client.post(
        f"/admin/integrations/webhooks/{endpoint.id}/deliveries/{source.id}/replay",
        data={},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    rows = (
        db_session.query(WebhookDelivery)
        .filter(WebhookDelivery.endpoint_id == endpoint.id)
        .order_by(WebhookDelivery.created_at)
        .all()
    )
    assert len(rows) == 2
    assert rows[1].status == WebhookDeliveryStatus.pending
    assert sent == [str(rows[1].id)]
```

- [ ] **Step 2: Run, expect failure**

```
poetry run pytest tests/test_webhook_admin_web.py -x -q -k "deliveries or replay"
```
Expected: FAIL.

- [ ] **Step 3: Add the routes**

In `app/web/admin/integrations.py`, after `webhook_subscription_remove`:

```python
@router.get("/webhooks/{endpoint_id}/deliveries", response_class=HTMLResponse)
def webhook_deliveries(
    request: Request,
    endpoint_id: str,
    status: str | None = Query(None),
    event_type: str | None = Query(None),
    range: str | None = Query("24h"),
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
):
    from app.services.webhook_events import EVENT_GROUPS, WEBHOOK_EVENT_DESCRIPTIONS

    endpoint = webhook_service.webhook_endpoints.get(db, endpoint_id)
    now = datetime.now(UTC)
    since_map = {
        "24h": now - timedelta(hours=24),
        "7d": now - timedelta(days=7),
        "30d": now - timedelta(days=30),
        "all": None,
    }
    since = since_map.get(range, since_map["24h"])
    page_size = 50
    offset = (page - 1) * page_size
    try:
        deliveries = webhook_service.webhook_deliveries.list_filtered(
            db,
            endpoint_id=endpoint_id,
            status=status,
            event_type=event_type,
            since=since,
            until=None,
            limit=page_size,
            offset=offset,
        )
    except HTTPException:
        deliveries = []

    context = _base_context(request, db, active_page="webhooks")
    context.update(
        {
            "endpoint": endpoint,
            "deliveries": deliveries,
            "filter_status": status or "",
            "filter_event_type": event_type or "",
            "filter_range": range or "24h",
            "page": page,
            "page_size": page_size,
            "event_groups": EVENT_GROUPS,
            "event_descriptions": WEBHOOK_EVENT_DESCRIPTIONS,
        }
    )
    return templates.TemplateResponse("admin/integrations/webhooks/deliveries.html", context)


@router.post(
    "/webhooks/{endpoint_id}/deliveries/{delivery_id}/replay",
    response_class=HTMLResponse,
)
def webhook_delivery_replay(
    request: Request,
    endpoint_id: str,
    delivery_id: str,
    db: Session = Depends(get_db),
):
    actor_id = _actor_person_id(request)
    replay = webhook_service.webhook_deliveries.replay(db, delivery_id, actor_person_id=actor_id)
    log_audit_event(
        db,
        request,
        action="webhook_delivery_replayed",
        entity_type="webhook_delivery",
        entity_id=str(replay.id),
        actor_id=actor_id,
        metadata={
            "endpoint_id": endpoint_id,
            "source_delivery_id": delivery_id,
            "event_type": replay.event_type.value if replay.event_type else None,
        },
    )
    return RedirectResponse(
        url=f"/admin/integrations/webhooks/{endpoint_id}/deliveries",
        status_code=303,
    )
```

Add imports if not present at the top of `integrations.py`:
```python
from datetime import UTC, datetime, timedelta
```

- [ ] **Step 4: Create the deliveries template**

`templates/admin/integrations/webhooks/deliveries.html`:
```html
{% extends "layouts/admin.html" %}
{% from "components/ui/macros.html" import empty_state %}
{% block title %}Deliveries - {{ endpoint.name }}{% endblock %}
{% block content %}
<div class="space-y-6">
    <div class="flex items-center justify-between gap-4">
        <div>
            <a href="/admin/integrations/webhooks/{{ endpoint.id }}" class="text-sm text-slate-500 hover:text-slate-700 dark:text-slate-400">← {{ endpoint.name }}</a>
            <h1 class="mt-1 text-2xl font-bold text-slate-900 dark:text-white">Delivery log</h1>
        </div>
    </div>

    <form method="get" class="flex flex-wrap items-end gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800">
        <div>
            <label class="block text-xs font-semibold uppercase tracking-wide text-slate-400" for="status">Status</label>
            <select id="status" name="status" class="mt-1 rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-700 dark:text-white">
                <option value="" {% if not filter_status %}selected{% endif %}>All</option>
                <option value="pending" {% if filter_status == "pending" %}selected{% endif %}>Pending</option>
                <option value="delivered" {% if filter_status == "delivered" %}selected{% endif %}>Delivered</option>
                <option value="failed" {% if filter_status == "failed" %}selected{% endif %}>Failed</option>
            </select>
        </div>
        <div>
            <label class="block text-xs font-semibold uppercase tracking-wide text-slate-400" for="event_type">Event</label>
            <select id="event_type" name="event_type" class="mt-1 rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-700 dark:text-white">
                <option value="" {% if not filter_event_type %}selected{% endif %}>All events</option>
                {% for label, items in event_groups %}
                <optgroup label="{{ label }}">
                    {% for et in items %}
                    <option value="{{ et.value }}" {% if filter_event_type == et.value %}selected{% endif %}>{{ et.value }}</option>
                    {% endfor %}
                </optgroup>
                {% endfor %}
            </select>
        </div>
        <div>
            <label class="block text-xs font-semibold uppercase tracking-wide text-slate-400" for="range">Range</label>
            <select id="range" name="range" class="mt-1 rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-700 dark:text-white">
                <option value="24h" {% if filter_range == "24h" %}selected{% endif %}>Last 24h</option>
                <option value="7d" {% if filter_range == "7d" %}selected{% endif %}>Last 7 days</option>
                <option value="30d" {% if filter_range == "30d" %}selected{% endif %}>Last 30 days</option>
                <option value="all" {% if filter_range == "all" %}selected{% endif %}>All time</option>
            </select>
        </div>
        <button type="submit" class="inline-flex items-center rounded-xl border border-primary-300 bg-primary-50 px-4 py-2 text-sm font-semibold text-primary-700 hover:bg-primary-100 dark:border-primary-700 dark:bg-primary-900/30 dark:text-primary-200">Apply</button>
    </form>

    <div class="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800">
        <table class="min-w-full divide-y divide-slate-200 dark:divide-slate-700">
            <thead class="bg-slate-50 dark:bg-slate-900/40">
                <tr>
                    <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">When</th>
                    <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">Event</th>
                    <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">Status</th>
                    <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">Attempts</th>
                    <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">HTTP</th>
                    <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">Error</th>
                    <th class="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-slate-500">Actions</th>
                </tr>
            </thead>
            <tbody class="divide-y divide-slate-200 dark:divide-slate-700">
                {% for d in deliveries %}
                {% set badge = {
                    'pending': 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
                    'delivered': 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
                    'failed': 'bg-rose-100 text-rose-800 dark:bg-rose-900/30 dark:text-rose-300',
                } %}
                <tr id="{{ d.id }}">
                    <td class="px-4 py-3 text-sm text-slate-600 dark:text-slate-300">{{ d.created_at.strftime('%Y-%m-%d %H:%M:%S') }}</td>
                    <td class="px-4 py-3 text-sm font-mono text-slate-900 dark:text-white">{{ d.event_type.value if d.event_type else '' }}</td>
                    <td class="px-4 py-3 text-sm"><span class="inline-flex items-center rounded-lg px-2 py-1 text-xs font-semibold {{ badge.get(d.status.value, 'bg-slate-100 text-slate-700') }}">{{ d.status.value }}</span></td>
                    <td class="px-4 py-3 text-sm text-slate-600 dark:text-slate-300">{{ d.attempt_count }}</td>
                    <td class="px-4 py-3 text-sm text-slate-600 dark:text-slate-300">{{ d.response_status if d.response_status else '—' }}</td>
                    <td class="px-4 py-3 text-sm text-slate-600 dark:text-slate-300">{{ (d.error or '')[:80] }}</td>
                    <td class="px-4 py-3 text-right">
                        {% if d.status.value in ('failed', 'delivered') %}
                        <form method="post" action="/admin/integrations/webhooks/{{ endpoint.id }}/deliveries/{{ d.id }}/replay" class="inline">
                            {% include "components/forms/csrf_input.html" %}
                            <button type="submit" class="rounded-lg border border-primary-300 bg-primary-50 px-3 py-1 text-xs font-semibold text-primary-700 hover:bg-primary-100 dark:border-primary-700 dark:bg-primary-900/30 dark:text-primary-200">Replay</button>
                        </form>
                        {% endif %}
                    </td>
                </tr>
                {% else %}
                <tr><td colspan="7" class="p-6 text-center text-sm text-slate-500">No deliveries match.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="flex items-center justify-between text-sm text-slate-500">
        <div>Page {{ page }}</div>
        <div class="flex gap-2">
            {% if page > 1 %}
            <a href="?status={{ filter_status }}&event_type={{ filter_event_type }}&range={{ filter_range }}&page={{ page - 1 }}" class="rounded-xl border border-slate-300 bg-white px-3 py-1 dark:border-slate-600 dark:bg-slate-800">Previous</a>
            {% endif %}
            {% if deliveries|length == page_size %}
            <a href="?status={{ filter_status }}&event_type={{ filter_event_type }}&range={{ filter_range }}&page={{ page + 1 }}" class="rounded-xl border border-slate-300 bg-white px-3 py-1 dark:border-slate-600 dark:bg-slate-800">Next</a>
            {% endif %}
        </div>
    </div>
</div>
{% endblock %}
```

- [ ] **Step 5: Run tests**

```
poetry run pytest tests/test_webhook_admin_web.py -x -q
```
Expected: 8 passed.

- [ ] **Step 6: Commit**

```
git add app/web/admin/integrations.py templates/admin/integrations/webhooks/deliveries.html tests/test_webhook_admin_web.py
git commit -m "feat(webhooks): admin delivery log + replay route"
```

---

### Task 12: Subscription editor in detail page + action row

**Files:**
- Modify: `templates/admin/integrations/webhooks/detail.html`
- Create: `templates/admin/integrations/webhooks/_subscription_picker.html`
- Modify: `app/web/admin/integrations.py` (the existing `webhook_detail` route — pass extra context)
- Modify: `tests/test_webhook_admin_web.py`

- [ ] **Step 1: Append failing test**

```python
# tests/test_webhook_admin_web.py — append
def test_detail_page_shows_action_row_and_picker(db_session):
    endpoint = webhook_service.webhook_endpoints.create(
        db_session,
        WebhookEndpointCreate(name="Detail", url="https://hooks.example.com/d"),
    )
    client = TestClient(app)
    resp = client.get(f"/admin/integrations/webhooks/{endpoint.id}")
    assert resp.status_code == 200
    assert "Send test event" in resp.text
    assert "Rotate secret" in resp.text
    assert "Edit" in resp.text
    # Picker is included
    assert "Add subscription" in resp.text
    # An event from the picker shows up
    assert "ticket.created" in resp.text
```

- [ ] **Step 2: Run, expect failure**

```
poetry run pytest tests/test_webhook_admin_web.py::test_detail_page_shows_action_row_and_picker -x -q
```
Expected: FAIL.

- [ ] **Step 3: Update the detail route**

In `app/web/admin/integrations.py`, locate `webhook_detail` (the existing route) and update its body so the context includes the picker data:

```python
@router.get("/webhooks/{endpoint_id}", response_class=HTMLResponse)
def webhook_detail(request: Request, endpoint_id: str, db: Session = Depends(get_db)):
    from app.services.webhook_events import EVENT_GROUPS, WEBHOOK_EVENT_DESCRIPTIONS

    endpoint = webhook_service.webhook_endpoints.get(db, endpoint_id)
    subscriptions = (
        db.query(WebhookSubscription)
        .filter(WebhookSubscription.endpoint_id == endpoint.id)
        .filter(WebhookSubscription.is_active.is_(True))
        .all()
    )
    subscribed_events = {s.event_type for s in subscriptions}
    recent_deliveries = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.endpoint_id == endpoint.id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(10)
        .all()
    )

    context = _base_context(request, db, active_page="webhooks")
    context.update(
        {
            "endpoint": endpoint,
            "subscriptions": subscriptions,
            "subscribed_events": subscribed_events,
            "recent_deliveries": recent_deliveries,
            "event_groups": EVENT_GROUPS,
            "event_descriptions": WEBHOOK_EVENT_DESCRIPTIONS,
        }
    )
    return templates.TemplateResponse("admin/integrations/webhooks/detail.html", context)
```

Add imports at the top if missing:
```python
from app.models.webhook import WebhookDelivery, WebhookSubscription
```

- [ ] **Step 4: Create the picker partial**

`templates/admin/integrations/webhooks/_subscription_picker.html`:
```html
<details class="rounded-2xl border border-slate-200 bg-slate-50 px-5 py-3 dark:border-slate-700 dark:bg-slate-900/40">
    <summary class="cursor-pointer text-sm font-semibold text-slate-700 dark:text-slate-200">Add subscription</summary>
    <div class="mt-4 space-y-4">
        {% for label, items in event_groups %}
        <div>
            <div class="text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">{{ label }}</div>
            <div class="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {% for et in items %}
                {% if et not in subscribed_events %}
                <form method="post" action="/admin/integrations/webhooks/{{ endpoint.id }}/subscriptions" class="rounded-xl border border-slate-200 bg-white px-3 py-2 hover:border-primary-300 dark:border-slate-700 dark:bg-slate-800">
                    {% include "components/forms/csrf_input.html" %}
                    <input type="hidden" name="event_type" value="{{ et.value }}" />
                    <button type="submit" class="block w-full text-left">
                        <div class="font-mono text-xs text-slate-900 dark:text-white">{{ et.value }}</div>
                        <div class="mt-1 text-xs text-slate-500 dark:text-slate-400">{{ event_descriptions.get(et, '') }}</div>
                    </button>
                </form>
                {% endif %}
                {% endfor %}
            </div>
        </div>
        {% endfor %}
    </div>
</details>
```

- [ ] **Step 5: Update the detail template**

Open `templates/admin/integrations/webhooks/detail.html`. Read the existing file once. Then make these changes (do NOT replace the whole file — preserve structure):

a) Just below the page header (before the existing subscriptions/deliveries cards), add the action row:

```html
<div class="flex flex-wrap items-center gap-2">
    <a href="/admin/integrations/webhooks/{{ endpoint.id }}/edit" class="inline-flex items-center rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300">Edit</a>
    <form method="post" action="/admin/integrations/webhooks/{{ endpoint.id }}/test" class="inline">
        {% include "components/forms/csrf_input.html" %}
        <button type="submit" class="inline-flex items-center rounded-xl border border-primary-300 bg-primary-50 px-3 py-2 text-sm font-medium text-primary-700 hover:bg-primary-100 dark:border-primary-700 dark:bg-primary-900/30 dark:text-primary-200">Send test event</button>
    </form>
    <form method="post" action="/admin/integrations/webhooks/{{ endpoint.id }}/rotate-secret" class="inline" onsubmit="return confirm('Generate a new signing secret? The old secret will stop working immediately.')">
        {% include "components/forms/csrf_input.html" %}
        <button type="submit" class="inline-flex items-center rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-sm font-medium text-amber-700 hover:bg-amber-100 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-200">Rotate secret</button>
    </form>
    <a href="/admin/integrations/webhooks/{{ endpoint.id }}/deliveries" class="inline-flex items-center rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300">View delivery log</a>
</div>
```

b) Replace the existing "Subscriptions" tag-list block with the editor — render each subscription as a chip with a delete form, and include the picker partial at the bottom:

```html
<div class="space-y-3">
    <h2 class="font-semibold text-slate-900 dark:text-white">Subscriptions</h2>
    {% if subscriptions %}
    <div class="flex flex-wrap gap-2">
        {% for s in subscriptions %}
        <form method="post" action="/admin/integrations/webhooks/{{ endpoint.id }}/subscriptions/{{ s.id }}/delete" class="inline">
            {% include "components/forms/csrf_input.html" %}
            <button type="submit" title="Remove subscription" class="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-1 font-mono text-xs text-slate-700 hover:border-rose-300 hover:bg-rose-50 hover:text-rose-700 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:border-rose-700 dark:hover:bg-rose-900/30">
                {{ s.event_type.value if s.event_type else '' }} ×
            </button>
        </form>
        {% endfor %}
    </div>
    {% else %}
    <p class="text-sm text-slate-500 dark:text-slate-400">No subscriptions yet. Add one below.</p>
    {% endif %}
    {% include "admin/integrations/webhooks/_subscription_picker.html" %}
</div>
```

- [ ] **Step 6: Run tests**

```
poetry run pytest tests/test_webhook_admin_web.py -x -q
```
Expected: 9 passed.

- [ ] **Step 7: Commit**

```
git add templates/admin/integrations/webhooks/detail.html templates/admin/integrations/webhooks/_subscription_picker.html app/web/admin/integrations.py tests/test_webhook_admin_web.py
git commit -m "feat(webhooks): detail page action row + subscription editor"
```

---

### Task 13: Health panel + status badges on list page

**Files:**
- Modify: `app/web/admin/integrations.py` (existing `connectors_webhooks` / `webhooks_list` route — replace `list_all` with `list_with_stats`)
- Modify: `templates/admin/integrations/webhooks/index.html`
- Modify: `tests/test_webhook_admin_web.py`

- [ ] **Step 1: Append failing test**

```python
# tests/test_webhook_admin_web.py — append
def test_list_page_renders_health_panel(db_session, monkeypatch):
    monkeypatch.setattr("app.tasks.webhooks.deliver_webhook.delay", lambda *_a, **_k: None)
    webhook_service.webhook_endpoints.create(
        db_session,
        WebhookEndpointCreate(name="H1", url="https://hooks.example.com/h1"),
    )
    client = TestClient(app)
    resp = client.get("/admin/integrations/webhooks")
    assert resp.status_code == 200
    assert "Active endpoints" in resp.text
    assert "24h success" in resp.text
    assert "24h failures" in resp.text
```

- [ ] **Step 2: Run, expect failure**

```
poetry run pytest tests/test_webhook_admin_web.py::test_list_page_renders_health_panel -x -q
```
Expected: FAIL.

- [ ] **Step 3: Update the list route**

Find the existing `GET /webhooks` list route in `app/web/admin/integrations.py`. Replace its body so it uses `list_with_stats` and computes the health panel:

```python
@router.get("/webhooks", response_class=HTMLResponse)
def webhooks_list(request: Request, db: Session = Depends(get_db)):
    rows = webhook_service.webhook_endpoints.list_with_stats(db, limit=100, offset=0)
    total_24h_delivered = sum(r.last_24h_delivered for r in rows)
    total_24h_failed = sum(r.last_24h_failed for r in rows)
    total_active = sum(1 for r in rows if r.endpoint.is_active)
    success_rate = (
        round(100 * total_24h_delivered / (total_24h_delivered + total_24h_failed))
        if (total_24h_delivered + total_24h_failed) > 0
        else None
    )
    context = _base_context(request, db, active_page="webhooks")
    context.update(
        {
            "endpoint_rows": rows,
            "stats": {
                "active": total_active,
                "delivered_24h": total_24h_delivered,
                "failed_24h": total_24h_failed,
                "success_rate": success_rate,
            },
        }
    )
    return templates.TemplateResponse("admin/integrations/webhooks/index.html", context)
```

- [ ] **Step 4: Update the index template**

Open `templates/admin/integrations/webhooks/index.html`. The template currently iterates `endpoints`. Update so it iterates `endpoint_rows` (each row has `.endpoint`, `.last_24h_delivered`, `.last_24h_failed`, `.pending_count`, `.last_delivery_at`) and prepend a health panel.

a) Just below the page header section, insert:

```html
<div class="grid gap-4 sm:grid-cols-3">
    <div class="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-700 dark:bg-slate-800">
        <div class="text-xs font-semibold uppercase tracking-wide text-slate-400">Active endpoints</div>
        <div class="mt-2 font-display text-3xl font-bold text-slate-900 dark:text-white">{{ stats.active }}</div>
    </div>
    <div class="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-700 dark:bg-slate-800">
        <div class="text-xs font-semibold uppercase tracking-wide text-slate-400">24h success</div>
        <div class="mt-2 font-display text-3xl font-bold text-emerald-600 dark:text-emerald-400">{{ stats.success_rate ~ '%' if stats.success_rate is not none else '—' }}</div>
        <div class="mt-1 text-xs text-slate-500">{{ stats.delivered_24h }} delivered</div>
    </div>
    <div class="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-700 dark:bg-slate-800">
        <div class="text-xs font-semibold uppercase tracking-wide text-slate-400">24h failures</div>
        <div class="mt-2 font-display text-3xl font-bold text-rose-600 dark:text-rose-400">{{ stats.failed_24h }}</div>
    </div>
</div>
```

b) Replace the existing `{% for endpoint in endpoints %}` loop with one that handles `endpoint_rows`. Inside the loop:

- Use `endpoint = row.endpoint` (set with `{% set endpoint = row.endpoint %}`).
- Render a status badge derived from `row`:

```html
{% set status = (
    'inactive' if not endpoint.is_active
    else ('failing' if row.last_24h_failed > 0 and row.last_24h_delivered == 0
        else ('degraded' if row.last_24h_failed > 0
            else ('healthy' if row.last_24h_delivered > 0
                else 'idle')))
) %}
{% set badge = {
    'inactive': 'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-200',
    'failing': 'bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300',
    'degraded': 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
    'healthy': 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
    'idle': 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300',
} %}
<span class="inline-flex items-center rounded-lg px-2 py-1 text-xs font-semibold {{ badge[status] }}">{{ status }}</span>
```

Also add a column "Last delivery" rendering `row.last_delivery_at.strftime('%Y-%m-%d %H:%M') if row.last_delivery_at else '—'`.

- [ ] **Step 5: Run tests**

```
poetry run pytest tests/test_webhook_admin_web.py -x -q
```
Expected: 10 passed.

- [ ] **Step 6: Commit**

```
git add app/web/admin/integrations.py templates/admin/integrations/webhooks/index.html tests/test_webhook_admin_web.py
git commit -m "feat(webhooks): list page health panel and status badges"
```

---

### Task 14: Audit-event coverage test

**Files:**
- Modify: `tests/test_webhook_admin_web.py`

- [ ] **Step 1: Append failing test**

```python
# tests/test_webhook_admin_web.py — append
from app.models.audit import AuditEvent


def test_admin_actions_emit_audit_events(db_session, monkeypatch):
    sent = []
    monkeypatch.setattr("app.tasks.webhooks.deliver_webhook.delay", lambda did: sent.append(did))
    endpoint = webhook_service.webhook_endpoints.create(
        db_session,
        WebhookEndpointCreate(name="Aud", url="https://hooks.example.com/aud"),
    )
    client = TestClient(app)

    client.post(
        f"/admin/integrations/webhooks/{endpoint.id}",
        data={"name": "Aud2", "url": "https://hooks.example.com/aud", "is_active": "true"},
        follow_redirects=False,
    )
    client.post(f"/admin/integrations/webhooks/{endpoint.id}/rotate-secret", data={})
    client.post(f"/admin/integrations/webhooks/{endpoint.id}/test", data={}, follow_redirects=False)
    client.post(
        f"/admin/integrations/webhooks/{endpoint.id}/subscriptions",
        data={"event_type": "ticket.created"},
        follow_redirects=False,
    )

    actions = [a for (a,) in db_session.query(AuditEvent.action).all()]
    assert "webhook_endpoint_updated" in actions
    assert "webhook_endpoint_secret_rotated" in actions
    assert "webhook_endpoint_test_fired" in actions
    assert "webhook_subscription_added" in actions
```

- [ ] **Step 2: Run test**

```
poetry run pytest tests/test_webhook_admin_web.py::test_admin_actions_emit_audit_events -x -q
```

If all earlier tasks were implemented correctly, this should already PASS because audit calls were added in Tasks 7-11. If it fails, locate the missing `log_audit_event` call site for the named action and add it.

- [ ] **Step 3: Commit**

```
git add tests/test_webhook_admin_web.py
git commit -m "test(webhooks): assert admin actions emit AuditEvents"
```

---

### Task 15: Retire `/admin/system/webhooks*` via 308 redirects

**Files:**
- Modify: `app/web/admin/system.py`
- Delete: `templates/admin/system/webhooks.html`
- Delete: `templates/admin/system/webhook_form.html`
- Modify: `tests/test_webhook_admin_web.py`

- [ ] **Step 1: Append failing tests**

```python
# tests/test_webhook_admin_web.py — append
import pytest


@pytest.mark.parametrize(
    "old,new",
    [
        ("/admin/system/webhooks", "/admin/integrations/webhooks"),
        ("/admin/system/webhooks/new", "/admin/integrations/webhooks/new"),
    ],
)
def test_system_webhook_get_routes_redirect_308(old, new):
    client = TestClient(app)
    resp = client.get(old, follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["location"] == new


def test_system_webhook_id_get_redirect_308(db_session, monkeypatch):
    monkeypatch.setattr("app.tasks.webhooks.deliver_webhook.delay", lambda *_a, **_k: None)
    endpoint = webhook_service.webhook_endpoints.create(
        db_session,
        WebhookEndpointCreate(name="R", url="https://hooks.example.com/r"),
    )
    client = TestClient(app)
    resp = client.get(f"/admin/system/webhooks/{endpoint.id}/edit", follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["location"] == f"/admin/integrations/webhooks/{endpoint.id}/edit"
```

- [ ] **Step 2: Run, expect failure**

```
poetry run pytest tests/test_webhook_admin_web.py -x -q -k "redirect"
```
Expected: FAIL.

- [ ] **Step 3: Replace the system route bodies**

In `app/web/admin/system.py`, locate the six `/admin/system/webhooks*` routes (`webhooks_list`, `webhook_new`, `webhook_create`, `webhook_edit`, `webhook_update`, and the redirect target around lines 2766-2935). Delete their bodies and replace with redirects to the integrations equivalents:

```python
from fastapi.responses import RedirectResponse


@router.get("/system/webhooks")
def system_webhooks_list_redirect():
    return RedirectResponse(url="/admin/integrations/webhooks", status_code=308)


@router.get("/system/webhooks/new")
def system_webhook_new_redirect():
    return RedirectResponse(url="/admin/integrations/webhooks/new", status_code=308)


@router.post("/system/webhooks")
def system_webhook_create_redirect():
    return RedirectResponse(url="/admin/integrations/webhooks", status_code=308)


@router.get("/system/webhooks/{endpoint_id}/edit")
def system_webhook_edit_redirect(endpoint_id: str):
    return RedirectResponse(
        url=f"/admin/integrations/webhooks/{endpoint_id}/edit", status_code=308
    )


@router.post("/system/webhooks/{endpoint_id}")
def system_webhook_update_redirect(endpoint_id: str):
    return RedirectResponse(
        url=f"/admin/integrations/webhooks/{endpoint_id}", status_code=308
    )
```

Note: the router for `system.py` already has a prefix; verify the prefix (likely `/admin`) and adjust paths to match. If the prefix already includes `/admin/system`, drop the `/system` prefix from each path above. Run the tests; if the redirect URL is wrong they'll surface it.

Delete the legacy templates:
```
git rm templates/admin/system/webhooks.html templates/admin/system/webhook_form.html
```

- [ ] **Step 4: Run tests**

```
poetry run pytest tests/test_webhook_admin_web.py -x -q
```
Expected: all tests pass.

- [ ] **Step 5: Verify no internal links still point at the old paths**

```
grep -rn "/admin/system/webhooks" app templates tests
```
Expected output: no matches outside the redirect handlers themselves.

If any matches remain, update them to point at `/admin/integrations/webhooks` and re-run tests.

- [ ] **Step 6: Commit**

```
git add app/web/admin/system.py tests/test_webhook_admin_web.py
git rm --cached templates/admin/system/webhooks.html templates/admin/system/webhook_form.html 2>/dev/null || true
git commit -m "feat(webhooks): retire /admin/system/webhooks* in favor of integrations"
```

---

### Task 16: Playwright happy-path E2E

**Files:**
- Create: `tests/playwright/e2e/test_webhook_admin.py`

- [ ] **Step 1: Write the test**

```python
# tests/playwright/e2e/test_webhook_admin.py
import pytest

pytestmark = pytest.mark.playwright


def test_create_subscribe_test_replay(admin_page):
    """Happy path: create endpoint → add subscription → test-fire → see delivery → replay."""
    admin_page.goto("/admin/integrations/webhooks/new")
    admin_page.fill('input[name="name"]', "E2E Endpoint")
    admin_page.fill('input[name="url"]', "https://example.invalid/e2e")
    admin_page.click('button[type="submit"]')

    # Now on detail page
    admin_page.wait_for_url("**/admin/integrations/webhooks/*")
    admin_page.get_by_role("button", name="Add subscription").click()
    admin_page.locator('form[action$="/subscriptions"] >> text=ticket.created').first.click()

    # Fire a test event
    admin_page.get_by_role("button", name="Send test event").click()
    admin_page.wait_for_url("**/deliveries**")

    # First delivery row visible
    row = admin_page.locator("tbody tr").first
    assert "custom" in row.inner_text()

    # Replay
    row.get_by_role("button", name="Replay").click()
    admin_page.wait_for_url("**/deliveries**")
    rows = admin_page.locator("tbody tr")
    assert rows.count() >= 2
```

- [ ] **Step 2: Run**

```
poetry run pytest tests/playwright/e2e/test_webhook_admin.py -v
```

If the test infrastructure isn't configured for Playwright in CI, mark this xfail with a note and proceed — the unit/integration tests above already cover the surface.

- [ ] **Step 3: Commit**

```
git add tests/playwright/e2e/test_webhook_admin.py
git commit -m "test(webhooks): playwright happy-path for admin UX"
```

---

### Task 17: Verification pass

- [ ] **Step 1: Full lint, type, test**

```
poetry run ruff check app/ tests/ --fix
poetry run ruff format app/ tests/
poetry run mypy app/services/webhook.py app/services/webhook_events.py app/web/admin/integrations.py
poetry run pytest tests/test_webhook_admin_services.py tests/test_webhook_admin_web.py tests/test_webhook_services.py -v
```

Fix any reported issues before continuing.

- [ ] **Step 2: Sidebar & cross-links sweep**

```
grep -rn "/admin/system/webhooks\|admin.system.webhooks" app templates
```
Should return only the redirect stubs in `system.py`. If any other reference exists, update it to point at `/admin/integrations/webhooks`.

- [ ] **Step 3: Commit any cleanup**

```
git add -p
git commit -m "chore(webhooks): post-implementation lint/cleanup"
```

---

## Self-Review (against spec)

- §3 Audience & permissions — CSRF input included in every POST form (Tasks 7-12). Audit covered (Task 14). Permission gates explicitly deferred (spec §13).
- §4 Information architecture — all 11 canonical routes implemented (Tasks 7-13). All 5 redirected routes implemented (Task 15).
- §5.1 Health panel + status badges — Task 13.
- §5.2 Detail page action row + subscription editor — Task 12.
- §5.3 Edit page — Task 7.
- §5.4 Rotate secret with one-time reveal — Task 8.
- §5.5 Test-fire — Task 9 (service: Task 3).
- §5.6 Delivery log with filters + replay — Task 11 (service: Tasks 4-5).
- §6 Service additions — `rotate_secret` (Task 2), `send_test` (Task 3), `replay` (Task 4), `list_filtered` (Task 5), `list_with_stats` + `EndpointStats` (Task 6).
- §7 Audit events — every action emits an event (Tasks 7-12), checked by Task 14.
- §8 Data flow — matches sub-task implementations.
- §9 Error handling — form re-render with error on edit POST (Task 7). Filter parsing is tolerant in deliveries route (Task 11).
- §10 Tests — service tests (Tasks 1-6), route tests (Tasks 7-15), Playwright (Task 16).
- §11 Migration / rollout — no DB migration; redirects shipped in Task 15; cross-link sweep in Task 17.
