"""Admin survey management routes."""

import contextlib
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.comms import CustomerSurveyStatus, SurveyQuestionType, SurveyTriggerType
from app.models.person import Person
from app.schemas.comms import SurveyCreate, SurveyQuestion, SurveyUpdate
from app.services.surveys import survey_invitations, survey_manager, survey_responses
from app.tasks.surveys import distribute_survey
from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/surveys", tags=["web-admin-surveys"])


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _base_ctx(request: Request, db: Session, **kwargs) -> dict:
    current_user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)
    return {
        "request": request,
        "current_user": current_user,
        "sidebar_stats": sidebar_stats,
        "active_page": "surveys",
        **kwargs,
    }


def _form_str(val: object | None, default: str = "") -> str:
    if isinstance(val, str):
        return val
    return default


def _form_str_opt(val: object | None) -> str | None:
    value = _form_str(val).strip()
    if not value:
        return None
    return value


# ── LIST ──────────────────────────────────────────────────────────


@router.get("", response_class=HTMLResponse)
def survey_list(
    request: Request,
    db: Session = Depends(_get_db),
    status: str | None = Query(None),
    trigger_type: str | None = Query(None),
    search: str | None = Query(None),
):
    items = survey_manager.list(db, status=status, trigger_type=trigger_type, search=search)
    status_counts = survey_manager.count_by_status(db)
    ctx = _base_ctx(
        request,
        db,
        surveys=items,
        status_counts=status_counts,
        filter_status=status or "",
        filter_trigger=trigger_type or "",
        search=search or "",
        CustomerSurveyStatus=CustomerSurveyStatus,
    )
    return templates.TemplateResponse("admin/surveys/index.html", ctx)


# ── CREATE FORM ───────────────────────────────────────────────────


@router.get("/new", response_class=HTMLResponse)
def survey_create_form(request: Request, db: Session = Depends(_get_db)):
    ctx = _base_ctx(
        request,
        db,
        survey=None,
        question_types=SurveyQuestionType,
        trigger_types=SurveyTriggerType,
        errors=[],
    )
    return templates.TemplateResponse("admin/surveys/form.html", ctx)


# ── CREATE ────────────────────────────────────────────────────────


@router.post("", response_class=HTMLResponse)
async def survey_create(request: Request, db: Session = Depends(_get_db)):
    current_user = get_current_user(request)
    created_by_id = current_user.get("person_id") if current_user else None

    form = await request.form()
    name = _form_str(form.get("name", "")).strip()
    description = _form_str_opt(form.get("description", ""))
    trigger_type_val = _form_str(form.get("trigger_type", "manual"))
    public_slug = _form_str_opt(form.get("public_slug", ""))
    thank_you_message = _form_str_opt(form.get("thank_you_message", ""))

    try:
        trigger_type = SurveyTriggerType(trigger_type_val)
    except ValueError:
        trigger_type = SurveyTriggerType.manual

    # Parse questions from form (JSON encoded by Alpine.js)
    import json

    questions_json = _form_str(form.get("questions_json", "[]"), "[]")
    questions = []
    try:
        raw_questions = json.loads(questions_json)
        for rq in raw_questions:
            questions.append(
                SurveyQuestion(
                    key=rq.get("key", ""),
                    type=SurveyQuestionType(rq.get("type", "free_text")),
                    label=rq.get("label", ""),
                    required=rq.get("required", True),
                    options=rq.get("options") or None,
                )
            )
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to parse questions: %s", e)

    payload = SurveyCreate(
        name=name,
        description=description,
        questions=questions if questions else None,
        trigger_type=trigger_type,
        public_slug=public_slug,
        thank_you_message=thank_you_message,
    )
    survey = survey_manager.create(db, payload, created_by_id=created_by_id)
    return RedirectResponse(url=f"/admin/surveys/{survey.id}", status_code=303)


# ── DETAIL ────────────────────────────────────────────────────────


@router.get("/{survey_id}", response_class=HTMLResponse)
def survey_detail(request: Request, survey_id: str, db: Session = Depends(_get_db)):
    survey = survey_manager.get(db, survey_id)
    stats = survey_manager.analytics(db, survey_id)
    responses = survey_responses.list(db, survey_id=survey_id, limit=20)
    invitations = survey_invitations.list(db, survey_id=survey_id, limit=20)

    # Batch load person names for invitations
    person_ids = [inv.person_id for inv in invitations]
    persons = db.query(Person).filter(Person.id.in_(person_ids)).all() if person_ids else []
    person_map = {str(p.id): p for p in persons}

    ctx = _base_ctx(
        request,
        db,
        survey=survey,
        stats=stats,
        responses=responses,
        invitations=invitations,
        person_map=person_map,
        CustomerSurveyStatus=CustomerSurveyStatus,
    )
    return templates.TemplateResponse("admin/surveys/detail.html", ctx)


# ── EDIT FORM ─────────────────────────────────────────────────────


