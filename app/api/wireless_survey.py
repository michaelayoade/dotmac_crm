from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.wireless_survey import (
    ElevationProfileRequest,
    ElevationProfileResponse,
    SurveyLosPathRead,
    SurveyPointCreate,
    SurveyPointRead,
    SurveyPointUpdate,
    WirelessSiteSurveyCreate,
    WirelessSiteSurveyDetail,
    WirelessSiteSurveyRead,
    WirelessSiteSurveyUpdate,
)
from app.services import wireless_survey as ws_service

router = APIRouter(prefix="/wireless-survey")


# Survey endpoints
@router.post(
    "/surveys",
    response_model=WirelessSiteSurveyRead,
    status_code=status.HTTP_201_CREATED,
    tags=["wireless-surveys"],
)
def create_survey(payload: WirelessSiteSurveyCreate, db: Session = Depends(get_db)):
    """Create a new wireless site survey."""
    return ws_service.wireless_surveys.create(db, payload)


@router.get(
    "/surveys/{survey_id}",
    response_model=WirelessSiteSurveyDetail,
    tags=["wireless-surveys"],
)
def get_survey(survey_id: str, db: Session = Depends(get_db)):
    """Get a survey with all points and LOS paths."""
    return ws_service.wireless_surveys.get_detail(db, survey_id)


@router.get(
    "/surveys",
    response_model=list[WirelessSiteSurveyRead],
    tags=["wireless-surveys"],
)
def list_surveys(
    status: str | None = None,
    project_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List wireless site surveys."""
    return ws_service.wireless_surveys.list(db, status, project_id, limit, offset)


@router.patch(
    "/surveys/{survey_id}",
    response_model=WirelessSiteSurveyRead,
    tags=["wireless-surveys"],
)
def update_survey(
    survey_id: str, payload: WirelessSiteSurveyUpdate, db: Session = Depends(get_db)
):
    """Update a wireless site survey."""
    return ws_service.wireless_surveys.update(db, survey_id, payload)


@router.delete(
    "/surveys/{survey_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["wireless-surveys"],
)
def delete_survey(survey_id: str, db: Session = Depends(get_db)):
    """Delete a wireless site survey."""
    ws_service.wireless_surveys.delete(db, survey_id)


# Survey point endpoints
@router.post(
    "/surveys/{survey_id}/points",
    response_model=SurveyPointRead,
    status_code=status.HTTP_201_CREATED,
    tags=["survey-points"],
)
def create_survey_point(
    survey_id: str, payload: SurveyPointCreate, db: Session = Depends(get_db)
):
    """Add a point to a survey. Elevation is automatically fetched."""
    return ws_service.survey_points.create(db, survey_id, payload)


@router.get(
    "/surveys/{survey_id}/points",
    response_model=list[SurveyPointRead],
    tags=["survey-points"],
)
def list_survey_points(survey_id: str, db: Session = Depends(get_db)):
    """List all points in a survey."""
    return ws_service.survey_points.list(db, survey_id)


@router.get(
    "/points/{point_id}",
    response_model=SurveyPointRead,
    tags=["survey-points"],
)
def get_survey_point(point_id: str, db: Session = Depends(get_db)):
    """Get a survey point."""
    return ws_service.survey_points.get(db, point_id)


@router.patch(
    "/points/{point_id}",
    response_model=SurveyPointRead,
    tags=["survey-points"],
)
def update_survey_point(
    point_id: str, payload: SurveyPointUpdate, db: Session = Depends(get_db)
):
    """Update a survey point. Elevation is refreshed if coordinates change."""
    return ws_service.survey_points.update(db, point_id, payload)


@router.delete(
    "/points/{point_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["survey-points"],
)
def delete_survey_point(point_id: str, db: Session = Depends(get_db)):
    """Delete a survey point."""
    ws_service.survey_points.delete(db, point_id)


@router.post(
    "/points/{point_id}/refresh-elevation",
    response_model=SurveyPointRead,
    tags=["survey-points"],
)
def refresh_point_elevation(point_id: str, db: Session = Depends(get_db)):
    """Re-fetch elevation data for a point."""
    return ws_service.survey_points.refresh_elevation(db, point_id)


# LOS analysis endpoints
@router.post(
    "/surveys/{survey_id}/analyze-los",
    response_model=SurveyLosPathRead,
    tags=["los-analysis"],
)
def analyze_los_path(
    survey_id: str,
    from_point_id: str = Query(..., description="Source point ID"),
    to_point_id: str = Query(..., description="Destination point ID"),
    sample_count: int = Query(default=100, ge=10, le=500),
    db: Session = Depends(get_db),
):
    """Analyze line-of-sight between two survey points."""
    return ws_service.survey_los.analyze_path(
        db, survey_id, from_point_id, to_point_id, sample_count
    )


@router.get(
    "/surveys/{survey_id}/los-paths",
    response_model=list[SurveyLosPathRead],
    tags=["los-analysis"],
)
def list_los_paths(survey_id: str, db: Session = Depends(get_db)):
    """List all LOS paths in a survey."""
    return ws_service.survey_los.list(db, survey_id)


@router.get(
    "/los-paths/{path_id}",
    response_model=SurveyLosPathRead,
    tags=["los-analysis"],
)
def get_los_path(path_id: str, db: Session = Depends(get_db)):
    """Get a LOS path with elevation profile."""
    return ws_service.survey_los.get(db, path_id)


@router.delete(
    "/los-paths/{path_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["los-analysis"],
)
def delete_los_path(path_id: str, db: Session = Depends(get_db)):
    """Delete a LOS path."""
    ws_service.survey_los.delete(db, path_id)


# Standalone elevation profile (no survey required)
@router.post(
    "/elevation-profile",
    response_model=ElevationProfileResponse,
    tags=["los-analysis"],
)
def calculate_elevation_profile(request: ElevationProfileRequest):
    """Calculate elevation profile between two points without saving to a survey."""
    return ws_service.calculate_elevation_profile(request)
