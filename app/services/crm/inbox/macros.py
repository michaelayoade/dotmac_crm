"""CRUD service for CRM conversation macros."""

from __future__ import annotations

import builtins
import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.crm.enums import MacroActionType, MacroVisibility
from app.models.crm.macro import CrmConversationMacro
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


def _validate_actions(actions: list[dict[str, Any]]) -> None:
    """Validate that each action has a known action_type."""
    valid_types = {t.value for t in MacroActionType}
    for idx, action in enumerate(actions):
        action_type = action.get("action_type")
        if action_type not in valid_types:
            raise ValueError(f"Invalid action type at index {idx}: {action_type}")
        if "params" not in action:
            raise ValueError(f"Missing params at index {idx}")


class ConversationMacros(ListResponseMixin):
    @staticmethod
    def create(
        db: Session,
        *,
        name: str,
        description: str | None,
        visibility: MacroVisibility,
        actions: list[dict[str, Any]],
        created_by_agent_id: str,
    ) -> CrmConversationMacro:
        _validate_actions(actions)
        macro = CrmConversationMacro(
            name=name,
            description=description,
            visibility=visibility,
            actions=actions,
            created_by_agent_id=coerce_uuid(created_by_agent_id),
        )
        db.add(macro)
        db.commit()
        db.refresh(macro)
        return macro

    @staticmethod
    def get(db: Session, macro_id: str) -> CrmConversationMacro:
        macro = db.get(CrmConversationMacro, coerce_uuid(macro_id))
        if not macro:
            raise HTTPException(status_code=404, detail="Macro not found")
        return macro

    @staticmethod
    def update(
        db: Session,
        macro_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        visibility: MacroVisibility | None = None,
        actions: list[dict[str, Any]] | None = None,
        is_active: bool | None = None,
    ) -> CrmConversationMacro:
        macro = ConversationMacros.get(db, macro_id)
        if name is not None:
            macro.name = name
        if description is not None:
            macro.description = description
        if visibility is not None:
            macro.visibility = visibility
        if actions is not None:
            _validate_actions(actions)
            macro.actions = actions
        if is_active is not None:
            macro.is_active = is_active
        db.commit()
        db.refresh(macro)
        return macro

    @staticmethod
    def delete(db: Session, macro_id: str) -> None:
        macro = ConversationMacros.get(db, macro_id)
        macro.is_active = False
        db.commit()

    @staticmethod
    def list(
        db: Session,
        *,
        agent_id: str | None = None,
        visibility: str | None = None,
        is_active: bool | None = True,
        limit: int = 200,
        offset: int = 0,
    ) -> list[CrmConversationMacro]:
        query = db.query(CrmConversationMacro)
        if is_active is not None:
            query = query.filter(CrmConversationMacro.is_active == is_active)
        if agent_id:
            query = query.filter(CrmConversationMacro.created_by_agent_id == coerce_uuid(agent_id))
        if visibility:
            try:
                vis_enum = MacroVisibility(visibility)
                query = query.filter(CrmConversationMacro.visibility == vis_enum)
            except ValueError:
                pass
        query = query.order_by(
            CrmConversationMacro.execution_count.desc(),
            CrmConversationMacro.name.asc(),
        )
        return query.offset(offset).limit(limit).all()

    @staticmethod
    def list_for_agent(db: Session, agent_id: str) -> builtins.list[CrmConversationMacro]:
        """Return agent's personal macros + all shared macros, sorted by popularity."""
        agent_uuid = coerce_uuid(agent_id)
        query = db.query(CrmConversationMacro).filter(
            CrmConversationMacro.is_active.is_(True),
            or_(
                CrmConversationMacro.created_by_agent_id == agent_uuid,
                CrmConversationMacro.visibility == MacroVisibility.shared,
            ),
        )
        query = query.order_by(
            CrmConversationMacro.execution_count.desc(),
            CrmConversationMacro.name.asc(),
        )
        return query.limit(200).all()


conversation_macros = ConversationMacros()
