from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.csrf import get_csrf_token
from app.db import SessionLocal
from app.models.domain_settings import SettingDomain, SettingValueType
from app.models.performance import GoalStatus, PerformanceDomain
from app.models.person import Person
from app.models.scheduler import ScheduledTask
from app.schemas.performance import AgentPerformanceGoalCreate, AgentPerformanceGoalUpdate
from app.schemas.settings import DomainSettingUpdate
from app.services.auth_dependencies import require_permission
from app.services.common import coerce_uuid
from app.services.domain_settings import performance_settings
from app.services.performance.goals import performance_goals
from app.services.performance.reports import performance_reports
from app.services.performance.reviews import performance_reviews
from app.services.settings_spec import resolve_value
from app.tasks.performance import compute_weekly_scores, generate_flagged_reviews
from app.web.admin import get_current_user, get_sidebar_stats

router = APIRouter(prefix="/performance", tags=["web-admin-performance"])
templates = Jinja2Templates(directory="templates")


def _parse_deadline(value: str | None) -> date | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    return date.fromisoformat(raw)


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ctx(request: Request, db: Session, **kwargs):
    user = get_current_user(request)
    return {
        "request": request,
        "user": user,
        "current_user": user,
        "sidebar_stats": get_sidebar_stats(db),
        "csrf_token": get_csrf_token(request),
        "active_menu": "reports",
        **kwargs,
    }


def _parse_custom_period(start_date: str, end_date: str) -> tuple[datetime, datetime]:
    start_raw = start_date.strip()
    end_raw = end_date.strip()
    if not start_raw or not end_raw:
        raise ValueError("Start date and end date are required")
    try:
        start = datetime.fromisoformat(start_raw).replace(tzinfo=UTC)
        end_day = datetime.fromisoformat(end_raw).replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError("Invalid date format") from exc
    end = end_day + timedelta(days=1) - timedelta(microseconds=1)
    if start > end:
        raise ValueError("Start date must be before end date")
    return start, end


def _as_bool_form(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int_form(value: str | None, default: int) -> int:
    try:
        return int((value or "").strip())
    except ValueError:
        return default


def _task_state(task_id: str | None) -> str | None:
    if not task_id:
        return None
    try:
        return str(celery_app.AsyncResult(task_id).state)
    except Exception:
        return None


def _scheduled_task_meta(db: Session, task_name: str) -> dict:
    task = db.query(ScheduledTask).filter(ScheduledTask.task_name == task_name).first()
    return {
        "configured": bool(task),
        "enabled": bool(task.enabled) if task else False,
        "last_run_at": task.last_run_at if task else None,
    }


@router.get("", response_class=HTMLResponse, dependencies=[Depends(require_permission("reports:operations"))])
def performance_root(request: Request):
    return RedirectResponse(url="/admin/performance/team", status_code=303)


@router.get("/team", response_class=HTMLResponse, dependencies=[Depends(require_permission("reports:operations"))])
def team_overview(request: Request, db: Session = Depends(_get_db)):
    user = get_current_user(request)
    scope = performance_reports.build_access_scope(
        db, user["person_id"], user.get("roles", []), user.get("permissions", [])
    )
    rows = performance_reports.leaderboard_for_scope(db, scope)
    # Aggregate stats for summary cards
    agent_count = len(rows)
    avg_score = round(sum(r["composite_score"] for r in rows) / agent_count, 1) if rows else 0
    top_performer = rows[0] if rows else None
    return templates.TemplateResponse(
        "admin/performance/team_overview.html",
        _ctx(
            request,
            db,
            active_page="team-performance",
            rows=rows,
            agent_count=agent_count,
            avg_score=avg_score,
            top_performer=top_performer,
        ),
    )


@router.get(
    "/team/_table", response_class=HTMLResponse, dependencies=[Depends(require_permission("reports:operations"))]
)
def team_table(request: Request, db: Session = Depends(_get_db)):
    user = get_current_user(request)
    scope = performance_reports.build_access_scope(
        db, user["person_id"], user.get("roles", []), user.get("permissions", [])
    )
    rows = performance_reports.leaderboard_for_scope(db, scope)
    return templates.TemplateResponse(
        "admin/performance/_leaderboard_table.html",
        _ctx(request, db, rows=rows),
    )


@router.get(
    "/agents/{person_id}", response_class=HTMLResponse, dependencies=[Depends(require_permission("reports:operations"))]
)
def agent_detail(request: Request, person_id: str, db: Session = Depends(_get_db)):
    user = get_current_user(request)
    scope = performance_reports.build_access_scope(
        db, user["person_id"], user.get("roles", []), user.get("permissions", [])
    )
    try:
        performance_reports.assert_can_access_person(scope, person_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    person = db.get(Person, coerce_uuid(person_id))
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    history = performance_reports.score_history(db, person_id)
    latest = history[-1] if history else None
    reviews = performance_reports.reviews(db, person_id, limit=10)
    return templates.TemplateResponse(
        "admin/performance/agent_detail.html",
        _ctx(
            request,
            db,
            active_page="team-performance",
            person_id=person_id,
            person=person,
            latest=latest,
            history=history,
            reviews=reviews,
            custom_review_error=request.query_params.get("custom_review_error", "").strip(),
            custom_review_success=request.query_params.get("custom_review_success", "").strip(),
            default_period_start=(
                latest.score_period_start.date().isoformat() if latest and latest.score_period_start else ""
            ),
            default_period_end=(latest.score_period_end.date().isoformat() if latest and latest.score_period_end else ""),
        ),
    )


@router.get(
    "/agents/{person_id}/_scores",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("reports:operations"))],
)
def agent_scores_partial(request: Request, person_id: str, db: Session = Depends(_get_db)):
    history = performance_reports.score_history(db, person_id)
    latest = history[-1] if history else None
    return templates.TemplateResponse(
        "admin/performance/_score_cards.html",
        _ctx(request, db, person_id=person_id, latest=latest),
    )


