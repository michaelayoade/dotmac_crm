"""Service helpers for admin web layer."""

from sqlalchemy.orm import Session


def _get_initials(name: str) -> str:
    if not name:
        return "??"
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[0:2].upper()


def get_current_user(request) -> dict:
    """Get current user context from the request state.

    Auth info should already be populated by require_web_auth dependency.
    No fallback DB queries - if auth isn't set, return empty user.
    """
    auth = getattr(request.state, "auth", None) or {}
    roles = auth.get("roles", []) if isinstance(auth, dict) else []
    permissions = auth.get("scopes", []) if isinstance(auth, dict) else []
    user = getattr(request.state, "user", None)

    if user:
        name = f"{user.first_name} {user.last_name}".strip() if hasattr(user, "first_name") else "User"
        person_id = getattr(user, "person_id", None)
        return {
            "id": str(getattr(user, "id", "")),
            "person_id": str(person_id if person_id else getattr(user, "id", "")),
            "initials": _get_initials(name),
            "name": name,
            "email": getattr(user, "email", ""),
            "roles": roles,
            "permissions": permissions,
        }

    return {
        "id": "",
        "person_id": "",
        "initials": "??",
        "name": "Unknown User",
        "email": "",
        "roles": roles,
        "permissions": permissions,
    }


def get_sidebar_stats(db: Session) -> dict:
    """Get stats for sidebar badges.

    Uses SQL COUNT for efficiency instead of loading records into memory.
    """
    from sqlalchemy import func
    from app.models.tickets import Ticket, TicketStatus

    try:
        # Use SQL COUNT with status filter - much faster than loading 1000 records
        open_statuses = [
            TicketStatus.open,
            TicketStatus.new,
            TicketStatus.pending,
            TicketStatus.waiting_on_customer,
            TicketStatus.lastmile_rerun,
            TicketStatus.site_under_construction,
            TicketStatus.on_hold,
        ]
        open_tickets_count = (
            db.query(func.count(Ticket.id))
            .filter(Ticket.status.in_(open_statuses))
            .filter(Ticket.is_active.is_(True))
            .scalar()
        ) or 0
    except Exception:
        open_tickets_count = 0

    return {
        "dispatch_jobs": 0,
        "open_tickets": open_tickets_count,
    }


def build_admin_context(request, db: Session) -> dict:
    """Build common context for admin templates."""
    current_user = get_current_user(request)
    return {
        "request": request,
        "user": current_user,
        "current_user": current_user,
        "sidebar_stats": get_sidebar_stats(db),
    }
