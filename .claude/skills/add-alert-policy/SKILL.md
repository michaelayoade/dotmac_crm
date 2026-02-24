---
name: add-alert-policy
description: Create notification alert policies with escalation chains, on-call rotations, and severity routing
arguments:
  - name: policy_info
    description: "Alert policy purpose (e.g. 'fiber cut escalation with on-call rotation and SMS fallback')"
---

# Add Alert Policy

Create a notification alert policy with escalation chains for the DotMac Omni CRM.

## Steps

### 1. Understand the request
Parse `$ARGUMENTS` to determine:
- **Alert type**: network alert, ticket SLA breach, work order overdue, system health
- **Severity levels**: info, warning, critical, emergency
- **Channels**: email, sms, push, whatsapp, webhook
- **Escalation**: single notification, escalation chain with delays, on-call rotation
- **Recipients**: specific person, role-based, on-call rotation member

### 2. Study the existing patterns
Read these reference files:

- **Notification models**: `app/models/notification.py` — `AlertNotificationPolicy`, `AlertNotificationPolicyStep`, `OnCallRotation`, `OnCallRotationMember`, `AlertNotificationLog`, `Notification`, `NotificationTemplate`
- **Notification service**: `app/services/notification.py` — `NotificationService` with CRUD + bulk create
- **Notification enums**: `AlertSeverity` (info/warning/critical/emergency), `AlertStatus` (open/acknowledged/resolved), `NotificationChannel` (email/sms/push/whatsapp/webhook), `NotificationStatus` (queued/sending/delivered/failed/canceled)
- **Template seeding**: `scripts/seed_notification_templates.py` — template creation patterns
- **Automation actions**: `app/services/automation_actions.py` — `send_notification` action type

### 3. Understand the data model

**AlertNotificationPolicy:**
```
id, name (unique), channel, recipient, rule_id, device_id, interface_id,
template_id (FK → notification_templates), severity_min, status, is_active, notes
→ has many: steps (AlertNotificationPolicyStep), notifications (AlertNotificationLog)
```

**AlertNotificationPolicyStep:**
```
id, policy_id (FK), step_order, delay_minutes, channel, recipient,
rotation_id (FK → on_call_rotations), template_id (FK), is_active
→ belongs to: policy, rotation
```

**OnCallRotation:**
```
id, name (unique), timezone, is_active, notes
→ has many: members (OnCallRotationMember), policy_steps
```

**OnCallRotationMember:**
```
id, rotation_id (FK), name, contact, priority, last_used_at, is_active
```

**NotificationTemplate:**
```
id, name, code (unique), channel, subject, body, is_active
```

### 4. Create the notification template
If a custom template is needed, add to `scripts/seed_notification_templates.py` or create via service:

```python
from app.models.notification import NotificationChannel, NotificationTemplate

template = NotificationTemplate(
    name="{Alert Name} Notification",
    code="{alert_code}_notification",
    channel=NotificationChannel.{channel},
    subject="{Alert Subject} - {{ severity }} Alert",
    body="""\
{{ alert_title }}

Severity: {{ severity }}
Entity: {{ entity_type }} #{{ entity_id }}
Time: {{ timestamp }}

{{ alert_message }}

---
Action required: {{ action_url }}
""",
    is_active=True,
)
db.add(template)
db.commit()
```

Template variables use Jinja2 syntax and are rendered by the notification service.

### 5. Create the alert policy

**Simple policy (single notification):**
```python
from app.models.notification import (
    AlertNotificationPolicy,
    AlertSeverity,
    NotificationChannel,
)

policy = AlertNotificationPolicy(
    name="{Policy Name}",
    channel=NotificationChannel.email,
    recipient="ops-team@company.com",
    severity_min=AlertSeverity.warning,
    template_id=template.id,
    is_active=True,
    notes="Notify ops team on warning+ alerts for {scope}",
)
db.add(policy)
db.commit()
```

**Escalation chain (multi-step with delays):**
```python
from app.models.notification import AlertNotificationPolicyStep

policy = AlertNotificationPolicy(
    name="{Escalation Policy Name}",
    channel=NotificationChannel.email,
    recipient="l1-support@company.com",  # Initial recipient
    severity_min=AlertSeverity.warning,
    template_id=template.id,
    is_active=True,
)
db.add(policy)
db.flush()

# Step 1: Immediate email to L1
step1 = AlertNotificationPolicyStep(
    policy_id=policy.id,
    step_order=1,
    delay_minutes=0,
    channel=NotificationChannel.email,
    recipient="l1-support@company.com",
    template_id=template.id,
    is_active=True,
)

# Step 2: SMS after 15 minutes if not acknowledged
step2 = AlertNotificationPolicyStep(
    policy_id=policy.id,
    step_order=2,
    delay_minutes=15,
    channel=NotificationChannel.sms,
    recipient="+2348012345678",
    template_id=sms_template.id,
    is_active=True,
)

# Step 3: On-call rotation after 30 minutes
step3 = AlertNotificationPolicyStep(
    policy_id=policy.id,
    step_order=3,
    delay_minutes=30,
    channel=NotificationChannel.sms,
    recipient="",  # Resolved from rotation
    rotation_id=rotation.id,
    template_id=sms_template.id,
    is_active=True,
)

db.add_all([step1, step2, step3])
db.commit()
```

