from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import HTTPException
from geoalchemy2.functions import ST_MakePoint, ST_SetSRID
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models.person import Person
from app.models.wireless_survey import SurveyLosPath, SurveyPoint, SurveyPointType, WirelessSiteSurvey
from app.schemas.wireless_survey import (
    ElevationProfilePoint,
    ElevationProfileRequest,
    ElevationProfileResponse,
    SurveyPointCreate,
    SurveyPointUpdate,
    WirelessSiteSurveyCreate,
    WirelessSiteSurveyUpdate,
)
from app.services import dem as dem_service
from app.services import projects as projects_service
from app.services.common import coerce_uuid

if TYPE_CHECKING:
    pass

EARTH_RADIUS_M = 6371000.0
SPEED_OF_LIGHT = 299792458.0


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in meters using Haversine formula."""
    lat1_rad, lon1_rad = math.radians(lat1), math.radians(lon1)
    lat2_rad, lon2_rad = math.radians(lat2), math.radians(lon2)

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_M * c


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate bearing from point 1 to point 2 in degrees."""
    lat1_rad, lon1_rad = math.radians(lat1), math.radians(lon1)
    lat2_rad, lon2_rad = math.radians(lat2), math.radians(lon2)

    dlon = lon2_rad - lon1_rad
    x = math.sin(dlon) * math.cos(lat2_rad)
    y = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(dlon)

    bearing_rad = math.atan2(x, y)
    return (math.degrees(bearing_rad) + 360) % 360


def _interpolate_point(lat1: float, lon1: float, lat2: float, lon2: float, fraction: float) -> tuple[float, float]:
    """Interpolate a point along the great circle path between two points."""
    lat1_rad, lon1_rad = math.radians(lat1), math.radians(lon1)
    lat2_rad, lon2_rad = math.radians(lat2), math.radians(lon2)

    d = _haversine_distance(lat1, lon1, lat2, lon2) / EARTH_RADIUS_M
    if d < 1e-10:
        return lat1, lon1

    a = math.sin((1 - fraction) * d) / math.sin(d)
    b = math.sin(fraction * d) / math.sin(d)

    x = a * math.cos(lat1_rad) * math.cos(lon1_rad) + b * math.cos(lat2_rad) * math.cos(lon2_rad)
    y = a * math.cos(lat1_rad) * math.sin(lon1_rad) + b * math.cos(lat2_rad) * math.sin(lon2_rad)
    z = a * math.sin(lat1_rad) + b * math.sin(lat2_rad)

    lat_rad = math.atan2(z, math.sqrt(x**2 + y**2))
    lon_rad = math.atan2(y, x)

    return math.degrees(lat_rad), math.degrees(lon_rad)


def _free_space_path_loss(distance_m: float, frequency_mhz: float) -> float:
    """Calculate free space path loss in dB."""
    if distance_m <= 0 or frequency_mhz <= 0:
        return 0.0
    # FSPL = 20*log10(d) + 20*log10(f) + 20*log10(4*pi/c) where c is speed of light
    # Simplified: FSPL = 20*log10(d_km) + 20*log10(f_mhz) + 32.45
    distance_km = distance_m / 1000.0
    return 20 * math.log10(distance_km) + 20 * math.log10(frequency_mhz) + 32.45


def _fresnel_radius(distance_from_tx_m: float, total_distance_m: float, frequency_mhz: float, zone: int = 1) -> float:
    """Calculate first Fresnel zone radius at a point along the path."""
    if distance_from_tx_m <= 0 or distance_from_tx_m >= total_distance_m or frequency_mhz <= 0:
        return 0.0

    wavelength_m = SPEED_OF_LIGHT / (frequency_mhz * 1e6)
    d1 = distance_from_tx_m
    d2 = total_distance_m - distance_from_tx_m

    return zone * math.sqrt((wavelength_m * d1 * d2) / total_distance_m)


