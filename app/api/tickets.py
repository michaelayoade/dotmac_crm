from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.schemas.common import ListResponse
from app.schemas.tickets import (
    InfrastructureTicketCreate,
    InfrastructureTicketResolveRequest,
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
from app.services import sla_assignment
from app.services import tickets as tickets_service
from app.services.auth_dependencies import require_permission
from app.services.filter_engine import parse_filter_payload_json
from app.services.infrastructure_tickets import infrastructure_tickets

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


def _impact_summary(affected: dict) -> dict:
    return {
        "affected_count": len(affected["crm_subscriber_ids"]),
        "topology_count": affected.get("topology_count", 0),
        "coverage": affected.get("coverage", {}),
        "unmatched_subscriber_numbers": affected.get("unmatched_subscriber_numbers", []),
    }


@router.get(
    "/tickets/infrastructure/assets",
    tags=["tickets"],
    dependencies=[Depends(require_permission("support:ticket:read"))],
)
def list_infrastructure_assets(q: str | None = Query(default=None), db: Session = Depends(get_db)):
    """Pickable infrastructure items (OLTs, PON ports, basestations) for the picker."""
    from app.services import selfcare

    return {"items": selfcare.fetch_infrastructure_assets(db, q=q)}


@router.get(
    "/tickets/infrastructure/impact-preview",
    tags=["tickets"],
    dependencies=[Depends(require_permission("support:ticket:read"))],
)
def preview_infrastructure_impact(
    node_id: str | None = Query(default=None),
    basestation_id: str | None = Query(default=None),
    olt_id: str | None = Query(default=None),
    pon_port_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Who would be notified for an asset — plus topology coverage — before creating."""
    if not any([node_id, basestation_id, olt_id, pon_port_id]):
        raise HTTPException(status_code=400, detail="An infrastructure asset id is required")
    affected = infrastructure_tickets.resolve_affected(
        db, node_id=node_id, basestation_id=basestation_id, olt_id=olt_id, pon_port_id=pon_port_id
    )
    return _impact_summary(affected)


@router.post(
    "/tickets/infrastructure",
    status_code=status.HTTP_201_CREATED,
    tags=["tickets"],
    dependencies=[Depends(require_permission("support:ticket:create"))],
)
def create_infrastructure_ticket(
    payload: InfrastructureTicketCreate,
    db: Session = Depends(get_db),
    auth=Depends(get_current_user),
):
    """Create one ticket for an infrastructure asset and notify every affected customer."""
    if not (
        payload.node_id
        or payload.basestation_id
        or payload.olt_id
        or payload.pon_port_id
        or payload.manual_subscriber_ids
    ):
        raise HTTPException(
            status_code=400,
            detail="Provide an infrastructure asset (OLT / PON port / device / basestation) or manual subscribers.",
        )
    actor_id = str(auth.get("person_id")) if auth else None
    result = infrastructure_tickets.create(
        db,
        title=payload.title,
        description=payload.description,
        node_id=payload.node_id,
        basestation_id=payload.basestation_id,
        olt_id=payload.olt_id,
        pon_port_id=payload.pon_port_id,
        manual_subscriber_ids=[str(s) for s in payload.manual_subscriber_ids],
        confirm_large=payload.confirm_large,
        asset_label=payload.asset_label,
        region=payload.region,
        priority=payload.priority,
        actor_id=actor_id,
        notify=payload.notify,
        channel=payload.channel,
        email_subject=payload.email_subject,
        email_body=payload.email_body,
        sms_body=payload.sms_body,
    )
    return {
        "ticket": TicketRead.model_validate(result["ticket"]),
        "impact": _impact_summary(result["affected"]),
        "notification": result["notification"],
    }


@router.post(
    "/tickets/infrastructure/{ticket_id}/resolve",
    tags=["tickets"],
    dependencies=[Depends(require_permission("support:ticket:update"))],
)
def resolve_infrastructure_ticket(
    ticket_id: str,
    payload: InfrastructureTicketResolveRequest,
    db: Session = Depends(get_db),
    auth=Depends(get_current_user),
):
    """Close an infrastructure ticket and notify every affected customer it's fixed."""
    actor_id = str(auth.get("person_id")) if auth else None
    result = infrastructure_tickets.resolve(
        db,
        ticket_id,
        actor_id=actor_id,
        notify=payload.notify,
        channel=payload.channel,
        email_subject=payload.email_subject,
        email_body=payload.email_body,
        sms_body=payload.sms_body,
    )
    return {
        "ticket": TicketRead.model_validate(result["ticket"]),
        "notification": result["notification"],
    }


@router.get(
    "/tickets/{ticket_id}",
    response_model=TicketRead,
    tags=["tickets"],
    dependencies=[Depends(require_permission("support:ticket:read"))],
)
def get_ticket(ticket_id: str, db: Session = Depends(get_db)):
    return tickets_service.tickets.get(db, ticket_id)


@router.get(
    "/tickets/{ticket_id}/sla",
    tags=["tickets"],
    dependencies=[Depends(require_permission("support:ticket:read"))],
)
def get_ticket_sla(ticket_id: str, db: Session = Depends(get_db)):
    """Live SLA-clock status (time-to-breach) for a ticket."""
    tickets_service.tickets.get(db, ticket_id)  # 404 if the ticket doesn't exist
    status_data = sla_assignment.ticket_sla_status(db, ticket_id)
    if status_data is None:
        raise HTTPException(status_code=404, detail="No SLA clock for this ticket")
    return status_data


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


@router.post(
    "/tickets/{ticket_id}/auto-assign",
    response_model=TicketRead,
    tags=["tickets"],
    dependencies=[Depends(require_permission("support:ticket:update"))],
)
def auto_assign_ticket_manually(ticket_id: str, db: Session = Depends(get_db), auth=Depends(get_current_user)):
    actor_id = str(auth.get("person_id")) if auth else None
    return tickets_service.tickets.auto_assign_manual(db, ticket_id, actor_id=actor_id)


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