### 6. Create on-call rotation (if needed)

```python
from app.models.notification import OnCallRotation, OnCallRotationMember

rotation = OnCallRotation(
    name="{Rotation Name}",
    timezone="Africa/Lagos",
    is_active=True,
    notes="Primary on-call rotation for {scope}",
)
db.add(rotation)
db.flush()

members = [
    OnCallRotationMember(
        rotation_id=rotation.id,
        name="Alice Engineer",
        contact="+2348012345678",
        priority=1,
        is_active=True,
    ),
    OnCallRotationMember(
        rotation_id=rotation.id,
        name="Bob Technician",
        contact="+2348087654321",
        priority=2,
        is_active=True,
    ),
]
db.add_all(members)
db.commit()
```

### 7. Create the service methods
Add to `app/services/notification.py` or create `app/services/alert_policies.py`:

```python
class AlertPolicies(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: AlertPolicyCreate) -> AlertNotificationPolicy:
        policy = AlertNotificationPolicy(**payload.model_dump())
        db.add(policy)
        db.commit()
        db.refresh(policy)
        return policy

    @staticmethod
    def get(db: Session, policy_id: str) -> AlertNotificationPolicy:
        policy = db.get(AlertNotificationPolicy, coerce_uuid(policy_id))
        if not policy or not policy.is_active:
            raise HTTPException(status_code=404, detail="Policy not found")
        return policy

    @staticmethod
    def fire_alert(
        db: Session,
        *,
        policy_id: str,
        alert_id: str | None = None,
        severity: AlertSeverity,
        context: dict,
    ) -> list[Notification]:
        """Execute alert policy: send initial notification, queue escalation steps."""
        policy = AlertPolicies.get(db, policy_id)

        if severity.value < policy.severity_min.value:
            return []  # Below minimum severity threshold

        notifications = []

        # Send immediate notification (step 0 / policy default)
        notification = _send_alert_notification(
            db, policy.channel, policy.recipient, policy.template_id, context
        )
        notifications.append(notification)

        # Log the alert
        log = AlertNotificationLog(
            alert_id=coerce_uuid(alert_id) if alert_id else None,
            policy_id=policy.id,
            notification_id=notification.id,
        )
        db.add(log)

        # Queue escalation steps via Celery delayed tasks
        for step in sorted(policy.steps, key=lambda s: s.step_order):
            if not step.is_active:
                continue
            if step.delay_minutes > 0:
                from app.tasks.notifications import send_escalation_step
                send_escalation_step.apply_async(
                    args=[str(step.id), str(log.id), context],
                    countdown=step.delay_minutes * 60,
                )
            else:
                recipient = _resolve_step_recipient(db, step)
                n = _send_alert_notification(
                    db, step.channel, recipient, step.template_id, context
                )
                notifications.append(n)

        db.commit()
        return notifications


def _resolve_step_recipient(db: Session, step: AlertNotificationPolicyStep) -> str:
    """Resolve recipient from step config or on-call rotation."""
    if step.recipient:
        return step.recipient
    if step.rotation_id:
        rotation = db.get(OnCallRotation, step.rotation_id)
        if rotation:
            member = (
                db.query(OnCallRotationMember)
                .filter(
                    OnCallRotationMember.rotation_id == rotation.id,
                    OnCallRotationMember.is_active.is_(True),
                )
                .order_by(
                    OnCallRotationMember.last_used_at.asc().nullsfirst(),
                    OnCallRotationMember.priority.asc(),
                )
                .first()
            )
            if member:
                member.last_used_at = datetime.now(UTC)
                return member.contact
    return ""


alert_policies = AlertPolicies()
```

### 8. Create migration (if new tables needed)
```bash
alembic revision --autogenerate -m "Add alert policy escalation steps"
```

### 9. Write tests
```python
def test_fire_alert_sends_notification(db_session):
    """Firing an alert creates a notification."""

def test_escalation_chain_queues_delayed_steps(db_session):
    """Escalation steps with delay_minutes > 0 are queued via Celery."""

def test_on_call_rotation_round_robin(db_session):
    """On-call rotation picks the member with earliest last_used_at."""

def test_severity_filter_skips_low_severity(db_session):
    """Alert below policy's severity_min is skipped."""
```

### 10. Verify
```bash
ruff check app/services/notification.py --fix
ruff format app/services/notification.py
python3 -c "from app.models.notification import AlertNotificationPolicy, AlertNotificationPolicyStep"
pytest tests/test_alert_policies.py -v
```

### 11. Checklist
- [ ] Policy has `severity_min` filter (don't send for low-severity alerts)
- [ ] Escalation steps ordered by `step_order`
- [ ] On-call rotation uses round-robin via `last_used_at`
- [ ] Delayed escalation steps use Celery `apply_async(countdown=...)`
- [ ] `AlertNotificationLog` created for audit trail
- [ ] Template variables documented for each template
- [ ] All channels validated against `NotificationChannel` enum
- [ ] Policy name is unique (DB constraint)
- [ ] Rotation name is unique (DB constraint)
