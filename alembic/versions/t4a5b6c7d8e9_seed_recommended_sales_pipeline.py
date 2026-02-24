"""Seed recommended sales pipeline when none exists

Revision ID: t4a5b6c7d8e9
Revises: s2a3b4c5d6e7
Create Date: 2026-02-16 14:20:00.000000

"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "t4a5b6c7d8e9"
down_revision = "s2a3b4c5d6e7"
branch_labels = None
depends_on = None

_RECOMMENDED_STAGES: list[tuple[str, int]] = [
    ("Lead Identified", 10),
    ("Qualification Call Completed", 20),
    ("Needs Assessment / Demo", 35),
    ("Proposal Sent", 50),
    ("Commercial Negotiation", 70),
    ("Decision Pending", 85),
    ("Closed Won", 100),
    ("Closed Lost", 0),
]


def _now() -> datetime:
    return datetime.now(UTC)


def upgrade() -> None:
    conn = op.get_bind()

    pipelines = sa.table(
        "crm_pipelines",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("is_active", sa.Boolean()),
        sa.column("metadata", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    stages = sa.table(
        "crm_pipeline_stages",
        sa.column("id", sa.Uuid()),
        sa.column("pipeline_id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("order_index", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
        sa.column("default_probability", sa.Integer()),
        sa.column("metadata", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    active_count = conn.execute(
        sa.select(sa.func.count()).select_from(pipelines).where(pipelines.c.is_active.is_(True))
    ).scalar_one()
    if int(active_count or 0) > 0:
        return

    pipeline_id = conn.execute(
        sa.select(pipelines.c.id).where(sa.func.lower(pipelines.c.name) == "recommended pipeline")
    ).scalar_one_or_none()
    now = _now()
    if pipeline_id is None:
        pipeline_id = uuid4()
        conn.execute(
            pipelines.insert().values(
                id=pipeline_id,
                name="Recommended Pipeline",
                is_active=True,
                metadata=None,
                created_at=now,
                updated_at=now,
            )
        )
    else:
        conn.execute(
            pipelines.update()
            .where(pipelines.c.id == pipeline_id)
            .values(is_active=True, updated_at=now)
        )

    existing_stage_names = set(
        conn.execute(
            sa.select(stages.c.name).where(stages.c.pipeline_id == pipeline_id).where(stages.c.is_active.is_(True))
        ).scalars()
    )
    for index, (name, probability) in enumerate(_RECOMMENDED_STAGES):
        if name in existing_stage_names:
            continue
        conn.execute(
            stages.insert().values(
                id=uuid4(),
                pipeline_id=pipeline_id,
                name=name,
                order_index=index,
                is_active=True,
                default_probability=probability,
                metadata=None,
                created_at=now,
                updated_at=now,
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    pipelines = sa.table(
        "crm_pipelines",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
    )
    stages = sa.table(
        "crm_pipeline_stages",
        sa.column("pipeline_id", sa.Uuid()),
    )

    pipeline_id = conn.execute(
        sa.select(pipelines.c.id).where(sa.func.lower(pipelines.c.name) == "recommended pipeline")
    ).scalar_one_or_none()
    if pipeline_id is None:
        return

    conn.execute(stages.delete().where(stages.c.pipeline_id == pipeline_id))
    conn.execute(pipelines.delete().where(pipelines.c.id == pipeline_id))
