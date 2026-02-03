"""Sync service for pushing data to DotMac ERP."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.projects import Project, ProjectStatus
from app.models.tickets import Ticket, TicketStatus
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.services.dotmac_erp.client import DotMacERPClient, DotMacERPError
from app.services import settings_spec

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


class DotMacERPSync:
    """
    Service for syncing DotMac CRM data to DotMac ERP.

    Syncs:
    - Projects (with status, region, customer info)
    - Tickets (with status, priority, customer info)
    - Work Orders (with status, assignee, linked project/ticket)
    """

    # Status mappings from CRM to ERP
    PROJECT_STATUS_MAP = {
        ProjectStatus.planned: "active",
        ProjectStatus.active: "active",
        ProjectStatus.on_hold: "active",
        ProjectStatus.completed: "completed",
        ProjectStatus.canceled: "canceled",
    }

    TICKET_STATUS_MAP = {
        TicketStatus.new: "active",
        TicketStatus.open: "active",
        TicketStatus.pending: "active",
        TicketStatus.on_hold: "active",
        TicketStatus.resolved: "completed",
        TicketStatus.closed: "completed",
        TicketStatus.canceled: "canceled",
    }

    WORK_ORDER_STATUS_MAP = {
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

    def _get_client(self) -> DotMacERPClient | None:
        """Get configured ERP client, or None if not configured."""
        if self._client is not None:
            return self._client

        # Check if sync is enabled
        enabled = settings_spec.resolve_value(
            self.db, SettingDomain.integration, "dotmac_erp_sync_enabled"
        )
        if not enabled:
            return None

        base_url_value = settings_spec.resolve_value(
            self.db, SettingDomain.integration, "dotmac_erp_base_url"
        )
        token_value = settings_spec.resolve_value(
            self.db, SettingDomain.integration, "dotmac_erp_token"
        )

        base_url = str(base_url_value) if base_url_value else None
        token = str(token_value) if token_value else None

        if not base_url or not token:
            logger.warning("DotMac ERP sync enabled but not configured (missing URL or token)")
            return None

        timeout_value = settings_spec.resolve_value(
            self.db, SettingDomain.integration, "dotmac_erp_timeout_seconds"
        )
        if isinstance(timeout_value, (int, str)):
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
            "omni_id": str(project.id),
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

    def _map_ticket(self, ticket: Ticket) -> dict:
        """Map a Ticket model to ERP sync payload."""
        payload = {
            "omni_id": str(ticket.id),
            "subject": ticket.title,
            "ticket_number": str(ticket.id),
            "ticket_type": ticket.ticket_type,
            "status": self.TICKET_STATUS_MAP.get(ticket.status, "active"),
            "priority": ticket.priority.value if ticket.priority else None,
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

    def sync_project(self, project: Project) -> bool:
        """
        Sync a single project to ERP.

        Returns:
            True if sync succeeded, False otherwise
        """
        client = self._get_client()
        if not client:
            logger.debug(f"ERP sync skipped for project {project.id}: not configured")
            return False

        try:
            payload = self._map_project(project)
            result = client.sync_project(payload)
            logger.info(
                f"Synced project to ERP project_id={project.id} "
                f"name={project.name} status={project.status.value if project.status else None}"
            )
            return result.get("projects_synced", 0) > 0
        except DotMacERPError as e:
            logger.error(
                f"Failed to sync project to ERP project_id={project.id} "
                f"name={project.name} status={project.status.value if project.status else None} "
                f"error={e}"
            )
            return False

    def sync_ticket(self, ticket: Ticket) -> bool:
        """
        Sync a single ticket to ERP.

        Returns:
            True if sync succeeded, False otherwise
        """
        client = self._get_client()
        if not client:
            logger.debug(f"ERP sync skipped for ticket {ticket.id}: not configured")
            return False

        try:
            payload = self._map_ticket(ticket)
            result = client.sync_ticket(payload)
            logger.info(
                f"Synced ticket to ERP ticket_id={ticket.id} "
                f"number={ticket.id} status={ticket.status.value if ticket.status else None}"
            )
            return result.get("tickets_synced", 0) > 0
        except DotMacERPError as e:
            logger.error(
                f"Failed to sync ticket to ERP ticket_id={ticket.id} "
                f"number={ticket.id} status={ticket.status.value if ticket.status else None} "
                f"error={e}"
            )
            return False

    def sync_work_order(self, work_order: WorkOrder) -> bool:
        """
        Sync a single work order to ERP.

        Returns:
            True if sync succeeded, False otherwise
        """
        client = self._get_client()
        if not client:
            logger.debug(f"ERP sync skipped for work_order {work_order.id}: not configured")
            return False

        try:
            payload = self._map_work_order(work_order)
            result = client.sync_work_order(payload)
            logger.info(
                f"Synced work order to ERP work_order_id={work_order.id} "
                f"title={work_order.title} status={work_order.status.value if work_order.status else None}"
            )
            return result.get("work_orders_synced", 0) > 0
        except DotMacERPError as e:
            logger.error(
                f"Failed to sync work order to ERP work_order_id={work_order.id} "
                f"title={work_order.title} status={work_order.status.value if work_order.status else None} "
                f"error={e}"
            )
            return False

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
        start_time = datetime.now(timezone.utc)
        result = SyncResult()

        client = self._get_client()
        if not client:
            result.errors.append({"type": "config", "error": "ERP sync not configured or disabled"})
            return result

        try:
            # Map all entities
            project_payloads = [self._map_project(p) for p in (projects or [])]
            ticket_payloads = [self._map_ticket(t) for t in (tickets or [])]
            work_order_payloads = [self._map_work_order(wo) for wo in (work_orders or [])]

            # Send bulk request
            api_result = client.bulk_sync(
                projects=project_payloads,
                tickets=ticket_payloads,
                work_orders=work_order_payloads,
            )

            result.projects_synced = api_result.get("projects_synced", 0)
            result.tickets_synced = api_result.get("tickets_synced", 0)
            result.work_orders_synced = api_result.get("work_orders_synced", 0)
            result.errors = api_result.get("errors", [])

        except DotMacERPError as e:
            logger.error(f"Bulk sync failed: {e}")
            result.errors.append({"type": "api", "error": str(e)})

        result.duration_seconds = (datetime.now(timezone.utc) - start_time).total_seconds()
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

        logger.info(
            f"Syncing to ERP: {len(projects)} projects, {len(tickets)} tickets, "
            f"{len(work_orders)} work orders"
        )

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

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)

        projects = (
            self.db.query(Project)
            .filter(Project.is_active.is_(True))
            .filter(Project.updated_at >= cutoff)
            .all()
        )

        tickets = (
            self.db.query(Ticket)
            .filter(Ticket.is_active.is_(True))
            .filter(Ticket.updated_at >= cutoff)
            .all()
        )

        work_orders = (
            self.db.query(WorkOrder)
            .filter(WorkOrder.is_active.is_(True))
            .filter(WorkOrder.updated_at >= cutoff)
            .all()
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
