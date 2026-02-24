from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import MessageDirection
from app.models.crm.team import CrmAgent
from app.models.domain_settings import SettingDomain
from app.models.performance import AgentPerformanceReview, AgentPerformanceSnapshot
from app.models.person import Person
from app.models.tickets import Ticket
from app.models.workforce import WorkOrder
from app.services.ai import AIClientError, build_ai_client
from app.services.ai.prompts import build_performance_review_prompts
from app.services.ai.redaction import redact_text
from app.services.audit_helpers import log_audit_event
from app.services.common import coerce_uuid
from app.services.settings_spec import resolve_value


def _coerce_int(value: object | None, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return default
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _safe_json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {
        "summary": text.strip()[:500] or "No summary generated.",
        "strengths": [],
        "improvements": [],
        "recommendations": [],
        "callouts": [],
    }


class PerformanceReviewsService:
    def _llm_ready(self, db: Session) -> bool:
        provider = str(resolve_value(db, SettingDomain.integration, "llm_provider") or "vllm").strip().lower()
        if provider != "vllm":
            return False
        base_url = str(resolve_value(db, SettingDomain.integration, "vllm_base_url") or "").strip()
        model = str(resolve_value(db, SettingDomain.integration, "vllm_model") or "").strip()
        if not (base_url and model):
            return False
        require_key = bool(resolve_value(db, SettingDomain.integration, "vllm_require_api_key") or False)
        if require_key:
            api_key = str(resolve_value(db, SettingDomain.integration, "vllm_api_key") or "").strip()
            return bool(api_key)
        return True

    def _existing_review(
        self, db: Session, person_id: str, start_at: datetime, end_at: datetime
    ) -> AgentPerformanceReview | None:
        return (
            db.query(AgentPerformanceReview)
            .filter(
                AgentPerformanceReview.person_id == coerce_uuid(person_id),
                AgentPerformanceReview.review_period_start == start_at,
                AgentPerformanceReview.review_period_end == end_at,
            )
            .first()
        )

    def _latest_review(self, db: Session, person_id: str) -> AgentPerformanceReview | None:
        return (
            db.query(AgentPerformanceReview)
            .filter(AgentPerformanceReview.person_id == coerce_uuid(person_id))
            .order_by(AgentPerformanceReview.created_at.desc())
            .first()
        )

    def _build_evidence_samples(
        self, db: Session, person_id: str, period_start: datetime, period_end: datetime
    ) -> list[str]:
        max_chars = _coerce_int(resolve_value(db, SettingDomain.performance, "review_sample_max_chars"), 600)
        ticket_limit = _coerce_int(resolve_value(db, SettingDomain.performance, "review_sample_tickets"), 3)
        conversation_limit = _coerce_int(resolve_value(db, SettingDomain.performance, "review_sample_conversations"), 3)
        work_order_limit = _coerce_int(resolve_value(db, SettingDomain.performance, "review_sample_work_orders"), 2)

        samples: list[str] = []

        tickets = (
            db.query(Ticket)
            .filter(
                Ticket.assigned_to_person_id == coerce_uuid(person_id),
                Ticket.created_at >= period_start,
                Ticket.created_at <= period_end,
            )
            .order_by(Ticket.updated_at.desc())
            .limit(max(ticket_limit, 0))
            .all()
        )
        for ticket in tickets:
            summary = redact_text(f"{ticket.title or ''}. {ticket.description or ''}", max_chars=max_chars)
            samples.append(f"Ticket {ticket.number or str(ticket.id)[:8]} ({ticket.status.value}): {summary}")

        agent = (
            db.query(CrmAgent)
            .filter(CrmAgent.person_id == coerce_uuid(person_id), CrmAgent.is_active.is_(True))
            .first()
        )
        if agent and conversation_limit > 0:
            conv_ids = (
                db.query(ConversationAssignment.conversation_id)
                .filter(ConversationAssignment.agent_id == agent.id, ConversationAssignment.is_active.is_(True))
                .all()
            )
            convo_ids = [row[0] for row in conv_ids]
            if convo_ids:
                conversations = (
                    db.query(Conversation)
                    .filter(
                        Conversation.id.in_(convo_ids),
                        Conversation.created_at >= period_start,
                        Conversation.created_at <= period_end,
                    )
                    .order_by(Conversation.updated_at.desc())
                    .limit(conversation_limit)
                    .all()
                )
                for conversation in conversations:
                    first_message = (
                        db.query(Message)
                        .filter(Message.conversation_id == conversation.id)
                        .order_by(Message.created_at.asc())
                        .first()
                    )
                    preview = redact_text(
                        f"{conversation.subject or ''}. {(first_message.body if first_message else '') or ''}",
                        max_chars=max_chars,
                    )
                    samples.append(f"Conversation {str(conversation.id)[:8]} ({conversation.status.value}): {preview}")

        work_orders = (
            db.query(WorkOrder)
            .filter(
                WorkOrder.assigned_to_person_id == coerce_uuid(person_id),
                WorkOrder.created_at >= period_start,
                WorkOrder.created_at <= period_end,
            )
            .order_by(WorkOrder.updated_at.desc())
            .limit(max(work_order_limit, 0))
            .all()
        )
        for order in work_orders:
            notes = None
            if order.ticket_id:
                notes = (
                    db.query(Message.body)
                    .join(Conversation, Conversation.id == Message.conversation_id)
                    .filter(
                        Conversation.ticket_id == order.ticket_id,
                        Message.direction == MessageDirection.outbound,
                    )
                    .order_by(Message.created_at.desc())
                    .limit(1)
                    .first()
                )
            preview = redact_text(
                f"{order.title}. {order.description or ''}. {(notes[0] if notes else '') or ''}", max_chars=max_chars
            )
            samples.append(f"Work order {str(order.id)[:8]} ({order.status.value}): {preview}")

        return samples

    def _is_admin(self, requester_roles: list[str] | None) -> bool:
        role_set = {str(role).lower() for role in (requester_roles or [])}
        return "admin" in role_set

    def _manual_reviews_today(self, db: Session, requester_id: str) -> int:
        now = datetime.now(UTC)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return (
            db.query(AuditEvent)
            .filter(
                AuditEvent.action == "performance_review_generate_manual",
                AuditEvent.actor_id == str(requester_id),
                AuditEvent.occurred_at >= day_start,
                AuditEvent.occurred_at <= now,
                AuditEvent.is_success.is_(True),
            )
            .count()
        )

    def generate_for_person(
        self,
        db: Session,
        person_id: str,
        period_start: datetime,
        period_end: datetime,
        *,
        force: bool = False,
        source: str = "auto",
    ) -> AgentPerformanceReview:
        snapshot = (
            db.query(AgentPerformanceSnapshot)
            .filter(
                AgentPerformanceSnapshot.person_id == coerce_uuid(person_id),
                AgentPerformanceSnapshot.score_period_start == period_start,
                AgentPerformanceSnapshot.score_period_end == period_end,
            )
            .first()
        )
        if not snapshot:
            raise ValueError("No performance snapshot found for requested period")

        person = db.get(Person, coerce_uuid(person_id))
        if not person:
            raise ValueError("Person not found")

        existing = self._existing_review(db, person_id, period_start, period_end)
        if existing:
            return existing

        cooldown_hours = _coerce_int(resolve_value(db, SettingDomain.performance, "review_cooldown_hours"), 24)
        if source == "manual" and cooldown_hours > 0 and not force:
            latest = self._latest_review(db, person_id)
            if latest and latest.created_at and latest.created_at > datetime.now(UTC) - timedelta(hours=cooldown_hours):
                raise ValueError(f"Review cooldown active ({cooldown_hours}h)")

        max_tokens = _coerce_int(
            resolve_value(db, SettingDomain.integration, "vllm_max_tokens"),
            2048,
        )

        system_prompt, user_prompt = build_performance_review_prompts(
            person_name=person.display_name or f"{person.first_name} {person.last_name}".strip() or "Agent",
            period_start=period_start,
            period_end=period_end,
            composite_score=float(snapshot.composite_score),
            domain_scores=snapshot.domain_scores_json,
            evidence_samples=self._build_evidence_samples(db, person_id, period_start, period_end),
        )

        client = build_ai_client(db)
        try:
            ai_result = client.generate(system=system_prompt, prompt=user_prompt, max_tokens=max_tokens)
            parsed = _safe_json(ai_result.content)
            review = AgentPerformanceReview(
                person_id=coerce_uuid(person_id),
                review_period_start=period_start,
                review_period_end=period_end,
                composite_score=float(snapshot.composite_score),
                domain_scores_json=snapshot.domain_scores_json,
                summary_text=str(parsed.get("summary") or ""),
                strengths_json=parsed.get("strengths") if isinstance(parsed.get("strengths"), list) else [],
                improvements_json=parsed.get("improvements") if isinstance(parsed.get("improvements"), list) else [],
                recommendations_json=parsed.get("recommendations")
                if isinstance(parsed.get("recommendations"), list)
                else [],
                callouts_json=parsed.get("callouts") if isinstance(parsed.get("callouts"), list) else [],
                llm_model=ai_result.model,
                llm_provider=ai_result.provider,
                llm_tokens_in=ai_result.tokens_in,
                llm_tokens_out=ai_result.tokens_out,
            )
        except AIClientError as exc:
            review = AgentPerformanceReview(
                person_id=coerce_uuid(person_id),
                review_period_start=period_start,
                review_period_end=period_end,
                composite_score=float(snapshot.composite_score),
                domain_scores_json=snapshot.domain_scores_json,
                summary_text=f"Review generation failed: {exc}",
                strengths_json=[],
                improvements_json=[],
                recommendations_json=[],
                callouts_json=[],
                llm_model="unavailable",
                llm_provider="unavailable",
                llm_tokens_in=None,
                llm_tokens_out=None,
            )

        db.add(review)
        db.commit()
        db.refresh(review)
        return review

    def generate_manual_for_manager(
        self,
        db: Session,
        *,
        requester_id: str,
        requester_roles: list[str],
        target_person_id: str,
        period_start: datetime,
        period_end: datetime,
        request: Any | None = None,
    ) -> AgentPerformanceReview:
        if not self._llm_ready(db):
            raise ValueError("LLM provider not configured")
        is_admin = self._is_admin(requester_roles)
        limit = _coerce_int(resolve_value(db, SettingDomain.performance, "review_manual_daily_limit_per_manager"), 25)
        if not is_admin and limit > 0:
            used = self._manual_reviews_today(db, requester_id)
            if used >= limit:
                raise ValueError(f"Daily manual review limit reached ({limit})")

        try:
            review = self.generate_for_person(
                db,
                target_person_id,
                period_start,
                period_end,
                force=is_admin,
                source="manual",
            )
            log_audit_event(
                db,
                request=request,
                action="performance_review_generate_manual",
                entity_type="performance_review",
                entity_id=str(review.id),
                actor_id=str(requester_id),
                metadata={
                    "target_person_id": target_person_id,
                    "review_period_start": period_start.isoformat(),
                    "review_period_end": period_end.isoformat(),
                },
            )
            return review
        except Exception as exc:
            log_audit_event(
                db,
                request=request,
                action="performance_review_generate_manual",
                entity_type="performance_review",
                entity_id=None,
                actor_id=str(requester_id),
                is_success=False,
                status_code=400,
                metadata={
                    "target_person_id": target_person_id,
                    "review_period_start": period_start.isoformat(),
                    "review_period_end": period_end.isoformat(),
                    "error": str(exc),
                },
            )
            raise

    def generate_flagged_reviews_for_period(self, db: Session, period_start: datetime, period_end: datetime) -> int:
        review_enabled = resolve_value(db, SettingDomain.performance, "review_generation_enabled")
        if not review_enabled:
            return 0
        if not self._llm_ready(db):
            return 0

        threshold = _coerce_int(
            resolve_value(db, SettingDomain.performance, "flagged_threshold"),
            70,
        )
        max_reviews = _coerce_int(
            resolve_value(db, SettingDomain.performance, "max_reviews_per_run"),
            20,
        )

        snapshots = (
            db.query(AgentPerformanceSnapshot)
            .filter(
                AgentPerformanceSnapshot.score_period_start == period_start,
                AgentPerformanceSnapshot.score_period_end == period_end,
                AgentPerformanceSnapshot.composite_score < threshold,
            )
            .order_by(AgentPerformanceSnapshot.composite_score.asc())
            .limit(max(1, max_reviews))
            .all()
        )

        count = 0
        for snapshot in snapshots:
            existing = self._existing_review(db, str(snapshot.person_id), period_start, period_end)
            if existing:
                continue
            self.generate_for_person(db, str(snapshot.person_id), period_start, period_end, force=False, source="auto")
            count += 1
        return count

    def acknowledge(self, db: Session, review_id: str, person_id: str) -> AgentPerformanceReview:
        review = db.get(AgentPerformanceReview, coerce_uuid(review_id))
        if not review:
            raise ValueError("Review not found")
        if str(review.person_id) != str(person_id):
            raise ValueError("Forbidden")
        review.is_acknowledged = True
        review.acknowledged_at = datetime.now(UTC)
        db.commit()
        db.refresh(review)
        return review


performance_reviews = PerformanceReviewsService()
