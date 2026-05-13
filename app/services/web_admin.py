"""Service helpers for admin web layer."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

_SIDEBAR_STATS_TTL_SECONDS = 15.0
_SIDEBAR_STATS_CACHE: dict[str, tuple[datetime, dict]] = {}


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


def _workqueue_attention(db: Session, current_user: dict | None) -> int:
    """Count of items in the workqueue ``right_now`` band for the badge.

    Defensive: any failure (feature flag off, perm missing, aggregator
    error, no person_id) returns 0 so the sidebar never breaks the page.
    The aggregator is invoked once per page render — keep an eye on this
    if it shows up in latency budgets at scale.
    """
    if not current_user:
        return 0
    perms = current_user.get("permissions") or []
    if "workqueue:view" not in perms:
        return 0
    person_id_str = current_user.get("person_id") or ""
    if not person_id_str:
        return 0
    try:
        from uuid import UUID

        from app.models.domain_settings import SettingDomain
        from app.services import settings_spec
        from app.services.workqueue.aggregator import build_workqueue

        if not settings_spec.resolve_value(db, SettingDomain.workflow, "workqueue.enabled"):
            return 0

        try:
            person_uuid = UUID(person_id_str)
        except (TypeError, ValueError):
            return 0

        class _U:
            def __init__(self, person_id, permissions, roles):
                self.person_id = person_id
                self.permissions = permissions
                self.roles = roles

        view = build_workqueue(db, _U(person_uuid, set(perms), set(current_user.get("roles") or [])))
        return len(view.right_now)
    except Exception:  # never let the sidebar break the page
        return 0


def get_sidebar_stats(
    db: Session,
    current_user: dict | None = None,
    *,
    workqueue_attention_override: int | None = None,
) -> dict:
    """Get stats for sidebar badges.

    Uses SQL COUNT for efficiency instead of loading records into memory.

    ``current_user`` is optional — when supplied it powers per-user badges
    such as the workqueue *right-now* count.  Callers that don't have a
    user handy (e.g., legacy routes) can omit it; the workqueue badge
    simply won't render until those routes are updated.
    """
    from sqlalchemy import func

    from app.models.tickets import Ticket, TicketStatus

    cache_key = None
    if workqueue_attention_override is None:
        permissions = sorted(str(permission) for permission in ((current_user or {}).get("permissions") or []))
        cache_key = "|".join(
            [
                str((current_user or {}).get("person_id") or ""),
                ",".join(permissions),
            ]
        )
        cached = _SIDEBAR_STATS_CACHE.get(cache_key)
        now = datetime.now(UTC)
        if cached and (now - cached[0]).total_seconds() < _SIDEBAR_STATS_TTL_SECONDS:
            return dict(cached[1])

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

    payload = {
        "dispatch_jobs": 0,
        "open_tickets": open_tickets_count,
        "workqueue_attention": (
            workqueue_attention_override
            if workqueue_attention_override is not None
            else _workqueue_attention(db, current_user)
        ),
    }
    if cache_key is not None:
        _SIDEBAR_STATS_CACHE[cache_key] = (datetime.now(UTC), dict(payload))
    return payload


def build_admin_context(request, db: Session) -> dict:
    """Build common context for admin templates."""
    current_user = get_current_user(request)
    return {
        "request": request,
        "user": current_user,
        "current_user": current_user,
        "sidebar_stats": get_sidebar_stats(db, current_user),
    }