def calculate_elevation_profile(request: ElevationProfileRequest) -> ElevationProfileResponse:
    """Calculate elevation profile between two points with LOS analysis."""
    total_distance = _haversine_distance(request.from_lat, request.from_lon, request.to_lat, request.to_lon)
    bearing_deg = _bearing(request.from_lat, request.from_lon, request.to_lat, request.to_lon)

    profile: list[ElevationProfilePoint] = []
    valid_count = 0

    for i in range(request.sample_count):
        fraction = i / (request.sample_count - 1) if request.sample_count > 1 else 0
        lat, lon = _interpolate_point(request.from_lat, request.from_lon, request.to_lat, request.to_lon, fraction)
        distance_m = total_distance * fraction

        # Get elevation
        elev_data = dem_service.get_elevation(lat, lon)
        ground_elev = elev_data.get("elevation_m")
        available = elev_data.get("available", False) and not elev_data.get("void", False)

        if ground_elev is not None:
            valid_count += 1

        # Calculate LOS height at this point (straight line between antennas)
        (request.from_antenna_height_m + (ground_elev or 0)) if i == 0 and ground_elev else None
        los_height = None

        if i == 0:
            from_elev = ground_elev
        elif i == request.sample_count - 1:
            to_elev = ground_elev

        # Calculate Fresnel radius
        fresnel_radius = None
        if request.frequency_mhz and 0 < fraction < 1:
            fresnel_radius = _fresnel_radius(distance_m, total_distance, request.frequency_mhz)

        profile.append(
            ElevationProfilePoint(
                distance_m=distance_m,
                latitude=lat,
                longitude=lon,
                ground_elevation_m=ground_elev,
                los_height_m=los_height,
                fresnel_radius_m=fresnel_radius,
                available=available,
            )
        )

    # Get start and end elevations
    from_elev = profile[0].ground_elevation_m if profile else None
    to_elev = profile[-1].ground_elevation_m if profile else None

    # Calculate total heights
    from_total_height = (from_elev + request.from_antenna_height_m) if from_elev is not None else None
    to_total_height = (to_elev + request.to_antenna_height_m) if to_elev is not None else None

    # Calculate LOS height for each point and check clearance
    has_clear_los = None
    max_obstruction = None
    obstruction_distance = None
    min_fresnel_clearance = None

    if from_total_height is not None and to_total_height is not None:
        has_clear_los = True
        for i, point in enumerate(profile):
            if point.ground_elevation_m is None:
                continue

            fraction = i / (len(profile) - 1) if len(profile) > 1 else 0
            # LOS height at this point (linear interpolation between antenna heights)
            los_height = from_total_height + fraction * (to_total_height - from_total_height)
            profile[i].los_height_m = los_height

            clearance = los_height - point.ground_elevation_m

            # Check Fresnel zone clearance
            if point.fresnel_radius_m and point.fresnel_radius_m > 0:
                fresnel_clearance_pct = (clearance / point.fresnel_radius_m) * 100
                if min_fresnel_clearance is None or fresnel_clearance_pct < min_fresnel_clearance:
                    min_fresnel_clearance = fresnel_clearance_pct

            if clearance < 0:
                has_clear_los = False
                if max_obstruction is None or abs(clearance) > max_obstruction:
                    max_obstruction = abs(clearance)
                    obstruction_distance = point.distance_m

    # Calculate free space path loss
    fspl = None
    if request.frequency_mhz and total_distance > 0:
        fspl = _free_space_path_loss(total_distance, request.frequency_mhz)

    data_coverage = (valid_count / request.sample_count * 100) if request.sample_count > 0 else 0

    return ElevationProfileResponse(
        from_lat=request.from_lat,
        from_lon=request.from_lon,
        to_lat=request.to_lat,
        to_lon=request.to_lon,
        total_distance_m=total_distance,
        bearing_deg=bearing_deg,
        from_elevation_m=from_elev,
        to_elevation_m=to_elev,
        from_total_height_m=from_total_height,
        to_total_height_m=to_total_height,
        has_clear_los=has_clear_los,
        fresnel_clearance_pct=min_fresnel_clearance,
        max_obstruction_m=max_obstruction,
        obstruction_distance_m=obstruction_distance,
        free_space_loss_db=fspl,
        profile=profile,
        sample_count=request.sample_count,
        data_coverage_pct=data_coverage,
    )


