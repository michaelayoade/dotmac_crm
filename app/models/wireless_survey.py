import enum
import uuid
from datetime import UTC, datetime

from geoalchemy2 import Geometry
from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SurveyStatus(enum.Enum):
    draft = "draft"
    in_progress = "in_progress"
    completed = "completed"
    archived = "archived"


class SurveyPointType(enum.Enum):
    tower = "tower"
    access_point = "access_point"
    cpe = "cpe"
    repeater = "repeater"
    custom = "custom"


class WirelessSiteSurvey(Base):
    """A wireless site survey containing multiple survey points and analysis."""

    __tablename__ = "wireless_site_surveys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[SurveyStatus] = mapped_column(Enum(SurveyStatus), default=SurveyStatus.draft)

    # Survey bounds (calculated from points)
    min_latitude: Mapped[float | None] = mapped_column(Float)
    min_longitude: Mapped[float | None] = mapped_column(Float)
    max_latitude: Mapped[float | None] = mapped_column(Float)
    max_longitude: Mapped[float | None] = mapped_column(Float)

    # Wireless parameters
    frequency_mhz: Mapped[float | None] = mapped_column(Float)
    default_antenna_height_m: Mapped[float] = mapped_column(Float, default=10.0)
    default_tx_power_dbm: Mapped[float] = mapped_column(Float, default=20.0)

    # Metadata
    notes: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)

    # Assignment
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    points = relationship("SurveyPoint", back_populates="survey", cascade="all, delete-orphan")
    los_paths = relationship("SurveyLosPath", back_populates="survey", cascade="all, delete-orphan")
    created_by = relationship("Person")
    project = relationship("Project")


class SurveyPoint(Base):
    """A point in a wireless site survey with elevation and RF parameters."""

    __tablename__ = "survey_points"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    survey_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wireless_site_surveys.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    point_type: Mapped[SurveyPointType] = mapped_column(Enum(SurveyPointType), default=SurveyPointType.custom)

    # Location
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    geom = mapped_column(Geometry("POINT", srid=4326), nullable=True)

    # Elevation data
    ground_elevation_m: Mapped[float | None] = mapped_column(Float)
    elevation_source: Mapped[str | None] = mapped_column(String(50))
    elevation_tile: Mapped[str | None] = mapped_column(String(20))

    # Antenna/Equipment parameters
    antenna_height_m: Mapped[float] = mapped_column(Float, default=10.0)
    antenna_gain_dbi: Mapped[float | None] = mapped_column(Float)
    tx_power_dbm: Mapped[float | None] = mapped_column(Float)

    # Calculated total height (ground + antenna)
    @property
    def total_height_m(self) -> float | None:
        if self.ground_elevation_m is not None:
            return self.ground_elevation_m + self.antenna_height_m
        return None

    # Notes and metadata
    notes: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    survey = relationship("WirelessSiteSurvey", back_populates="points")


class SurveyLosPath(Base):
    """Line-of-sight analysis between two survey points."""

    __tablename__ = "survey_los_paths"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    survey_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wireless_site_surveys.id"), nullable=False
    )
    from_point_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("survey_points.id"), nullable=False)
    to_point_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("survey_points.id"), nullable=False)

    # Distance and bearing
    distance_m: Mapped[float | None] = mapped_column(Float)
    bearing_deg: Mapped[float | None] = mapped_column(Float)

    # LOS analysis results
    has_clear_los: Mapped[bool | None] = mapped_column(Boolean)
    fresnel_clearance_pct: Mapped[float | None] = mapped_column(Float)
    max_obstruction_m: Mapped[float | None] = mapped_column(Float)
    obstruction_distance_m: Mapped[float | None] = mapped_column(Float)

    # Elevation profile (array of {distance_m, elevation_m})
    elevation_profile: Mapped[list | None] = mapped_column(JSON)

    # RF calculations
    free_space_loss_db: Mapped[float | None] = mapped_column(Float)
    estimated_rssi_dbm: Mapped[float | None] = mapped_column(Float)

    # Analysis metadata
    analysis_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sample_count: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    survey = relationship("WirelessSiteSurvey", back_populates="los_paths")
    from_point = relationship("SurveyPoint", foreign_keys=[from_point_id])
    to_point = relationship("SurveyPoint", foreign_keys=[to_point_id])
