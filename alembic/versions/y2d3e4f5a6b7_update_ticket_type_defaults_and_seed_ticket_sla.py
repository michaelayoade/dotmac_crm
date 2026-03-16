"""update ticket type defaults and seed ticket SLA

Revision ID: y2d3e4f5a6b7
Revises: y1c2d3e4f5a6
Create Date: 2026-03-12 10:05:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "y2d3e4f5a6b7"
down_revision: str | Sequence[str] | None = "y1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOMAIN_SETTINGS_TABLE = sa.table(
    "domain_settings",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("domain", sa.String()),
    sa.column("key", sa.String()),
    sa.column("value_type", sa.String()),
    sa.column("value_text", sa.Text()),
    sa.column("value_json", sa.JSON()),
    sa.column("is_secret", sa.Boolean()),
    sa.column("is_active", sa.Boolean()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

SLA_POLICIES_TABLE = sa.table(
    "sla_policies",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("name", sa.String()),
    sa.column("entity_type", sa.String()),
    sa.column("description", sa.Text()),
    sa.column("is_active", sa.Boolean()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

SLA_TARGETS_TABLE = sa.table(
    "sla_targets",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("policy_id", UUID(as_uuid=True)),
    sa.column("priority", sa.String()),
    sa.column("target_minutes", sa.Integer()),
    sa.column("warning_minutes", sa.Integer()),
    sa.column("is_active", sa.Boolean()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

TICKET_PRIORITY_OVERRIDES = {
    "Bandwidth Complaint": "urgent",
    "Router Configuration": "high",
    "Slow Browsing/Intermittent Connectivity": "lower",
    "Billing Issues": "lower",
    "IP Authentication": "lower",
    "PPPOE Authentication": "lower",
    "LAN Troubleshooting": "medium",
    "Customer Link Disconnection": "medium",
    "Customer Realignment": "medium",
    "Router Troubleshooting": "medium",
    "AP/Air Fiber Realignment": "high",
    "AP/Air Fiber Outage": "high",
    "Cabinet Disconnection": "high",
    "Call Down Support": "low",
    "Cabinet Migration": "medium",
    "BTS Intermittent Connectivity": "high",
    "Core Link Disconnection": "high",
    "BTS Outage": "high",
    "Multiple Cabinet Disconnection": "high",
    "Power Optimization": "low",
}

REMOVED_TICKET_TYPES = {
    "ERPNext Outage",
    "Chatwoot Downtime",
}

TICKET_TYPE_ALIASES = {
    "Customer Link Disconection": "Customer Link Disconnection",
}

TICKET_TYPE_ORDER = [
    "Bandwidth Complaint",
    "Router Configuration",
    "Slow Browsing/Intermittent Connectivity",
    "Billing Issues",
    "IP Authentication",
    "PPPOE Authentication",
    "LAN Troubleshooting",
    "Customer Link Disconnection",
    "Customer Realignment",
    "Router Troubleshooting",
    "AP/Air Fiber Realignment",
    "AP/Air Fiber Outage",
    "Cabinet Disconnection",
    "Call Down Support",
    "Cabinet Migration",
    "BTS Intermittent Connectivity",
    "Core Link Disconnection",
    "BTS Outage",
    "Multiple Cabinet Disconnection",
    "Power Optimization",
]

TICKET_SLA_POLICY_NAME = "Ticket Resolution SLA"
TICKET_SLA_TARGETS = {
    "urgent": 360,
    "high": 240,
    "medium": 1440,
    "low": 2880,
    "lower": 120,
}


def _normalize_ticket_types(items: object) -> list[dict[str, object]]:
    normalized: dict[str, dict[str, object]] = {}
    extra_order: list[str] = []

    if not isinstance(items, list):
        items = []

    for item in items:
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get("name") or "").strip()
        if not raw_name:
            continue
        name = TICKET_TYPE_ALIASES.get(raw_name, raw_name)
        if name in REMOVED_TICKET_TYPES:
            continue
        if name not in normalized:
            normalized[name] = {
                "name": name,
                "priority": item.get("priority"),
                "is_active": bool(item.get("is_active", True)),
            }
            if name not in TICKET_PRIORITY_OVERRIDES:
                extra_order.append(name)
        else:
            normalized[name]["is_active"] = bool(normalized[name].get("is_active")) or bool(item.get("is_active", True))
            if normalized[name].get("priority") is None and item.get("priority") is not None:
                normalized[name]["priority"] = item.get("priority")

    for name, priority in TICKET_PRIORITY_OVERRIDES.items():
        normalized[name] = {
            "name": name,
            "priority": priority,
            "is_active": True,
        }

    ordered: list[dict[str, object]] = []
    for name in TICKET_TYPE_ORDER:
        if name in normalized:
            ordered.append(normalized.pop(name))
    for name in extra_order:
        if name in normalized:
            ordered.append(normalized.pop(name))
    for name in sorted(normalized):
        ordered.append(normalized[name])
    return ordered


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(UTC)

    existing_setting = conn.execute(
        sa.select(
            DOMAIN_SETTINGS_TABLE.c.id,
            DOMAIN_SETTINGS_TABLE.c.value_json,
        )
        .where(sa.cast(DOMAIN_SETTINGS_TABLE.c.domain, sa.String()) == "comms")
        .where(DOMAIN_SETTINGS_TABLE.c.key == "ticket_types")
    ).first()

    normalized_types = _normalize_ticket_types(existing_setting.value_json if existing_setting else [])
    if existing_setting:
        conn.execute(
            DOMAIN_SETTINGS_TABLE.update()
            .where(DOMAIN_SETTINGS_TABLE.c.id == existing_setting.id)
            .values(
                value_type=sa.text("'json'::settingvaluetype"),
                value_text=None,
                value_json=normalized_types,
                is_active=True,
                updated_at=now,
            )
        )
    else:
        conn.execute(
            DOMAIN_SETTINGS_TABLE.insert().values(
                id=uuid.uuid4(),
                domain=sa.text("'comms'::settingdomain"),
                key="ticket_types",
                value_type=sa.text("'json'::settingvaluetype"),
                value_text=None,
                value_json=normalized_types,
                is_secret=False,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )

    ticket_policy = conn.execute(
        sa.select(SLA_POLICIES_TABLE.c.id)
        .where(SLA_POLICIES_TABLE.c.name == TICKET_SLA_POLICY_NAME)
        .where(sa.cast(SLA_POLICIES_TABLE.c.entity_type, sa.String()) == "ticket")
    ).first()

    if ticket_policy:
        policy_id = ticket_policy.id
        conn.execute(
            SLA_POLICIES_TABLE.update()
            .where(SLA_POLICIES_TABLE.c.id == policy_id)
            .values(
                description="Priority-driven resolution SLA for support tickets.",
                is_active=True,
                updated_at=now,
            )
        )
    else:
        policy_id = uuid.uuid4()
        conn.execute(
            SLA_POLICIES_TABLE.insert().values(
                id=policy_id,
                name=TICKET_SLA_POLICY_NAME,
                entity_type=sa.text("'ticket'::workflowentitytype"),
                description="Priority-driven resolution SLA for support tickets.",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )

    existing_targets = {
        row.priority: row.id
        for row in conn.execute(
            sa.select(SLA_TARGETS_TABLE.c.id, SLA_TARGETS_TABLE.c.priority).where(
                SLA_TARGETS_TABLE.c.policy_id == policy_id
            )
        )
    }

    for priority, target_minutes in TICKET_SLA_TARGETS.items():
        warning_minutes = max(target_minutes // 2, 1)
        target_id = existing_targets.get(priority)
        if target_id:
            conn.execute(
                SLA_TARGETS_TABLE.update()
                .where(SLA_TARGETS_TABLE.c.id == target_id)
                .values(
                    target_minutes=target_minutes,
                    warning_minutes=warning_minutes,
                    is_active=True,
                    updated_at=now,
                )
            )
        else:
            conn.execute(
                SLA_TARGETS_TABLE.insert().values(
                    id=uuid.uuid4(),
                    policy_id=policy_id,
                    priority=priority,
                    target_minutes=target_minutes,
                    warning_minutes=warning_minutes,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )


def downgrade() -> None:
    conn = op.get_bind()
    ticket_policy = conn.execute(
        sa.select(SLA_POLICIES_TABLE.c.id)
        .where(SLA_POLICIES_TABLE.c.name == TICKET_SLA_POLICY_NAME)
        .where(sa.cast(SLA_POLICIES_TABLE.c.entity_type, sa.String()) == "ticket")
    ).first()
    if ticket_policy:
        conn.execute(SLA_TARGETS_TABLE.delete().where(SLA_TARGETS_TABLE.c.policy_id == ticket_policy.id))
        conn.execute(SLA_POLICIES_TABLE.delete().where(SLA_POLICIES_TABLE.c.id == ticket_policy.id))