@router.get("/{survey_id}/edit", response_class=HTMLResponse)
def survey_edit_form(request: Request, survey_id: str, db: Session = Depends(_get_db)):
    survey = survey_manager.get(db, survey_id)
    ctx = _base_ctx(
        request,
        db,
        survey=survey,
        question_types=SurveyQuestionType,
        trigger_types=SurveyTriggerType,
        errors=[],
    )
    return templates.TemplateResponse("admin/surveys/form.html", ctx)


# ── UPDATE ────────────────────────────────────────────────────────


@router.post("/{survey_id}", response_class=HTMLResponse)
async def survey_update(request: Request, survey_id: str, db: Session = Depends(_get_db)):
    form = await request.form()
    name = _form_str_opt(form.get("name", ""))
    description = _form_str_opt(form.get("description", ""))
    trigger_type_val = _form_str(form.get("trigger_type"))
    public_slug = _form_str_opt(form.get("public_slug", ""))
    thank_you_message = _form_str_opt(form.get("thank_you_message", ""))

    trigger_type = None
    if trigger_type_val:
        with contextlib.suppress(ValueError):
            trigger_type = SurveyTriggerType(trigger_type_val)

    import json

    questions_json = _form_str(form.get("questions_json", "[]"), "[]")
    questions = None
    try:
        raw_questions = json.loads(questions_json)
        if raw_questions:
            questions = [
                SurveyQuestion(
                    key=rq.get("key", ""),
                    type=SurveyQuestionType(rq.get("type", "free_text")),
                    label=rq.get("label", ""),
                    required=rq.get("required", True),
                    options=rq.get("options") or None,
                )
                for rq in raw_questions
            ]
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to parse questions: %s", e)

    update_data: dict[str, Any] = {}
    if name:
        update_data["name"] = name
    if description is not None:
        update_data["description"] = description
    if trigger_type is not None:
        update_data["trigger_type"] = trigger_type
    if public_slug is not None:
        update_data["public_slug"] = public_slug
    if thank_you_message is not None:
        update_data["thank_you_message"] = thank_you_message
    if questions is not None:
        update_data["questions"] = questions

    payload = SurveyUpdate(**update_data)
    survey_manager.update(db, survey_id, payload)
    return RedirectResponse(url=f"/admin/surveys/{survey_id}", status_code=303)


# ── DELETE ────────────────────────────────────────────────────────


@router.post("/{survey_id}/delete")
def survey_delete(survey_id: str, db: Session = Depends(_get_db)):
    survey_manager.delete(db, survey_id)
    return RedirectResponse(url="/admin/surveys", status_code=303)


# ── STATUS ACTIONS ────────────────────────────────────────────────


@router.post("/{survey_id}/activate")
def survey_activate(survey_id: str, db: Session = Depends(_get_db)):
    survey_manager.activate(db, survey_id)
    return RedirectResponse(url=f"/admin/surveys/{survey_id}", status_code=303)


@router.post("/{survey_id}/pause")
def survey_pause(survey_id: str, db: Session = Depends(_get_db)):
    survey_manager.pause(db, survey_id)
    return RedirectResponse(url=f"/admin/surveys/{survey_id}", status_code=303)


@router.post("/{survey_id}/close")
def survey_close(survey_id: str, db: Session = Depends(_get_db)):
    survey_manager.close(db, survey_id)
    return RedirectResponse(url=f"/admin/surveys/{survey_id}", status_code=303)


# ── MANUAL SEND ───────────────────────────────────────────────────


@router.post("/{survey_id}/send")
def survey_send(survey_id: str, db: Session = Depends(_get_db)):
    survey = survey_manager.get(db, survey_id)
    if survey.status != CustomerSurveyStatus.active:
        survey_manager.activate(db, survey_id)
    distribute_survey.delay(survey_id)
    return RedirectResponse(url=f"/admin/surveys/{survey_id}", status_code=303)


# ── HTMX PARTIALS ────────────────────────────────────────────────


@router.get("/{survey_id}/preview-audience", response_class=HTMLResponse)
def survey_preview_audience(request: Request, survey_id: str, db: Session = Depends(_get_db)):
    survey = survey_manager.get(db, survey_id)
    result = survey_manager.preview_audience(db, survey.segment_filter)
    ctx = _base_ctx(request, db, audience=result, survey=survey)
    return templates.TemplateResponse("admin/surveys/_audience_preview.html", ctx)


@router.get("/{survey_id}/responses", response_class=HTMLResponse)
def survey_responses_partial(request: Request, survey_id: str, db: Session = Depends(_get_db)):
    responses = survey_responses.list(db, survey_id=survey_id, limit=50)
    ctx = _base_ctx(request, db, responses=responses, survey_id=survey_id)
    return templates.TemplateResponse("admin/surveys/_responses_table.html", ctx)


@router.get("/{survey_id}/analytics", response_class=HTMLResponse)
def survey_analytics_partial(request: Request, survey_id: str, db: Session = Depends(_get_db)):
    stats = survey_manager.analytics(db, survey_id)
    ctx = _base_ctx(request, db, stats=stats)
    return templates.TemplateResponse("admin/surveys/_analytics.html", ctx)
