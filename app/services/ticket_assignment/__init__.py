"""Ticket rule-based auto-assignment services."""

from app.services.ticket_assignment.engine import AssignmentResult, auto_assign_ticket

__all__ = ["AssignmentResult", "auto_assign_ticket"]
