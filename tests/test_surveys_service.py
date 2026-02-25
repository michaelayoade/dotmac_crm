from app.models.comms import SurveyQuestionType
from app.schemas.comms import SurveyCreate, SurveyQuestion, SurveyUpdate
from app.services.surveys import survey_manager


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
