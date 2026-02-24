from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.tickets import (
    TicketBulkUpdateRequest,
    TicketBulkUpdateResponse,
    TicketCommentBulkCreateRequest,
    TicketCommentBulkCreateResponse,
    TicketCommentCreate,
    TicketCommentRead,
    TicketCommentUpdate,
    TicketCreate,
    TicketRead,
    TicketSlaEventCreate,
    TicketSlaEventRead,
    TicketSlaEventUpdate,
    TicketUpdate,
)
from app.services import tickets as tickets_service
from app.services.auth_dependencies import require_permission
from app.services.filter_engine import parse_filter_payload_json

router = APIRouter()


@router.post(
    "/tickets",
    response_model=TicketRead,
    status_code=status.HTTP_201_CREATED,
    tags=["tickets"],
    dependencies=[Depends(require_permission("support:ticket:create"))],
)
def create_ticket(payload: TicketCreate, db: Session = Depends(get_db)):
    return tickets_service.tickets.create(db, payload)


@router.get(
    "/tickets/{ticket_id}",
    response_model=TicketRead,
    tags=["tickets"],
    dependencies=[Depends(require_permission("support:ticket:read"))],
)
def get_ticket(ticket_id: str, db: Session = Depends(get_db)):
    return tickets_service.tickets.get(db, ticket_id)


@router.get(
    "/tickets",
    response_model=ListResponse[TicketRead],
    tags=["tickets"],
    dependencies=[Depends(require_permission("support:ticket:read"))],
)
def list_tickets(
    subscriber_id: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    channel: str | None = None,
    search: str | None = None,
    created_by_person_id: str | None = None,
    assigned_to_person_id: str | None = None,
    is_active: bool | None = None,
    filters: str | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    try:
        filters_payload = parse_filter_payload_json(filters)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    args = (
        subscriber_id,
        status,
        priority,
        channel,
        search,
        created_by_person_id,
        assigned_to_person_id,
        is_active,
        order_by,
        order_dir,
        limit,
        offset,
    )
    if filters_payload is None:
        return tickets_service.tickets.list_response(db, *args)
    return tickets_service.tickets.list_response(db, *args, filters_payload=filters_payload)


@router.patch(
    "/tickets/{ticket_id}",
    response_model=TicketRead,
    tags=["tickets"],
    dependencies=[Depends(require_permission("support:ticket:update"))],
)
def update_ticket(ticket_id: str, payload: TicketUpdate, db: Session = Depends(get_db)):
    return tickets_service.tickets.update(db, ticket_id, payload)


@router.delete(
    "/tickets/{ticket_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["tickets"],
    dependencies=[Depends(require_permission("support:ticket:delete"))],
)
def delete_ticket(ticket_id: str, db: Session = Depends(get_db)):
    tickets_service.tickets.delete(db, ticket_id)


@router.post(
    "/tickets/bulk-update",
    response_model=TicketBulkUpdateResponse,
    tags=["tickets"],
    dependencies=[Depends(require_permission("support:ticket:update"))],
)
def bulk_update_tickets(payload: TicketBulkUpdateRequest, db: Session = Depends(get_db)):
    response = tickets_service.tickets.bulk_update_response(
        db, [str(ticket_id) for ticket_id in payload.ticket_ids], payload.update
    )
    return TicketBulkUpdateResponse(**response)


@router.post(
    "/ticket-comments",
    response_model=TicketCommentRead,
    status_code=status.HTTP_201_CREATED,
    tags=["ticket-comments"],
    dependencies=[Depends(require_permission("support:ticket:update"))],
)
def create_ticket_comment(payload: TicketCommentCreate, db: Session = Depends(get_db)):
    return tickets_service.ticket_comments.create(db, payload)


@router.post(
    "/ticket-comments/bulk",
    response_model=TicketCommentBulkCreateResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["ticket-comments"],
    dependencies=[Depends(require_permission("support:ticket:update"))],
)
def create_ticket_comments_bulk(payload: TicketCommentBulkCreateRequest, db: Session = Depends(get_db)):
    response = tickets_service.ticket_comments.bulk_create_response(db, payload)
    return TicketCommentBulkCreateResponse(**response)


@router.get(
    "/ticket-comments/{comment_id}",
    response_model=TicketCommentRead,
    tags=["ticket-comments"],
    dependencies=[Depends(require_permission("support:ticket:read"))],
)
def get_ticket_comment(comment_id: str, db: Session = Depends(get_db)):
    return tickets_service.ticket_comments.get(db, comment_id)


@router.get(
    "/ticket-comments",
    response_model=ListResponse[TicketCommentRead],
    tags=["ticket-comments"],
    dependencies=[Depends(require_permission("support:ticket:read"))],
)
def list_ticket_comments(
    ticket_id: str | None = None,
    is_internal: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return tickets_service.ticket_comments.list_response(db, ticket_id, is_internal, order_by, order_dir, limit, offset)


@router.patch(
    "/ticket-comments/{comment_id}",
    response_model=TicketCommentRead,
    tags=["ticket-comments"],
    dependencies=[Depends(require_permission("support:ticket:update"))],
)
def update_ticket_comment(comment_id: str, payload: TicketCommentUpdate, db: Session = Depends(get_db)):
    return tickets_service.ticket_comments.update(db, comment_id, payload)


@router.delete(
    "/ticket-comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["ticket-comments"],
    dependencies=[Depends(require_permission("support:ticket:update"))],
)
def delete_ticket_comment(comment_id: str, db: Session = Depends(get_db)):
    tickets_service.ticket_comments.delete(db, comment_id)


@router.post(
    "/ticket-sla-events",
    response_model=TicketSlaEventRead,
    status_code=status.HTTP_201_CREATED,
    tags=["ticket-sla-events"],
    dependencies=[Depends(require_permission("support:ticket:update"))],
)
def create_ticket_sla_event(payload: TicketSlaEventCreate, db: Session = Depends(get_db)):
    return tickets_service.ticket_sla_events.create(db, payload)


@router.get(
    "/ticket-sla-events/{event_id}",
    response_model=TicketSlaEventRead,
    tags=["ticket-sla-events"],
    dependencies=[Depends(require_permission("support:ticket:read"))],
)
def get_ticket_sla_event(event_id: str, db: Session = Depends(get_db)):
    return tickets_service.ticket_sla_events.get(db, event_id)


@router.get(
    "/ticket-sla-events",
    response_model=ListResponse[TicketSlaEventRead],
    tags=["ticket-sla-events"],
    dependencies=[Depends(require_permission("support:ticket:read"))],
)
def list_ticket_sla_events(
    ticket_id: str | None = None,
    event_type: str | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return tickets_service.ticket_sla_events.list_response(
        db, ticket_id, event_type, order_by, order_dir, limit, offset
    )


@router.patch(
    "/ticket-sla-events/{event_id}",
    response_model=TicketSlaEventRead,
    tags=["ticket-sla-events"],
    dependencies=[Depends(require_permission("support:ticket:update"))],
)
def update_ticket_sla_event(event_id: str, payload: TicketSlaEventUpdate, db: Session = Depends(get_db)):
    return tickets_service.ticket_sla_events.update(db, event_id, payload)


@router.delete(
    "/ticket-sla-events/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["ticket-sla-events"],
    dependencies=[Depends(require_permission("support:ticket:update"))],
)
def delete_ticket_sla_event(event_id: str, db: Session = Depends(get_db)):
    tickets_service.ticket_sla_events.delete(db, event_id)
