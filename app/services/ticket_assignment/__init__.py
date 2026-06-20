"""Ticket rule-based auto-assignment services."""

from app.services.ticket_assignment.engine import (
    AssignmentResult,
    auto_assign_project,
    auto_assign_ticket,
    auto_assign_ticket_all,
)

__all__ = ["AssignmentResult", "auto_assign_project", "auto_assign_ticket", "auto_assign_ticket_all"]
