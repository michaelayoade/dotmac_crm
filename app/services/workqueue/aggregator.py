"""Workqueue aggregator — merges provider output and ranks items."""

from __future__ import annotations

import logging
from itertools import chain

from sqlalchemy.orm import Session

from app.services.workqueue.permissions import resolve_audience
from app.services.workqueue.providers import all_providers
from app.services.workqueue.providers.conversations import conversations_provider  # noqa: F401
from app.services.workqueue.providers.leads_quotes import leads_quotes_provider  # noqa: F401
from app.services.workqueue.providers.tasks import tasks_provider  # noqa: F401
from app.services.workqueue.providers.tickets import tickets_provider  # noqa: F401
from app.services.workqueue.scope import get_workqueue_scope
from app.services.workqueue.scoring_config import (
    DEFAULT_HERO_BAND_SIZE,
    KIND_ORDER,
    SECTION_ORDER,
)
from app.services.workqueue.snooze import workqueue_snooze
from app.services.workqueue.types import (
    ItemKind,
    WorkqueueSection,
    WorkqueueView,
)

logger = logging.getLogger(__name__)

PROVIDERS = tuple(all_providers())


def build_workqueue(
    db: Session,
    user,
    *,
    requested_audience: str | None = None,
    hero_band_size: int = DEFAULT_HERO_BAND_SIZE,
) -> WorkqueueView:
    audience = resolve_audience(user, requested_audience)
    scope = get_workqueue_scope(db, user, audience)
    snoozed_by_kind = workqueue_snooze.active_snoozed_ids(db, user.person_id)

    # Snoozes are tracked per-kind in the DB but providers may emit items of
    # multiple kinds (e.g. ``leads_quotes`` produces both leads and quotes).
    # Pass the union so each provider can correctly suppress any snoozed item
    # it owns; providers only check membership, so this is safe.
    all_snoozed: set = set().union(*snoozed_by_kind.values()) if snoozed_by_kind else set()

    items_by_kind: dict[ItemKind, list] = {k: [] for k in ItemKind}
    for provider in PROVIDERS:
        fetched = provider.fetch(
            db,
            user=user,
            audience=audience,
            scope=scope,
            snoozed_ids=all_snoozed,
        )
        logger.info(
            "workqueue_provider_results user_id=%s audience=%s kind=%s applied_filters=%s result_count=%s",
            user.person_id,
            audience.value,
            provider.kind.value,
            scope.applied_filters,
            len(fetched),
        )
        for it in fetched:
            items_by_kind[it.kind].append(it)

    all_items = list(chain.from_iterable(items_by_kind.values()))
    all_items.sort(key=lambda i: (-i.score, -i.happened_at.timestamp(), KIND_ORDER[i.kind]))
    right_now = tuple(all_items[:hero_band_size])

    sections = tuple(
        WorkqueueSection(kind=k, items=tuple(items_by_kind[k]), total=len(items_by_kind[k])) for k in SECTION_ORDER
    )

    return WorkqueueView(audience=audience, right_now=right_now, sections=sections)
