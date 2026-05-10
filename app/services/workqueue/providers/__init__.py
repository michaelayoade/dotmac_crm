"""Workqueue provider registry."""

from __future__ import annotations

from collections.abc import Iterable

from app.services.workqueue.providers.base import WorkqueueProvider

_PROVIDERS: list[WorkqueueProvider] = []


def register(provider: WorkqueueProvider) -> WorkqueueProvider:
    _PROVIDERS.append(provider)
    return provider


def all_providers() -> Iterable[WorkqueueProvider]:
    return tuple(_PROVIDERS)