@router.get(
    "/reviews/{review_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("reports:operations"))],
)
def review_detail(request: Request, review_id: str, db: Session = Depends(_get_db)):
    user = get_current_user(request)
    scope = performance_reports.build_access_scope(
        db, user["person_id"], user.get("roles", []), user.get("permissions", [])
    )
    try:
        review = performance_reports.review_detail_for_scope(db, scope, review_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    person = db.get(Person, review.person_id)
    return templates.TemplateResponse(
        "admin/performance/review_detail.html",
        _ctx(request, db, active_page="team-performance", review=review, person=person),
    )


@router.post(
    "/agents/{person_id}/generate-review",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def generate_review(request: Request, person_id: str, db: Session = Depends(_get_db)):
    user = get_current_user(request)
    scope = performance_reports.build_access_scope(
        db, user["person_id"], user.get("roles", []), user.get("permissions", [])
    )
    try:
        performance_reports.assert_can_access_person(scope, person_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    history = performance_reports.score_history(db, person_id)
    if not history:
        raise HTTPException(status_code=400, detail="No score history for agent")
    latest = history[-1]
    try:
        performance_reviews.generate_manual_for_manager(
            db,
            requester_id=user["person_id"],
            requester_roles=user.get("roles", []),
            target_person_id=person_id,
            period_start=latest.score_period_start,
            period_end=latest.score_period_end,
            request=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/admin/performance/agents/{person_id}", status_code=303)


@router.post(
    "/agents/{person_id}/generate-review-custom",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def generate_review_custom(
    request: Request,
    person_id: str,
    period_start_date: str = Form(...),
    period_end_date: str = Form(...),
    db: Session = Depends(_get_db),
):
    user = get_current_user(request)
    scope = performance_reports.build_access_scope(
        db, user["person_id"], user.get("roles", []), user.get("permissions", [])
    )
    try:
        performance_reports.assert_can_access_person(scope, person_id)
        period_start, period_end = _parse_custom_period(period_start_date, period_end_date)
        performance_reviews.generate_manual_for_manager(
            db,
            requester_id=user["person_id"],
            requester_roles=user.get("roles", []),
            target_person_id=person_id,
            period_start=period_start,
            period_end=period_end,
            request=request,
        )
        return RedirectResponse(
            url=f"/admin/performance/agents/{person_id}?custom_review_success={quote_plus('Custom review generated')}",
            status_code=303,
        )
    except ValueError as exc:
        return RedirectResponse(
            url=f"/admin/performance/agents/{person_id}?custom_review_error={quote_plus(str(exc))}",
            status_code=303,
        )


@router.get(
    "/controls",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("system:settings:read"))],
)
def performance_controls(request: Request, db: Session = Depends(_get_db)):
    controls = {
        "review_generation_enabled": bool(resolve_value(db, SettingDomain.performance, "review_generation_enabled")),
        "flagged_threshold": _as_int_form(
            str(resolve_value(db, SettingDomain.performance, "flagged_threshold") or "70"),
            70,
        ),
        "max_reviews_per_run": _as_int_form(
            str(resolve_value(db, SettingDomain.performance, "max_reviews_per_run") or "20"),
            20,
        ),
        "review_manual_daily_limit_per_manager": _as_int_form(
            str(resolve_value(db, SettingDomain.performance, "review_manual_daily_limit_per_manager") or "25"),
            25,
        ),
        "review_cooldown_hours": _as_int_form(
            str(resolve_value(db, SettingDomain.performance, "review_cooldown_hours") or "24"),
            24,
        ),
    }
    compute_task_id = str(resolve_value(db, SettingDomain.performance, "controls_last_compute_task_id") or "").strip() or None
    flagged_task_id = str(resolve_value(db, SettingDomain.performance, "controls_last_flagged_task_id") or "").strip() or None
    run_status = {
        "compute_scores": {
            "task_name": "app.tasks.performance.compute_weekly_scores",
            "task_id": compute_task_id,
            "state": _task_state(compute_task_id),
            **_scheduled_task_meta(db, "app.tasks.performance.compute_weekly_scores"),
        },
        "generate_flagged_reviews": {
            "task_name": "app.tasks.performance.generate_flagged_reviews",
            "task_id": flagged_task_id,
            "state": _task_state(flagged_task_id),
            **_scheduled_task_meta(db, "app.tasks.performance.generate_flagged_reviews"),
        },
    }
    return templates.TemplateResponse(
        "admin/performance/controls.html",
        _ctx(
            request,
            db,
            active_page="team-performance",
            controls=controls,
            controls_error=request.query_params.get("controls_error", "").strip(),
            controls_success=request.query_params.get("controls_success", "").strip(),
            run_feedback=request.query_params.get("run_feedback", "").strip(),
            run_task_id=request.query_params.get("run_task_id", "").strip(),
            run_status=run_status,
        ),
    )


@router.post(
    "/controls/settings",
    dependencies=[Depends(require_permission("system:settings:write"))],
)
def performance_controls_save(
    request: Request,
    review_generation_enabled: str | None = Form(None),
    flagged_threshold: str = Form("70"),
    max_reviews_per_run: str = Form("20"),
    review_manual_daily_limit_per_manager: str = Form("25"),
    review_cooldown_hours: str = Form("24"),
    db: Session = Depends(_get_db),
):
    _ = request
    try:
        threshold = max(0, min(_as_int_form(flagged_threshold, 70), 100))
        max_reviews = max(1, min(_as_int_form(max_reviews_per_run, 20), 500))
        daily_limit = max(1, min(_as_int_form(review_manual_daily_limit_per_manager, 25), 500))
        cooldown = max(0, min(_as_int_form(review_cooldown_hours, 24), 720))
        enabled = _as_bool_form(review_generation_enabled)

        performance_settings.upsert_by_key(
            db,
            "review_generation_enabled",
            DomainSettingUpdate(
                value_type=SettingValueType.boolean,
                value_text="true" if enabled else "false",
                value_json=enabled,
            ),
        )
        performance_settings.upsert_by_key(
            db,
            "flagged_threshold",
            DomainSettingUpdate(
                value_type=SettingValueType.integer,
                value_text=str(threshold),
                value_json=threshold,
            ),
        )
        performance_settings.upsert_by_key(
            db,
            "max_reviews_per_run",
            DomainSettingUpdate(
                value_type=SettingValueType.integer,
                value_text=str(max_reviews),
                value_json=max_reviews,
            ),
        )
        performance_settings.upsert_by_key(
            db,
            "review_manual_daily_limit_per_manager",
            DomainSettingUpdate(
                value_type=SettingValueType.integer,
                value_text=str(daily_limit),
                value_json=daily_limit,
            ),
        )
        performance_settings.upsert_by_key(
            db,
            "review_cooldown_hours",
            DomainSettingUpdate(
                value_type=SettingValueType.integer,
                value_text=str(cooldown),
                value_json=cooldown,
            ),
        )
        return RedirectResponse(
            url="/admin/performance/controls?controls_success=Settings+saved",
            status_code=303,
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/admin/performance/controls?controls_error={quote_plus(str(exc))}",
            status_code=303,
        )


@router.post(
    "/controls/run",
    dependencies=[Depends(require_permission("system:settings:write"))],
)
def performance_controls_run(
    request: Request,
    action: str = Form(...),
    db: Session = Depends(_get_db),
):
    _ = request
    try:
        if action == "compute_scores":
            task = compute_weekly_scores.delay()
            feedback = "Queued weekly score computation"
            persisted_key = "controls_last_compute_task_id"
            scheduled_task_name = "app.tasks.performance.compute_weekly_scores"
        elif action == "generate_flagged_reviews":
            task = generate_flagged_reviews.delay()
            feedback = "Queued flagged review generation"
            persisted_key = "controls_last_flagged_task_id"
            scheduled_task_name = "app.tasks.performance.generate_flagged_reviews"
        else:
            return RedirectResponse(
                url="/admin/performance/controls?run_feedback=Unknown+action",
                status_code=303,
            )
        performance_settings.upsert_by_key(
            db,
            persisted_key,
            DomainSettingUpdate(
                value_type=SettingValueType.string,
                value_text=task.id,
                value_json=task.id,
            ),
        )
        scheduled_task = db.query(ScheduledTask).filter(ScheduledTask.task_name == scheduled_task_name).first()
        if scheduled_task:
            scheduled_task.last_run_at = datetime.now(UTC)
            db.commit()
        return RedirectResponse(
            url=f"/admin/performance/controls?run_feedback={quote_plus(feedback)}&run_task_id={task.id}",
            status_code=303,
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/admin/performance/controls?run_feedback={quote_plus(str(exc))}",
            status_code=303,
        )


@router.get("/goals", response_class=HTMLResponse, dependencies=[Depends(require_permission("reports:operations"))])
def goals_index(
    request: Request,
    person_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    domain: str | None = Query(default=None),
    db: Session = Depends(_get_db),
):
    user = get_current_user(request)
    scope = performance_reports.build_access_scope(
        db, user["person_id"], user.get("roles", []), user.get("permissions", [])
    )
    managed_ids = scope.managed_person_ids
    goals = [goal for goal in performance_goals.list(db) if str(goal.person_id) in managed_ids]
    if person_id:
        goals = [goal for goal in goals if str(goal.person_id) == person_id]
    if status and status in GoalStatus._value2member_map_:
        goals = [goal for goal in goals if goal.status == GoalStatus(status)]
    if domain and domain in PerformanceDomain._value2member_map_:
        goals = [goal for goal in goals if goal.domain == PerformanceDomain(domain)]

    people = (
        db.query(Person)
        .filter(Person.id.in_([coerce_uuid(pid) for pid in managed_ids]))
        .order_by(Person.first_name.asc(), Person.last_name.asc(), Person.display_name.asc())
        .all()
    )
    person_map = {str(person.id): person for person in people}

    return templates.TemplateResponse(
        "admin/performance/goals.html",
        _ctx(
            request,
            db,
            active_page="team-performance",
            goals=goals,
            people=people,
            person_map=person_map,
            statuses=[item.value for item in GoalStatus],
            domains=[item.value for item in PerformanceDomain],
            filters={"person_id": person_id or "", "status": status or "", "domain": domain or ""},
            error=request.query_params.get("error", "").strip(),
        ),
    )


@router.post("/goals", dependencies=[Depends(require_permission("reports:operations"))])
def goals_create(
    request: Request,
    person_id: str = Form(...),
    domain: str = Form(...),
    metric_key: str = Form(...),
    label: str = Form(...),
    target_value: float = Form(...),
    comparison: str = Form(...),
    deadline: str = Form(...),
    db: Session = Depends(_get_db),
):
    user = get_current_user(request)
    scope = performance_reports.build_access_scope(
        db, user["person_id"], user.get("roles", []), user.get("permissions", [])
    )
    try:
        performance_reports.assert_can_access_person(scope, person_id)
        payload = AgentPerformanceGoalCreate(
            person_id=person_id,
            domain=PerformanceDomain(domain),
            metric_key=metric_key.strip(),
            label=label.strip(),
            target_value=target_value,
            comparison=comparison.strip(),
            deadline=_parse_deadline(deadline) or date.today(),
        )
        performance_goals.create(db, payload, created_by_person_id=user.get("person_id"))
        return RedirectResponse(url="/admin/performance/goals", status_code=303)
    except Exception as exc:
        return RedirectResponse(url=f"/admin/performance/goals?error={quote_plus(str(exc))}", status_code=303)


@router.post("/goals/{goal_id}/update", dependencies=[Depends(require_permission("reports:operations"))])
def goals_update(
    request: Request,
    goal_id: str,
    label: str | None = Form(default=None),
    target_value: float | None = Form(default=None),
    comparison: str | None = Form(default=None),
    deadline: str | None = Form(default=None),
    status: str | None = Form(default=None),
    db: Session = Depends(_get_db),
):
    user = get_current_user(request)
    scope = performance_reports.build_access_scope(
        db, user["person_id"], user.get("roles", []), user.get("permissions", [])
    )
    try:
        goal = performance_goals.get(db, goal_id)
        performance_reports.assert_can_access_person(scope, str(goal.person_id))
        payload = AgentPerformanceGoalUpdate(
            label=(label.strip() if isinstance(label, str) else None) or None,
            target_value=target_value,
            comparison=(comparison.strip() if isinstance(comparison, str) else None) or None,
            deadline=_parse_deadline(deadline),
            status=GoalStatus(status) if status and status in GoalStatus._value2member_map_ else None,
        )
        performance_goals.update(db, goal_id, payload)
        return RedirectResponse(url="/admin/performance/goals", status_code=303)
    except Exception as exc:
        return RedirectResponse(url=f"/admin/performance/goals?error={quote_plus(str(exc))}", status_code=303)
