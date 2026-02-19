"""CRM inbox settings and admin routes."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.crm.enums import ChannelType
from app.services import crm as crm_service
from app.services.common import coerce_uuid
from app.services.crm.inbox.settings_admin import (
    create_agent,
    create_agent_team,
    create_message_template,
    create_team,
    delete_message_template,
    update_message_template,
    update_notification_settings,
)
from app.services.crm.inbox.settings_view import build_inbox_settings_context

router = APIRouter(tags=["web-admin-crm"])
templates = Jinja2Templates(directory="templates")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_current_roles(request: Request) -> list[str]:
    auth = getattr(request.state, "auth", None)
    if isinstance(auth, dict):
        roles = auth.get("roles") or []
        if isinstance(roles, list):
            return [str(role) for role in roles]
    return []


def _get_current_scopes(request: Request) -> list[str]:
    auth = getattr(request.state, "auth", None)
    if isinstance(auth, dict):
        scopes = auth.get("scopes") or []
        if isinstance(scopes, list):
            return [str(scope) for scope in scopes]
    return []


@router.get("/inbox/settings", response_class=HTMLResponse)
async def inbox_settings(
    request: Request,
    db: Session = Depends(get_db),
):
    """Connector settings for CRM inbox channels."""
    from app.web.admin import get_current_user, get_sidebar_stats

    current_user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)

    context = build_inbox_settings_context(
        db,
        query_params=request.query_params,
        headers=request.headers,
        current_user=current_user,
        sidebar_stats=sidebar_stats,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    return templates.TemplateResponse(
        "admin/crm/inbox_settings.html",
        {
            "request": request,
            **context,
        },
    )


@router.post("/inbox/notification-settings", response_class=HTMLResponse)
async def update_inbox_notification_settings(
    request: Request,
    reminder_delay_seconds: str = Form(""),
    reminder_repeat_enabled: str | None = Form(None),
    reminder_repeat_interval_seconds: str = Form(""),
    notification_auto_dismiss_seconds: str = Form(""),
    db: Session = Depends(get_db),
):
    result = update_notification_settings(
        db,
        reminder_delay_seconds=reminder_delay_seconds,
        reminder_repeat_enabled=reminder_repeat_enabled,
        reminder_repeat_interval_seconds=reminder_repeat_interval_seconds,
        notification_auto_dismiss_seconds=notification_auto_dismiss_seconds,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if not result.ok:
        detail = quote(result.error_detail or "Failed to save notification settings", safe="")
        return RedirectResponse(
            url=f"/admin/crm/inbox/settings?notification_error=1&notification_error_detail={detail}",
            status_code=303,
        )
    return RedirectResponse(
        url="/admin/crm/inbox/settings?notification_setup=1",
        status_code=303,
    )


@router.post("/inbox/teams", response_class=HTMLResponse)
async def create_crm_team(
    request: Request,
    name: str = Form(...),
    notes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    result = create_team(
        db,
        name=name,
        notes=notes,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if result.ok:
        return RedirectResponse(url="/admin/crm/inbox/settings?team_setup=1", status_code=303)
    detail = quote(result.error_detail or "Failed to create team", safe="")
    return RedirectResponse(
        url=f"/admin/crm/inbox/settings?team_error=1&team_error_detail={detail}",
        status_code=303,
    )


@router.post("/inbox/agents", response_class=HTMLResponse)
async def create_crm_agent(
    request: Request,
    person_id: str | None = Form(None),
    title: str | None = Form(None),
    db: Session = Depends(get_db),
):
    result = create_agent(
        db,
        person_id=person_id,
        title=title,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if result.ok:
        return RedirectResponse(url="/admin/crm/inbox/settings?agent_setup=1", status_code=303)
    detail = quote(result.error_detail or "Failed to create agent", safe="")
    return RedirectResponse(
        url=f"/admin/crm/inbox/settings?agent_error=1&agent_error_detail={detail}",
        status_code=303,
    )


@router.post("/inbox/agents/bulk", response_class=HTMLResponse)
async def bulk_update_crm_agents(
    request: Request,
    action: str = Form(...),
    agent_ids: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox.settings_admin import deactivate_agent

    selected_ids = [agent_id.strip() for agent_id in agent_ids if agent_id and agent_id.strip()]
    if not selected_ids:
        detail = quote("No agents selected", safe="")
        return RedirectResponse(
            url=f"/admin/crm/inbox/settings?agent_error=1&agent_error_detail={detail}",
            status_code=303,
        )

    normalized_action = action.strip().lower()
    if normalized_action != "deactivate":
        detail = quote("Unsupported bulk action", safe="")
        return RedirectResponse(
            url=f"/admin/crm/inbox/settings?agent_error=1&agent_error_detail={detail}",
            status_code=303,
        )

    roles = _get_current_roles(request)
    scopes = _get_current_scopes(request)
    failures = 0
    for agent_id in selected_ids:
        result = deactivate_agent(
            db,
            agent_id=agent_id,
            roles=roles,
            scopes=scopes,
        )
        if not result.ok:
            failures += 1

    if failures:
        detail = quote(f"Failed to update {failures} selected agent(s)", safe="")
        return RedirectResponse(
            url=f"/admin/crm/inbox/settings?agent_error=1&agent_error_detail={detail}",
            status_code=303,
        )

    return RedirectResponse(url="/admin/crm/inbox/settings?agent_update=1", status_code=303)


@router.post("/inbox/agents/{agent_id}/deactivate", response_class=HTMLResponse)
async def deactivate_crm_agent(
    request: Request,
    agent_id: str,
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox.settings_admin import deactivate_agent

    result = deactivate_agent(
        db,
        agent_id=agent_id,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if result.ok:
        return RedirectResponse(url="/admin/crm/inbox/settings?agent_update=1", status_code=303)
    detail = quote(result.error_detail or "Failed to update agent", safe="")
    return RedirectResponse(
        url=f"/admin/crm/inbox/settings?agent_error=1&agent_error_detail={detail}",
        status_code=303,
    )


@router.post("/inbox/agents/{agent_id}/activate", response_class=HTMLResponse)
async def activate_crm_agent(
    request: Request,
    agent_id: str,
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox.settings_admin import activate_agent

    result = activate_agent(
        db,
        agent_id=agent_id,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if result.ok:
        return RedirectResponse(url="/admin/crm/inbox/settings?agent_update=1", status_code=303)
    detail = quote(result.error_detail or "Failed to update agent", safe="")
    return RedirectResponse(
        url=f"/admin/crm/inbox/settings?agent_error=1&agent_error_detail={detail}",
        status_code=303,
    )


@router.post("/inbox/agents/{agent_id}/delete", response_class=HTMLResponse)
async def delete_crm_agent(
    request: Request,
    agent_id: str,
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox.settings_admin import hard_delete_agent

    result = hard_delete_agent(
        db,
        agent_id=agent_id,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if result.ok:
        return RedirectResponse(url="/admin/crm/inbox/settings?agent_deleted=1", status_code=303)
    detail = quote(result.error_detail or "Failed to delete agent", safe="")
    return RedirectResponse(
        url=f"/admin/crm/inbox/settings?agent_error=1&agent_error_detail={detail}",
        status_code=303,
    )


@router.post("/inbox/agent-teams", response_class=HTMLResponse)
async def create_crm_agent_team(
    request: Request,
    agent_id: str = Form(...),
    team_id: str = Form(...),
    db: Session = Depends(get_db),
):
    result = create_agent_team(
        db,
        agent_id=agent_id,
        team_id=team_id,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if result.ok:
        return RedirectResponse(url="/admin/crm/inbox/settings?assignment_setup=1", status_code=303)
    detail = quote(result.error_detail or "Failed to assign agent to team", safe="")
    return RedirectResponse(
        url=f"/admin/crm/inbox/settings?assignment_error=1&assignment_error_detail={detail}",
        status_code=303,
    )


@router.post("/inbox/agent-teams/{link_id}/remove", response_class=HTMLResponse)
async def remove_crm_agent_team(
    request: Request,
    link_id: str,
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox.settings_admin import remove_agent_team

    result = remove_agent_team(
        db,
        link_id=link_id,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if result.ok:
        return RedirectResponse(url="/admin/crm/inbox/settings?assignment_setup=1", status_code=303)
    detail = quote(result.error_detail or "Failed to update agent team", safe="")
    return RedirectResponse(
        url=f"/admin/crm/inbox/settings?assignment_error=1&assignment_error_detail={detail}",
        status_code=303,
    )


@router.post("/inbox/templates", response_class=HTMLResponse)
async def create_inbox_template(
    request: Request,
    name: str = Form(...),
    channel_type: str = Form(...),
    subject: str | None = Form(None),
    body: str = Form(...),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    result = create_message_template(
        db,
        name=name,
        channel_type=channel_type,
        subject=subject,
        body=body,
        is_active=is_active,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if result.ok:
        return RedirectResponse(
            url="/admin/crm/inbox/settings?template_setup=1",
            status_code=303,
        )
    detail = quote(result.error_detail or "Failed to create template", safe="")
    return RedirectResponse(
        url=f"/admin/crm/inbox/settings?template_error=1&template_error_detail={detail}",
        status_code=303,
    )


@router.post("/inbox/templates/{template_id}", response_class=HTMLResponse)
async def update_inbox_template(
    request: Request,
    template_id: str,
    name: str = Form(...),
    channel_type: str = Form(...),
    subject: str | None = Form(None),
    body: str = Form(...),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    result = update_message_template(
        db,
        template_id=template_id,
        name=name,
        channel_type=channel_type,
        subject=subject,
        body=body,
        is_active=is_active,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if result.ok:
        return RedirectResponse(
            url="/admin/crm/inbox/settings?template_setup=1",
            status_code=303,
        )
    detail = quote(result.error_detail or "Failed to update template", safe="")
    return RedirectResponse(
        url=f"/admin/crm/inbox/settings?template_error=1&template_error_detail={detail}",
        status_code=303,
    )


@router.post("/inbox/templates/{template_id}/delete", response_class=HTMLResponse)
async def delete_inbox_template(
    request: Request,
    template_id: str,
    db: Session = Depends(get_db),
):
    result = delete_message_template(
        db,
        template_id=template_id,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if result.ok:
        return RedirectResponse(
            url="/admin/crm/inbox/settings?template_setup=1",
            status_code=303,
        )
    detail = quote(result.error_detail or "Failed to delete template", safe="")
    return RedirectResponse(
        url=f"/admin/crm/inbox/settings?template_error=1&template_error_detail={detail}",
        status_code=303,
    )


@router.post("/inbox/routing-rules", response_class=HTMLResponse)
async def create_inbox_routing_rule(
    request: Request,
    team_id: str = Form(...),
    channel_type: str = Form(...),
    keywords: str | None = Form(None),
    target_id: str | None = Form(None),
    strategy: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.schemas.crm.team import RoutingRuleCreate
    from app.services.crm.inbox.permissions import can_manage_inbox_settings

    if not can_manage_inbox_settings(_get_current_roles(request), _get_current_scopes(request)):
        return RedirectResponse(
            url="/admin/crm/inbox/settings?routing_error=1&routing_error_detail=Forbidden",
            status_code=303,
        )
    try:
        try:
            channel_enum = ChannelType(channel_type)
        except ValueError as exc:
            raise ValueError("Invalid channel type") from exc
        keywords_list = [k.strip() for k in (keywords or "").split(",") if k.strip()]
        rule_config = {
            "keywords": keywords_list,
            "target_id": target_id.strip() if target_id else None,
            "strategy": (strategy or "round_robin").strip(),
        }
        payload = RoutingRuleCreate(
            team_id=coerce_uuid(team_id),
            channel_type=channel_enum,
            rule_config=rule_config,
            is_active=bool(is_active),
        )
        crm_service.routing_rules.create(db, payload)
        return RedirectResponse(
            url="/admin/crm/inbox/settings?routing_setup=1",
            status_code=303,
        )
    except Exception as exc:
        detail = quote(str(exc) or "Failed to create routing rule", safe="")
        return RedirectResponse(
            url=f"/admin/crm/inbox/settings?routing_error=1&routing_error_detail={detail}",
            status_code=303,
        )


@router.post("/inbox/routing-rules/{rule_id}", response_class=HTMLResponse)
async def update_inbox_routing_rule(
    request: Request,
    rule_id: str,
    team_id: str = Form(...),
    channel_type: str = Form(...),
    keywords: str | None = Form(None),
    target_id: str | None = Form(None),
    strategy: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.schemas.crm.team import RoutingRuleUpdate
    from app.services.crm.inbox.permissions import can_manage_inbox_settings

    if not can_manage_inbox_settings(_get_current_roles(request), _get_current_scopes(request)):
        return RedirectResponse(
            url="/admin/crm/inbox/settings?routing_error=1&routing_error_detail=Forbidden",
            status_code=303,
        )
    try:
        try:
            channel_enum = ChannelType(channel_type)
        except ValueError as exc:
            raise ValueError("Invalid channel type") from exc
        keywords_list = [k.strip() for k in (keywords or "").split(",") if k.strip()]
        rule_config = {
            "keywords": keywords_list,
            "target_id": target_id.strip() if target_id else None,
            "strategy": (strategy or "round_robin").strip(),
        }
        payload = RoutingRuleUpdate(
            channel_type=channel_enum,
            rule_config=rule_config,
            is_active=bool(is_active),
        )
        crm_service.routing_rules.update(db, rule_id, payload)
        return RedirectResponse(
            url="/admin/crm/inbox/settings?routing_setup=1",
            status_code=303,
        )
    except Exception as exc:
        detail = quote(str(exc) or "Failed to update routing rule", safe="")
        return RedirectResponse(
            url=f"/admin/crm/inbox/settings?routing_error=1&routing_error_detail={detail}",
            status_code=303,
        )


@router.post("/inbox/routing-rules/{rule_id}/delete", response_class=HTMLResponse)
async def delete_inbox_routing_rule(
    request: Request,
    rule_id: str,
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox.permissions import can_manage_inbox_settings

    if not can_manage_inbox_settings(_get_current_roles(request), _get_current_scopes(request)):
        return RedirectResponse(
            url="/admin/crm/inbox/settings?routing_error=1&routing_error_detail=Forbidden",
            status_code=303,
        )
    try:
        crm_service.routing_rules.delete(db, rule_id)
        return RedirectResponse(
            url="/admin/crm/inbox/settings?routing_setup=1",
            status_code=303,
        )
    except Exception as exc:
        detail = quote(str(exc) or "Failed to delete routing rule", safe="")
        return RedirectResponse(
            url=f"/admin/crm/inbox/settings?routing_error=1&routing_error_detail={detail}",
            status_code=303,
        )
