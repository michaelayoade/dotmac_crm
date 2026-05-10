"""Provider Protocol for Workqueue items."""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.workqueue.scoring_config import PROVIDER_LIMIT
from app.services.workqueue.types import ItemKind, WorkqueueAudience, WorkqueueItem


@runtime_checkable
class WorkqueueProvider(Protocol):
    kind: ItemKind

    def fetch(
        self,
        db: Session,
        *,
        user,
        audience: WorkqueueAudience,
        snoozed_ids: set[UUID],
        limit: int = PROVIDER_LIMIT,
    ) -> list[WorkqueueItem]: ...
