from __future__ import annotations

import logging

from app.services.ai.personas._base import PersonaSpec

logger = logging.getLogger(__name__)


class PersonaRegistry:
    def __init__(self) -> None:
        self._personas: dict[str, PersonaSpec] = {}

    def register(self, spec: PersonaSpec) -> None:
        if spec.key in self._personas:
            logger.warning("Persona %s already registered, overwriting", spec.key)
        self._personas[spec.key] = spec

    def get(self, key: str) -> PersonaSpec:
        spec = self._personas.get(key)
        if not spec:
            raise ValueError(f"Unknown persona: {key}")
        return spec

    def list_all(self) -> list[PersonaSpec]:
        return list(self._personas.values())

    def keys(self) -> list[str]:
        return list(self._personas.keys())


persona_registry = PersonaRegistry()
