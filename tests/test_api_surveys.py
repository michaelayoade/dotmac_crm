"""Public surveys JSON API — anonymous slug + tokenized invitation collection."""

import pytest
from fastapi import HTTPException

from app.api import surveys as surveys_api
from app.models.comms import Survey


def _survey(db, slug="feedback"):
    survey = Survey(name="Feedback", public_slug=slug, questions=[], is_active=True)
    db.add(survey)
    db.commit()
    db.refresh(survey)
    return survey


def test_get_definition_and_submit(db_session):
    _survey(db_session)
    definition = surveys_api.get_survey("feedback", db_session)
    assert definition["name"] == "Feedback"
    assert "questions" in definition

    result = surveys_api.submit_response("feedback", surveys_api.SurveyResponseSubmit(answers={}), db_session)
    assert result["ok"] is True


def test_unknown_slug_404(db_session):
    with pytest.raises(HTTPException) as exc:
        surveys_api.get_survey("does-not-exist", db_session)
    assert exc.value.status_code == 404


def test_unknown_invitation_404(db_session):
    with pytest.raises(HTTPException) as exc:
        surveys_api.get_invitation("bad-token", db_session)
    assert exc.value.status_code == 404