class WirelessSurveyService:
    @staticmethod
    def build_form_context(
        db: Session,
        survey: WirelessSiteSurvey | None,
        initial_lat: float | None,
        initial_lon: float | None,
        subscriber_id: str | None,
    ) -> dict:
        projects = projects_service.projects.list_for_site_surveys(db)
        suggested_name = None
        customer_label = None
        person_uuid = coerce_uuid(subscriber_id) if subscriber_id else None
        if person_uuid:
            person = db.get(Person, person_uuid)
            if person:
                name = f"{person.first_name or ''} {person.last_name or ''}".strip() or "Unknown Customer"
                customer_label = name
                suggested_name = f"{name} Site Survey"
        return {
            "survey": survey,
            "projects": projects,
            "initial_lat": initial_lat,
            "initial_lon": initial_lon,
            "suggested_name": suggested_name,
            "subscriber_id": str(person_uuid) if person_uuid else None,
            "customer_label": customer_label,
        }

    @staticmethod
    def create_from_form(
        db: Session,
        name: str,
        description: str | None,
        frequency_mhz: float | None,
        default_antenna_height_m: float | None,
        default_tx_power_dbm: float | None,
        project_id: str | None,
        subscriber_id: str | None,
        actor_id: str | None,
    ) -> WirelessSiteSurvey:
        payload = WirelessSiteSurveyCreate(
            name=name,
            description=description,
            frequency_mhz=frequency_mhz,
            default_antenna_height_m=default_antenna_height_m or 10.0,
            default_tx_power_dbm=default_tx_power_dbm or 20.0,
            project_id=coerce_uuid(project_id) if project_id else None,
            metadata_={"subscriber_id": subscriber_id} if subscriber_id else None,
        )
        user_uuid = coerce_uuid(actor_id) if actor_id else None
        return WirelessSurveyService.create(db, payload, user_uuid)

    @staticmethod
    def build_post_create_redirect(
        survey_id: str | uuid.UUID,
        initial_lat: float | None,
        initial_lon: float | None,
    ) -> str:
        if initial_lat is not None and initial_lon is not None:
            return f"/admin/network/site-survey/{survey_id}?lat={initial_lat:.6f}&lon={initial_lon:.6f}"
        return f"/admin/network/site-survey/{survey_id}"

    @staticmethod
    def build_detail_context(db: Session, survey_id: str | uuid.UUID) -> dict:
        survey = WirelessSurveyService.get_detail(db, survey_id)
        points = sorted(survey.points, key=lambda p: p.sort_order)

        features = []
        for point in points:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [point.longitude, point.latitude],
                    },
                    "properties": {
                        "id": str(point.id),
                        "name": point.name,
                        "point_type": point.point_type.value,
                        "ground_elevation_m": point.ground_elevation_m,
                        "antenna_height_m": point.antenna_height_m,
                        "total_height_m": point.total_height_m,
                    },
                }
            )

        for los_path in survey.los_paths:
            from_point = next((p for p in points if p.id == los_path.from_point_id), None)
            to_point = next((p for p in points if p.id == los_path.to_point_id), None)
            if from_point and to_point:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [from_point.longitude, from_point.latitude],
                                [to_point.longitude, to_point.latitude],
                            ],
                        },
                        "properties": {
                            "id": str(los_path.id),
                            "type": "los_path",
                            "has_clear_los": los_path.has_clear_los,
                            "distance_m": los_path.distance_m,
                            "from_point_name": from_point.name,
                            "to_point_name": to_point.name,
                        },
                    }
                )

        map_data = {"type": "FeatureCollection", "features": features}

        return {
            "survey": survey,
            "points": points,
            "los_paths": survey.los_paths,
            "map_data": map_data,
            "point_types": [pt.value for pt in SurveyPointType],
        }

    @staticmethod
    def create(db: Session, payload: WirelessSiteSurveyCreate, user_id: uuid.UUID | None = None) -> WirelessSiteSurvey:
        survey = WirelessSiteSurvey(
            name=payload.name,
            description=payload.description,
            status=payload.status,
            frequency_mhz=payload.frequency_mhz,
            default_antenna_height_m=payload.default_antenna_height_m,
            default_tx_power_dbm=payload.default_tx_power_dbm,
            notes=payload.notes,
            metadata_=payload.metadata_,
            project_id=payload.project_id,
            created_by_id=user_id,
        )
        db.add(survey)
        db.commit()
        db.refresh(survey)
        return survey

    @staticmethod
    def get(db: Session, survey_id: str | uuid.UUID) -> WirelessSiteSurvey:
        survey = db.query(WirelessSiteSurvey).filter(WirelessSiteSurvey.id == survey_id).first()
        if not survey:
            raise HTTPException(status_code=404, detail="Survey not found")
        return survey

    @staticmethod
    def get_detail(db: Session, survey_id: str | uuid.UUID) -> WirelessSiteSurvey:
        survey = (
            db.query(WirelessSiteSurvey)
            .options(
                joinedload(WirelessSiteSurvey.points),
                joinedload(WirelessSiteSurvey.los_paths),
            )
            .filter(WirelessSiteSurvey.id == survey_id)
            .first()
        )
        if not survey:
            raise HTTPException(status_code=404, detail="Survey not found")
        return survey

    @staticmethod
    def list(
        db: Session,
        status: str | None = None,
        project_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[WirelessSiteSurvey]:
        query = db.query(WirelessSiteSurvey)
        if status:
            query = query.filter(WirelessSiteSurvey.status == status)
        if project_id:
            query = query.filter(WirelessSiteSurvey.project_id == project_id)
        return query.order_by(WirelessSiteSurvey.created_at.desc()).offset(offset).limit(limit).all()

    @staticmethod
    def update(db: Session, survey_id: str | uuid.UUID, payload: WirelessSiteSurveyUpdate) -> WirelessSiteSurvey:
        survey = WirelessSurveyService.get(db, survey_id)
        update_data = payload.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(survey, key, value)
        db.commit()
        db.refresh(survey)
        return survey

    @staticmethod
    def delete(db: Session, survey_id: str | uuid.UUID) -> None:
        survey = WirelessSurveyService.get(db, survey_id)
        db.delete(survey)
        db.commit()

    @staticmethod
    def _update_bounds(db: Session, survey: WirelessSiteSurvey) -> None:
        """Update survey bounds based on points."""
        result = (
            db.query(
                func.min(SurveyPoint.latitude),
                func.max(SurveyPoint.latitude),
                func.min(SurveyPoint.longitude),
                func.max(SurveyPoint.longitude),
            )
            .filter(SurveyPoint.survey_id == survey.id)
            .first()
        )

        if result and result[0] is not None:
            survey.min_latitude = result[0]
            survey.max_latitude = result[1]
            survey.min_longitude = result[2]
            survey.max_longitude = result[3]
            db.commit()


class SurveyPointService:
    @staticmethod
    def create(db: Session, survey_id: str | uuid.UUID, payload: SurveyPointCreate) -> SurveyPoint:
        survey = WirelessSurveyService.get(db, survey_id)

        # Get elevation data
        elev_data = dem_service.get_elevation(payload.latitude, payload.longitude)

        point = SurveyPoint(
            survey_id=survey.id,
            name=payload.name,
            point_type=payload.point_type,
            latitude=payload.latitude,
            longitude=payload.longitude,
            geom=ST_SetSRID(ST_MakePoint(payload.longitude, payload.latitude), 4326),
            ground_elevation_m=elev_data.get("elevation_m"),
            elevation_source=elev_data.get("source"),
            elevation_tile=elev_data.get("tile"),
            antenna_height_m=payload.antenna_height_m,
            antenna_gain_dbi=payload.antenna_gain_dbi,
            tx_power_dbm=payload.tx_power_dbm,
            notes=payload.notes,
            metadata_=payload.metadata_,
            sort_order=payload.sort_order,
        )
        db.add(point)
        db.commit()
        db.refresh(point)

        # Update survey bounds
        WirelessSurveyService._update_bounds(db, survey)

        return point

    @staticmethod
    def get(db: Session, point_id: str | uuid.UUID) -> SurveyPoint:
        point = db.query(SurveyPoint).filter(SurveyPoint.id == point_id).first()
        if not point:
            raise HTTPException(status_code=404, detail="Survey point not found")
        return point

    @staticmethod
    def list(db: Session, survey_id: str | uuid.UUID) -> list[SurveyPoint]:
        return (
            db.query(SurveyPoint)
            .filter(SurveyPoint.survey_id == survey_id)
            .order_by(SurveyPoint.sort_order, SurveyPoint.created_at)
            .all()
        )

    @staticmethod
    def update(db: Session, point_id: str | uuid.UUID, payload: SurveyPointUpdate) -> SurveyPoint:
        point = SurveyPointService.get(db, point_id)
        update_data = payload.model_dump(exclude_unset=True)

        # If coordinates changed, update elevation
        if "latitude" in update_data or "longitude" in update_data:
            lat = update_data.get("latitude", point.latitude)
            lon = update_data.get("longitude", point.longitude)
            elev_data = dem_service.get_elevation(lat, lon)
            point.ground_elevation_m = elev_data.get("elevation_m")
            point.elevation_source = elev_data.get("source")
            point.elevation_tile = elev_data.get("tile")
            point.geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326)

        for key, value in update_data.items():
            setattr(point, key, value)

        db.commit()
        db.refresh(point)

        # Update survey bounds
        survey = WirelessSurveyService.get(db, point.survey_id)
        WirelessSurveyService._update_bounds(db, survey)

        return point

    @staticmethod
    def delete(db: Session, point_id: str | uuid.UUID) -> None:
        point = SurveyPointService.get(db, point_id)
        survey_id = point.survey_id
        db.delete(point)
        db.commit()

        # Update survey bounds
        survey = db.query(WirelessSiteSurvey).filter(WirelessSiteSurvey.id == survey_id).first()
        if survey:
            WirelessSurveyService._update_bounds(db, survey)

    @staticmethod
    def refresh_elevation(db: Session, point_id: str | uuid.UUID) -> SurveyPoint:
        """Re-fetch elevation data for a point."""
        point = SurveyPointService.get(db, point_id)
        elev_data = dem_service.get_elevation(point.latitude, point.longitude)
        point.ground_elevation_m = elev_data.get("elevation_m")
        point.elevation_source = elev_data.get("source")
        point.elevation_tile = elev_data.get("tile")
        db.commit()
        db.refresh(point)
        return point


