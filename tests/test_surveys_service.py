from app.models.comms import SurveyQuestionType
from app.schemas.comms import SurveyCreate, SurveyQuestion, SurveyUpdate
from app.services.surveys import survey_manager, survey_responses


def test_survey_create_serializes_question_type_enum(db_session):
    survey = survey_manager.create(
        db_session,
        SurveyCreate(
            name="CSAT",
            questions=[
                SurveyQuestion(
                    key="rating",
                    type=SurveyQuestionType.rating,
                    label="How was your experience?",
                    required=True,
                )
            ],
        ),
    )

    assert isinstance(survey.questions, list)
    assert survey.questions[0]["type"] == "rating"


def test_survey_update_serializes_question_type_enum(db_session):
    survey = survey_manager.create(
        db_session,
        SurveyCreate(
            name="CSAT",
            questions=[],
        ),
    )

    updated = survey_manager.update(
        db_session,
        str(survey.id),
        SurveyUpdate(
            questions=[
                SurveyQuestion(
                    key="nps",
                    type=SurveyQuestionType.nps,
                    label="How likely are you to recommend us?",
                    required=True,
                )
            ]
        ),
    )

    assert isinstance(updated.questions, list)
    assert updated.questions[0]["type"] == "nps"


def test_survey_submit_recalculates_avg_rating_without_error(db_session):
    survey = survey_manager.create(
        db_session,
        SurveyCreate(
            name="CRM CSAT",
            questions=[
                SurveyQuestion(
                    key="rating",
                    type=SurveyQuestionType.rating,
                    label="How satisfied are you with our support?",
                    required=True,
                )
            ],
        ),
    )

    first = survey_responses.submit(db_session, str(survey.id), {"rating": "5"})
    second = survey_responses.submit(db_session, str(survey.id), {"rating": "3"})

    refreshed = survey_manager.get(db_session, str(survey.id))

    assert first.rating == 5
    assert second.rating == 3
    assert refreshed.total_responses == 2
    assert refreshed.avg_rating == 4.0


def test_survey_submit_does_not_coerce_zero_rating_to_one(db_session):
    survey = survey_manager.create(
        db_session,
        SurveyCreate(
            name="CRM CSAT",
            questions=[
                SurveyQuestion(
                    key="rating",
                    type=SurveyQuestionType.rating,
                    label="How satisfied are you with our support?",
                    required=True,
                )
            ],
        ),
    )

    response = survey_responses.submit(db_session, str(survey.id), {"rating": "0"})
    refreshed = survey_manager.get(db_session, str(survey.id))

    assert response.rating is None
    assert refreshed.total_responses == 1
    assert refreshed.avg_rating is None
