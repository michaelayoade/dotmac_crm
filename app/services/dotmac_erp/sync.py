"""Sync service for pushing data to DotMac ERP."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.event_store import EventStore
from app.models.person import Person
from app.models.projects import Project, ProjectStatus
from app.models.tickets import Ticket, TicketComment, TicketStatus
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.services import settings_spec
from app.services.dotmac_erp.client import (
    DotMacERPAuthError,
    DotMacERPClient,
    DotMacERPError,
    DotMacERPNotFoundError,
    DotMacERPRateLimitError,
    DotMacERPTransientError,
)

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Result of a sync operation."""

    projects_synced: int = 0
    tickets_synced: int = 0
    work_orders_synced: int = 0
    errors: list[dict] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def total_synced(self) -> int:
        return self.projects_synced + self.tickets_synced + self.work_orders_synced

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


@dataclass
class SyncEntityResult:
    """Result of syncing a single entity to ERP."""

    entity_type: str
    entity_id: str
    success: bool
    error_type: str | None = None
    error: str | None = None
    status_code: int | None = None
    response: dict | None = None


class DotMacERPSync:
    """
    Service for syncing DotMac CRM data to DotMac ERP.

    Syncs:
    - Projects (with status, region, customer info)
    - Tickets (with status, priority, customer info)
    - Work Orders (with status, assignee, linked project/ticket)
    """

    # Status mappings from CRM to ERP
    PROJECT_STATUS_MAP: ClassVar[dict[ProjectStatus, str]] = {
        ProjectStatus.planned: "active",
        ProjectStatus.active: "active",
        ProjectStatus.on_hold: "active",
        ProjectStatus.completed: "completed",
        ProjectStatus.canceled: "canceled",
    }

    TICKET_STATUS_MAP: ClassVar[dict[TicketStatus, str]] = {
        TicketStatus.new: "active",
        TicketStatus.open: "active",
        TicketStatus.pending: "active",
        TicketStatus.waiting_on_customer: "active",
        TicketStatus.lastmile_rerun: "active",
        TicketStatus.site_under_construction: "active",
        TicketStatus.on_hold: "active",
        TicketStatus.resolved: "completed",
        TicketStatus.closed: "completed",
        TicketStatus.canceled: "canceled",
    }

    WORK_ORDER_STATUS_MAP: ClassVar[dict[WorkOrderStatus, str]] = {
        WorkOrderStatus.draft: "active",
        WorkOrderStatus.scheduled: "active",
        WorkOrderStatus.dispatched: "active",
        WorkOrderStatus.in_progress: "active",
        WorkOrderStatus.completed: "completed",
        WorkOrderStatus.canceled: "canceled",
    }

    def __init__(self, db: Session):
        self.db = db
        self._client: DotMacERPClient | None = None
        self._erp_person_cache_by_email: dict[str, str | None] = {}
        self._author_mapping_dirty = False

    def _get_client(self) -> DotMacERPClient | None:
        """Get configured ERP client, or None if not configured."""
        if self._client is not None:
            return self._client

        # Check if sync is enabled
        enabled = settings_spec.resolve_value(
            self.db,
            SettingDomain.integration,
            "dotmac_erp_sync_enabled",
            use_cache=False,
        )
        if not enabled:
            return None

        base_url_value = settings_spec.resolve_value(
            self.db,
            SettingDomain.integration,
            "dotmac_erp_base_url",
            use_cache=False,
        )
        token_value = settings_spec.resolve_value(
            self.db,
            SettingDomain.integration,
            "dotmac_erp_token",
            use_cache=False,
        )

        base_url = str(base_url_value) if base_url_value else None
        token = str(token_value) if token_value else None

        if not base_url or not token:
            logger.warning("DotMac ERP sync enabled but not configured (missing URL or token)")
            return None

        timeout_value = settings_spec.resolve_value(
            self.db,
            SettingDomain.integration,
            "dotmac_erp_timeout_seconds",
            use_cache=False,
        )
        if isinstance(timeout_value, int | str):
            timeout = int(timeout_value)
        else:
            timeout = 30

        self._client = DotMacERPClient(
            base_url=base_url,
            token=token,
            timeout=timeout,
        )
        return self._client

    def close(self):
        """Close the ERP client."""
        if self._client:
            self._client.close()
            self._client = None

    # ============ Data Mappers ============

    def _map_project(self, project: Project) -> dict:
        """Map a Project model to ERP sync payload."""
        payload = {
            "crm_id": str(project.id),
            "omni_id": str(project.id),
            "erpnext_id": project.erpnext_id,
            "name": project.name,
            "code": project.code,
            "project_type": project.project_type.value if project.project_type else None,
            "status": self.PROJECT_STATUS_MAP.get(project.status, "active"),
            "region": project.region,
            "description": project.description,
            "start_at": project.start_at.isoformat() if project.start_at else None,
            "due_at": project.due_at.isoformat() if project.due_at else None,
            "metadata": {
                "priority": project.priority.value if project.priority else None,
                "tags": project.tags,
            },
        }

        # Add customer info if available
        if project.subscriber:
            payload["customer_name"] = project.subscriber.display_name
            payload["customer_omni_id"] = str(project.subscriber.id)

        return payload

    @staticmethod
    def _format_iso(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat()

    @staticmethod
    def _safe_text(value: str | None, limit: int = 4000) -> str | None:
        if value is None:
            return None
        if len(value) <= limit:
            return value
        return value[: limit - 3] + "..."

    @staticmethod
    def _normalize_email(value: str | None) -> str | None:
        if not value:
            return None
        normalized = value.strip().lower()
        return normalized or None

    @staticmethod
    def _redact_email(value: str | None) -> str:
        if not value:
            return "<empty>"
        if "@" not in value:
            return "***"
        local, domain = value.split("@", 1)
        if len(local) <= 2:
            local_part = local[:1] + "***"
        else:
            local_part = local[:2] + "***"
        return f"{local_part}@{domain}"

    @staticmethod
    def _extract_erp_person_id(employee: dict) -> str | None:
        for key in ("employee_id", "person_id", "id"):
            value = employee.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _resolve_erp_person_id_by_email(self, email: str) -> str | None:
        normalized_email = self._normalize_email(email)
        if not normalized_email:
            return None

        if normalized_email in self._erp_person_cache_by_email:
            return self._erp_person_cache_by_email[normalized_email]

        client = self._get_client()
        if client is None:
            self._erp_person_cache_by_email[normalized_email] = None
            return None

        offset = 0
        limit = 500
        found: str | None = None
        try:
            while True:
                employees = client.get_employees(include_inactive=True, limit=limit, offset=offset)
                if not employees:
                    break
                for employee in employees:
                    employee_email = self._normalize_email(employee.get("email"))
                    if employee_email != normalized_email:
                        continue
                    found = self._extract_erp_person_id(employee)
                    if found:
                        break
                if found or len(employees) < limit:
                    break
                offset += limit
        except DotMacERPError as exc:
            logger.debug(
                "ERP staff lookup unavailable for author mapping email=%s error=%s", self._redact_email(email), exc
            )
            self._erp_person_cache_by_email[normalized_email] = None
            return None

        self._erp_person_cache_by_email[normalized_email] = found
        return found

    def _resolve_comment_author_erp_person_id(
        self,
        comment: TicketComment,
        ticket: Ticket,
        stats: dict[str, int] | None = None,
    ) -> str | None:
        if not comment.author_person_id:
            return None

        person = self.db.get(Person, comment.author_person_id)
        if not person:
            if stats is not None:
                stats["unresolved"] = stats.get("unresolved", 0) + 1
            return None

        if person.erp_person_id:
            if stats is not None:
                stats["resolved"] = stats.get("resolved", 0) + 1
            return person.erp_person_id

        normalized_email = self._normalize_email(person.email)
        if not normalized_email:
            if stats is not None:
                stats["unresolved"] = stats.get("unresolved", 0) + 1
            logger.debug(
                "ERP author mapping unresolved: missing email ticket_id=%s email=%s",
                ticket.id,
                self._redact_email(normalized_email),
            )
            return None

        erp_person_id = self._resolve_erp_person_id_by_email(normalized_email)
        if not erp_person_id:
            if stats is not None:
                stats["unresolved"] = stats.get("unresolved", 0) + 1
            logger.debug(
                "ERP author mapping unresolved: no ERP staff match ticket_id=%s email=%s",
                ticket.id,
                self._redact_email(normalized_email),
            )
            return None

        person.erp_person_id = erp_person_id
        self._author_mapping_dirty = True
        if stats is not None:
            stats["resolved"] = stats.get("resolved", 0) + 1
        return erp_person_id

    def _flush_author_mappings(self) -> None:
        if not self._author_mapping_dirty:
            return
        self.db.flush()
        self.db.commit()
        self._author_mapping_dirty = False

    def _build_ticket_activity_log(
        self,
        ticket: Ticket,
        max_entries: int = 25,
        stats: dict[str, int] | None = None,
    ) -> list[dict[str, object]]:
        """Build a bounded ticket activity log from comments and event store rows."""
        comments = self._get_ticket_comments(ticket_id=ticket.id, limit=max_entries)
        events = (
            self.db.query(EventStore)
            .filter(EventStore.ticket_id == ticket.id)
            .filter(EventStore.is_active.is_(True))
            .order_by(EventStore.created_at.desc())
            .limit(max_entries)
            .all()
        )

        entries: list[dict[str, object]] = []
        for comment in comments:
            entries.append(
                {
                    "kind": "comment",
                    "id": str(comment.id),
                    "timestamp": self._format_iso(comment.created_at),
                    "author_person_id": self._resolve_comment_author_erp_person_id(comment, ticket, stats=stats),
                    "is_internal": bool(comment.is_internal),
                    "body": self._safe_text(comment.body),
                    "attachments_count": len(comment.attachments or []),
                }
            )

        for event in events:
            raw_payload = event.payload if isinstance(event.payload, dict) else {}
            details: dict[str, object] = {}
            for key in ("title", "subject", "from_status", "to_status", "status", "priority", "channel"):
                value = raw_payload.get(key)
                if value is not None:
                    details[key] = value
            changed_fields = raw_payload.get("changed_fields")
            if isinstance(changed_fields, list):
                details["changed_fields"] = changed_fields[:50]
            entries.append(
                {
                    "kind": "event",
                    "id": str(event.event_id),
                    "timestamp": self._format_iso(event.created_at),
                    "event_type": event.event_type,
                    "status": event.status.value if event.status else None,
                    "details": details,
                }
            )

        entries.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
        return entries[:max_entries]

    def _get_ticket_comments(self, ticket_id: object, limit: int = 50) -> list[TicketComment]:
        return (
            self.db.query(TicketComment)
            .filter(TicketComment.ticket_id == ticket_id)
            .order_by(TicketComment.created_at.desc())
            .limit(limit)
            .all()
        )

    def _build_ticket_comments(
        self,
        ticket: Ticket,
        max_entries: int = 50,
        stats: dict[str, int] | None = None,
    ) -> list[dict[str, object]]:
        comments = self._get_ticket_comments(ticket_id=ticket.id, limit=max_entries)
        return [
            {
                "id": str(comment.id),
                "timestamp": self._format_iso(comment.created_at),
                "author_person_id": self._resolve_comment_author_erp_person_id(comment, ticket, stats=stats),
                "is_internal": bool(comment.is_internal),
                "body": self._safe_text(comment.body),
                "attachments_count": len(comment.attachments or []),
            }
            for comment in comments
        ]

    def _map_ticket(self, ticket: Ticket, stats: dict[str, int] | None = None) -> dict:
        """Map a Ticket model to ERP sync payload."""
        payload = {
            "crm_id": str(ticket.id),
            "omni_id": str(ticket.id),
            "erpnext_id": ticket.erpnext_id,
            "subject": ticket.title,
            "description": ticket.description,
            "ticket_number": ticket.number or str(ticket.id),
            "ticket_type": ticket.ticket_type,
            "status": self.TICKET_STATUS_MAP.get(ticket.status, "active"),
            "priority": ticket.priority.value if ticket.priority else None,
            "comments": self._build_ticket_comments(ticket, stats=stats),
            "activity_log": self._build_ticket_activity_log(ticket, stats=stats),
            "metadata": {
                "channel": ticket.channel.value if ticket.channel else None,
                "tags": ticket.tags,
            },
        }

        # Add customer info if available
        if ticket.subscriber:
            payload["customer_name"] = ticket.subscriber.display_name
            payload["customer_omni_id"] = str(ticket.subscriber.id)

        return payload

    def _map_work_order(self, work_order: WorkOrder) -> dict:
        """Map a WorkOrder model to ERP sync payload."""
        payload: dict[str, object] = {
            "crm_id": str(work_order.id),
            "omni_id": str(work_order.id),
            "title": work_order.title,
            "work_type": work_order.work_type.value if work_order.work_type else None,
            "status": self.WORK_ORDER_STATUS_MAP.get(work_order.status, "active"),
            "priority": work_order.priority.value if work_order.priority else None,
            "scheduled_start": work_order.scheduled_start.isoformat() if work_order.scheduled_start else None,
            "scheduled_end": work_order.scheduled_end.isoformat() if work_order.scheduled_end else None,
            "metadata": {},
        }

        # Link to project if available
        if work_order.project_id:
            payload["project_omni_id"] = str(work_order.project_id)

        # Link to ticket if available
        if work_order.ticket_id:
            payload["ticket_omni_id"] = str(work_order.ticket_id)

        # Add assignee email for employee matching in ERP
        if work_order.assigned_to and work_order.assigned_to.email:
            payload["assigned_employee_email"] = work_order.assigned_to.email

        return payload

    # ============ Sync Methods ============

    def sync_project(self, project: Project) -> SyncEntityResult:
        """
        Sync a single project to ERP.

        Returns:
            SyncEntityResult with success and error details
        """
        client = self._get_client()
        if not client:
            logger.debug(f"ERP sync skipped for project {project.id}: not configured")
            return SyncEntityResult(
                entity_type="project",
                entity_id=str(project.id),
                success=False,
                error_type="not_configured",
            )

        try:
            payload = self._map_project(project)
            result = client.sync_project(payload)
            logger.info(
                f"Synced project to ERP project_id={project.id} "
                f"name={project.name} status={project.status.value if project.status else None}"
            )
            synced = result.get("projects_synced", 0) > 0 if isinstance(result, dict) else False
            return SyncEntityResult(
                entity_type="project",
                entity_id=str(project.id),
                success=synced,
                error_type=None if synced else "no_sync",
                response=result if isinstance(result, dict) else None,
            )
        except DotMacERPRateLimitError as e:
            logger.warning(
                "ERP sync rate limited for project_id=%s retry_after=%s",
                project.id,
                e.retry_after,
            )
            raise
        except DotMacERPAuthError as e:
            logger.error(
                "ERP auth failed syncing project project_id=%s error=%s",
                project.id,
                e,
            )
            return SyncEntityResult(
                entity_type="project",
                entity_id=str(project.id),
                success=False,
                error_type="auth",
                error=str(e),
                status_code=e.status_code,
            )
        except DotMacERPNotFoundError as e:
            logger.error(
                "ERP resource not found syncing project project_id=%s error=%s",
                project.id,
                e,
            )
            return SyncEntityResult(
                entity_type="project",
                entity_id=str(project.id),
                success=False,
                error_type="not_found",
                error=str(e),
                status_code=e.status_code,
            )
        except DotMacERPError as e:
            status_code = e.status_code
            if status_code is not None and 400 <= status_code < 500 and status_code != 429:
                logger.error(
                    "ERP validation failed syncing project project_id=%s status=%s error=%s",
                    project.id,
                    status_code,
                    e,
                )
                return SyncEntityResult(
                    entity_type="project",
                    entity_id=str(project.id),
                    success=False,
                    error_type="validation",
                    error=str(e),
                    status_code=status_code,
                    response=e.response if isinstance(e.response, dict) else None,
                )
            raise DotMacERPTransientError(str(e), status_code=status_code, response=e.response)

    def sync_ticket(self, ticket: Ticket) -> SyncEntityResult:
        """
        Sync a single ticket to ERP.

        Returns:
            SyncEntityResult with success and error details
        """
        client = self._get_client()
        if not client:
            logger.debug(f"ERP sync skipped for ticket {ticket.id}: not configured")
            return SyncEntityResult(
                entity_type="ticket",
                entity_id=str(ticket.id),
                success=False,
                error_type="not_configured",
            )

        try:
            author_stats: dict[str, int] = {"resolved": 0, "unresolved": 0}
            payload = self._map_ticket(ticket, stats=author_stats)
            logger.info(
                "ERP author mapping stats ticket_id=%s resolved=%d unresolved=%d",
                ticket.id,
                author_stats["resolved"],
                author_stats["unresolved"],
            )
            result = client.sync_ticket(payload)
            self._flush_author_mappings()
            logger.info(
                f"Synced ticket to ERP ticket_id={ticket.id} "
                f"number={ticket.id} status={ticket.status.value if ticket.status else None}"
            )
            synced = result.get("tickets_synced", 0) > 0 if isinstance(result, dict) else False
            return SyncEntityResult(
                entity_type="ticket",
                entity_id=str(ticket.id),
                success=synced,
                error_type=None if synced else "no_sync",
                response=result if isinstance(result, dict) else None,
            )
        except DotMacERPRateLimitError as e:
            self._flush_author_mappings()
            logger.warning(
                "ERP sync rate limited for ticket_id=%s retry_after=%s",
                ticket.id,
                e.retry_after,
            )
            raise
        except DotMacERPAuthError as e:
            self._flush_author_mappings()
            logger.error(
                "ERP auth failed syncing ticket ticket_id=%s error=%s",
                ticket.id,
                e,
            )
            return SyncEntityResult(
                entity_type="ticket",
                entity_id=str(ticket.id),
                success=False,
                error_type="auth",
                error=str(e),
                status_code=e.status_code,
            )
        except DotMacERPNotFoundError as e:
            self._flush_author_mappings()
            logger.error(
                "ERP resource not found syncing ticket ticket_id=%s error=%s",
                ticket.id,
                e,
            )
            return SyncEntityResult(
                entity_type="ticket",
                entity_id=str(ticket.id),
                success=False,
                error_type="not_found",
                error=str(e),
                status_code=e.status_code,
            )
        except DotMacERPError as e:
            self._flush_author_mappings()
            status_code = e.status_code
            if status_code is not None and 400 <= status_code < 500 and status_code != 429:
                logger.error(
                    "ERP validation failed syncing ticket ticket_id=%s status=%s error=%s",
                    ticket.id,
                    status_code,
                    e,
                )
                return SyncEntityResult(
                    entity_type="ticket",
                    entity_id=str(ticket.id),
                    success=False,
                    error_type="validation",
                    error=str(e),
                    status_code=status_code,
                    response=e.response if isinstance(e.response, dict) else None,
                )
            raise DotMacERPTransientError(str(e), status_code=status_code, response=e.response)
        except Exception:
            self._flush_author_mappings()
            raise

    def sync_work_order(self, work_order: WorkOrder) -> SyncEntityResult:
        """
        Sync a single work order to ERP.

        Returns:
            SyncEntityResult with success and error details
        """
        client = self._get_client()
        if not client:
            logger.debug(f"ERP sync skipped for work_order {work_order.id}: not configured")
            return SyncEntityResult(
                entity_type="work_order",
                entity_id=str(work_order.id),
                success=False,
                error_type="not_configured",
            )

        try:
            payload = self._map_work_order(work_order)
            result = client.sync_work_order(payload)
            logger.info(
                f"Synced work order to ERP work_order_id={work_order.id} "
                f"title={work_order.title} status={work_order.status.value if work_order.status else None}"
            )
            synced = result.get("work_orders_synced", 0) > 0 if isinstance(result, dict) else False
            return SyncEntityResult(
                entity_type="work_order",
                entity_id=str(work_order.id),
                success=synced,
                error_type=None if synced else "no_sync",
                response=result if isinstance(result, dict) else None,
            )
        except DotMacERPRateLimitError as e:
            logger.warning(
                "ERP sync rate limited for work_order_id=%s retry_after=%s",
                work_order.id,
                e.retry_after,
            )
            raise
        except DotMacERPAuthError as e:
            logger.error(
                "ERP auth failed syncing work_order work_order_id=%s error=%s",
                work_order.id,
                e,
            )
            return SyncEntityResult(
                entity_type="work_order",
                entity_id=str(work_order.id),
                success=False,
                error_type="auth",
                error=str(e),
                status_code=e.status_code,
            )
        except DotMacERPNotFoundError as e:
            logger.error(
                "ERP resource not found syncing work_order work_order_id=%s error=%s",
                work_order.id,
                e,
            )
            return SyncEntityResult(
                entity_type="work_order",
                entity_id=str(work_order.id),
                success=False,
                error_type="not_found",
                error=str(e),
                status_code=e.status_code,
            )
        except DotMacERPError as e:
            status_code = e.status_code
            if status_code is not None and 400 <= status_code < 500 and status_code != 429:
                logger.error(
                    "ERP validation failed syncing work_order work_order_id=%s status=%s error=%s",
                    work_order.id,
                    status_code,
                    e,
                )
                return SyncEntityResult(
                    entity_type="work_order",
                    entity_id=str(work_order.id),
                    success=False,
                    error_type="validation",
                    error=str(e),
                    status_code=status_code,
                    response=e.response if isinstance(e.response, dict) else None,
                )
            raise DotMacERPTransientError(str(e), status_code=status_code, response=e.response)

    def bulk_sync(
        self,
        projects: list[Project] | None = None,
        tickets: list[Ticket] | None = None,
        work_orders: list[WorkOrder] | None = None,
    ) -> SyncResult:
        """
        Bulk sync multiple entities to ERP in a single API call.

        Args:
            projects: List of projects to sync
            tickets: List of tickets to sync
            work_orders: List of work orders to sync

        Returns:
            SyncResult with counts and any errors
        """
        start_time = datetime.now(UTC)
        result = SyncResult()

        client = self._get_client()
        if not client:
            result.errors.append({"type": "config", "error": "ERP sync not configured or disabled"})
            return result

        try:
            # Map all entities
            project_payloads = [self._map_project(p) for p in (projects or [])]
            author_stats: dict[str, int] = {"resolved": 0, "unresolved": 0}
            ticket_payloads = [self._map_ticket(t, stats=author_stats) for t in (tickets or [])]
            work_order_payloads = [self._map_work_order(wo) for wo in (work_orders or [])]
            logger.info(
                "ERP author mapping stats bulk resolved=%d unresolved=%d ticket_count=%d",
                author_stats["resolved"],
                author_stats["unresolved"],
                len(ticket_payloads),
            )

            # Send bulk request
            api_result = client.bulk_sync(
                projects=project_payloads,
                tickets=ticket_payloads,
                work_orders=work_order_payloads,
            )
            self._flush_author_mappings()

            result.projects_synced = api_result.get("projects_synced", 0)
            result.tickets_synced = api_result.get("tickets_synced", 0)
            result.work_orders_synced = api_result.get("work_orders_synced", 0)
            result.errors = api_result.get("errors", [])

        except DotMacERPError as e:
            self._flush_author_mappings()
            logger.error(f"Bulk sync failed: {e}")
            result.errors.append({"type": "api", "error": str(e)})
        except Exception:
            self._flush_author_mappings()
            raise

        result.duration_seconds = (datetime.now(UTC) - start_time).total_seconds()
        return result

    def sync_all_active(self, limit: int = 500) -> SyncResult:
        """
        Sync all active projects, tickets, and work orders.

        Args:
            limit: Maximum number of each entity type to sync

        Returns:
            SyncResult with counts and any errors
        """
        # Fetch active projects
        projects = (
            self.db.query(Project)
            .filter(Project.is_active.is_(True))
            .filter(Project.status.notin_([ProjectStatus.canceled]))
            .order_by(Project.updated_at.desc())
            .limit(limit)
            .all()
        )

        # Fetch active tickets (recent, not closed/canceled)
        tickets = (
            self.db.query(Ticket)
            .filter(Ticket.is_active.is_(True))
            .filter(Ticket.status.notin_([TicketStatus.closed, TicketStatus.canceled]))
            .order_by(Ticket.updated_at.desc())
            .limit(limit)
            .all()
        )

        # Fetch active work orders
        work_orders = (
            self.db.query(WorkOrder)
            .filter(WorkOrder.is_active.is_(True))
            .filter(WorkOrder.status.notin_([WorkOrderStatus.canceled]))
            .order_by(WorkOrder.updated_at.desc())
            .limit(limit)
            .all()
        )

        logger.info(f"Syncing to ERP: {len(projects)} projects, {len(tickets)} tickets, {len(work_orders)} work orders")

        return self.bulk_sync(projects=projects, tickets=tickets, work_orders=work_orders)

    def sync_recently_updated(self, since_minutes: int = 60) -> SyncResult:
        """
        Sync entities updated within the last N minutes.

        Args:
            since_minutes: Look back period in minutes

        Returns:
            SyncResult with counts and any errors
        """
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(minutes=since_minutes)

        projects = self.db.query(Project).filter(Project.is_active.is_(True)).filter(Project.updated_at >= cutoff).all()

        tickets = self.db.query(Ticket).filter(Ticket.is_active.is_(True)).filter(Ticket.updated_at >= cutoff).all()

        work_orders = (
            self.db.query(WorkOrder).filter(WorkOrder.is_active.is_(True)).filter(WorkOrder.updated_at >= cutoff).all()
        )

        logger.info(
            f"Syncing recently updated to ERP: {len(projects)} projects, "
            f"{len(tickets)} tickets, {len(work_orders)} work orders"
        )

        return self.bulk_sync(projects=projects, tickets=tickets, work_orders=work_orders)

    # ============ Expense Totals ============

    def get_project_expense_totals(self, project_ids: list[str]) -> dict[str, dict]:
        """
        Get expense totals from ERP for the given projects.

        Returns:
            Dict mapping project_id to expense totals
        """
        client = self._get_client()
        if not client:
            logger.debug("ERP expense fetch skipped: not configured")
            return {}

        try:
            result = client.get_expense_totals(project_omni_ids=project_ids)
            logger.debug(f"Fetched expense totals for {len(project_ids)} projects from ERP")
            return result
        except DotMacERPError as e:
            logger.warning(f"Failed to get project expense totals from ERP: {e}")
            return {}

    def get_ticket_expense_totals(self, ticket_ids: list[str]) -> dict[str, dict]:
        """Get expense totals from ERP for the given tickets."""
        client = self._get_client()
        if not client:
            logger.debug("ERP expense fetch skipped: not configured")
            return {}

        try:
            result = client.get_expense_totals(ticket_omni_ids=ticket_ids)
            logger.debug(f"Fetched expense totals for {len(ticket_ids)} tickets from ERP")
            return result
        except DotMacERPError as e:
            logger.warning(f"Failed to get ticket expense totals from ERP: {e}")
            return {}

    def get_work_order_expense_totals(self, work_order_ids: list[str]) -> dict[str, dict]:
        """Get expense totals from ERP for the given work orders."""
        client = self._get_client()
        if not client:
            logger.debug("ERP expense fetch skipped: not configured")
            return {}

        try:
            result = client.get_expense_totals(work_order_omni_ids=work_order_ids)
            logger.debug(f"Fetched expense totals for {len(work_order_ids)} work orders from ERP")
            return result
        except DotMacERPError as e:
            logger.warning(f"Failed to get work order expense totals from ERP: {e}")
            return {}


# Singleton-style factory function
def dotmac_erp_sync(db: Session) -> DotMacERPSync:
    """Create a DotMac ERP sync service instance."""
    return DotMacERPSync(db)
