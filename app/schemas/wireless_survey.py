from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.wireless_survey import SurveyPointType, SurveyStatus


# Survey Point schemas
class SurveyPointBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=160)
    point_type: SurveyPointType = SurveyPointType.custom
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    antenna_height_m: float = Field(default=10.0, ge=0, le=500)
    antenna_gain_dbi: float | None = None
    tx_power_dbm: float | None = None
    notes: str | None = None
    metadata_: dict | None = Field(
        default=None,
        serialization_alias="metadata",
    )
    sort_order: int = 0


class SurveyPointCreate(SurveyPointBase):
    pass


class SurveyPointUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = Field(default=None, min_length=1, max_length=160)
    point_type: SurveyPointType | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    antenna_height_m: float | None = Field(default=None, ge=0, le=500)
    antenna_gain_dbi: float | None = None
    tx_power_dbm: float | None = None
    notes: str | None = None
    metadata_: dict | None = Field(
        default=None,
        serialization_alias="metadata",
    )
    sort_order: int | None = None


class SurveyPointRead(SurveyPointBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    survey_id: UUID
    ground_elevation_m: float | None = None
    elevation_source: str | None = None
    elevation_tile: str | None = None
    total_height_m: float | None = None
    created_at: datetime
    updated_at: datetime


# LOS Path schemas
class SurveyLosPathRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    survey_id: UUID
    from_point_id: UUID
    to_point_id: UUID
    distance_m: float | None = None
    bearing_deg: float | None = None
    has_clear_los: bool | None = None
    fresnel_clearance_pct: float | None = None
    max_obstruction_m: float | None = None
    obstruction_distance_m: float | None = None
    elevation_profile: list | None = None
    free_space_loss_db: float | None = None
    estimated_rssi_dbm: float | None = None
    analysis_timestamp: datetime | None = None
    sample_count: int | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


# Survey schemas
class WirelessSiteSurveyBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    status: SurveyStatus = SurveyStatus.draft
    frequency_mhz: float | None = Field(default=None, ge=100, le=100000)
    default_antenna_height_m: float = Field(default=10.0, ge=0, le=500)
    default_tx_power_dbm: float = Field(default=20.0, ge=-30, le=60)
    notes: str | None = None
    metadata_: dict | None = Field(
        default=None,
        serialization_alias="metadata",
    )
    project_id: UUID | None = None


class WirelessSiteSurveyCreate(WirelessSiteSurveyBase):
    pass


class WirelessSiteSurveyUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: SurveyStatus | None = None
    frequency_mhz: float | None = Field(default=None, ge=100, le=100000)
    default_antenna_height_m: float | None = Field(default=None, ge=0, le=500)
    default_tx_power_dbm: float | None = Field(default=None, ge=-30, le=60)
    notes: str | None = None
    metadata_: dict | None = Field(
        default=None,
        serialization_alias="metadata",
    )
    project_id: UUID | None = None


class WirelessSiteSurveyRead(WirelessSiteSurveyBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    min_latitude: float | None = None
    min_longitude: float | None = None
    max_latitude: float | None = None
    max_longitude: float | None = None
    created_by_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class WirelessSiteSurveyDetail(WirelessSiteSurveyRead):
    """Survey with points and LOS paths."""
    points: list[SurveyPointRead] = []
    los_paths: list[SurveyLosPathRead] = []


# Elevation profile request/response
class ElevationProfileRequest(BaseModel):
    from_lat: float = Field(ge=-90, le=90)
    from_lon: float = Field(ge=-180, le=180)
    to_lat: float = Field(ge=-90, le=90)
    to_lon: float = Field(ge=-180, le=180)
    sample_count: int = Field(default=100, ge=10, le=500)
    from_antenna_height_m: float = Field(default=10.0, ge=0, le=500)
    to_antenna_height_m: float = Field(default=10.0, ge=0, le=500)
    frequency_mhz: float | None = Field(default=None, ge=100, le=100000)


class ElevationProfilePoint(BaseModel):
    distance_m: float
    latitude: float
    longitude: float
    ground_elevation_m: float | None = None
    los_height_m: float | None = None
    fresnel_radius_m: float | None = None
    available: bool = True


class ElevationProfileResponse(BaseModel):
    from_lat: float
    from_lon: float
    to_lat: float
    to_lon: float
    total_distance_m: float
    bearing_deg: float
    from_elevation_m: float | None = None
    to_elevation_m: float | None = None
    from_total_height_m: float | None = None
    to_total_height_m: float | None = None
    has_clear_los: bool | None = None
    fresnel_clearance_pct: float | None = None
    max_obstruction_m: float | None = None
    obstruction_distance_m: float | None = None
    free_space_loss_db: float | None = None
    profile: list[ElevationProfilePoint] = []
    sample_count: int
    data_coverage_pct: float = 0.0


# Quick elevation lookup
class QuickElevationRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class QuickElevationResponse(BaseModel):
    latitude: float
    longitude: float
    elevation_m: float | None = None
    tile: str | None = None
    source: str | None = None
    available: bool = False