class SurveyLosService:
    @staticmethod
    def analyze_path(
        db: Session,
        survey_id: str | uuid.UUID,
        from_point_id: str | uuid.UUID,
        to_point_id: str | uuid.UUID,
        sample_count: int = 100,
    ) -> SurveyLosPath:
        """Analyze LOS path between two survey points."""
        survey = WirelessSurveyService.get(db, survey_id)
        from_point = SurveyPointService.get(db, from_point_id)
        to_point = SurveyPointService.get(db, to_point_id)

        if from_point.survey_id != survey.id or to_point.survey_id != survey.id:
            raise HTTPException(status_code=400, detail="Points must belong to the same survey")

        # Calculate elevation profile
        request = ElevationProfileRequest(
            from_lat=from_point.latitude,
            from_lon=from_point.longitude,
            to_lat=to_point.latitude,
            to_lon=to_point.longitude,
            sample_count=sample_count,
            from_antenna_height_m=from_point.antenna_height_m,
            to_antenna_height_m=to_point.antenna_height_m,
            frequency_mhz=survey.frequency_mhz,
        )
        profile_result = calculate_elevation_profile(request)

        # Check for existing path
        existing = (
            db.query(SurveyLosPath)
            .filter(
                SurveyLosPath.survey_id == survey.id,
                SurveyLosPath.from_point_id == from_point.id,
                SurveyLosPath.to_point_id == to_point.id,
            )
            .first()
        )

        if existing:
            los_path = existing
        else:
            los_path = SurveyLosPath(
                survey_id=survey.id,
                from_point_id=from_point.id,
                to_point_id=to_point.id,
            )
            db.add(los_path)

        # Update with results
        los_path.distance_m = profile_result.total_distance_m
        los_path.bearing_deg = profile_result.bearing_deg
        los_path.has_clear_los = profile_result.has_clear_los
        los_path.fresnel_clearance_pct = profile_result.fresnel_clearance_pct
        los_path.max_obstruction_m = profile_result.max_obstruction_m
        los_path.obstruction_distance_m = profile_result.obstruction_distance_m
        los_path.elevation_profile = [p.model_dump() for p in profile_result.profile]
        los_path.free_space_loss_db = profile_result.free_space_loss_db
        los_path.sample_count = profile_result.sample_count
        los_path.analysis_timestamp = datetime.now(UTC)

        # Calculate estimated RSSI if we have TX power
        if from_point.tx_power_dbm is not None and los_path.free_space_loss_db is not None:
            gain = (from_point.antenna_gain_dbi or 0) + (to_point.antenna_gain_dbi or 0)
            los_path.estimated_rssi_dbm = from_point.tx_power_dbm + gain - los_path.free_space_loss_db

        db.commit()
        db.refresh(los_path)
        return los_path

    @staticmethod
    def get(db: Session, path_id: str | uuid.UUID) -> SurveyLosPath:
        path = db.query(SurveyLosPath).filter(SurveyLosPath.id == path_id).first()
        if not path:
            raise HTTPException(status_code=404, detail="LOS path not found")
        return path

    @staticmethod
    def list(db: Session, survey_id: str | uuid.UUID) -> list[SurveyLosPath]:
        return db.query(SurveyLosPath).filter(SurveyLosPath.survey_id == survey_id).all()

    @staticmethod
    def delete(db: Session, path_id: str | uuid.UUID) -> None:
        path = SurveyLosService.get(db, path_id)
        db.delete(path)
        db.commit()


# Service instances
wireless_surveys = WirelessSurveyService()
survey_points = SurveyPointService()
survey_los = SurveyLosService()
