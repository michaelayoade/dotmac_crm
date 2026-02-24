import argparse
import itertools
import json
import math
import re
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from sqlalchemy import func

from app.db import SessionLocal
from app.models.gis import ServiceBuilding
from app.models.network import (
    FdhCabinet,
    FiberAccessPoint,
    FiberCableType,
    FiberSegment,
    FiberSegmentType,
    FiberSplice,
    FiberSpliceClosure,
    FiberSpliceTray,
    OLTDevice,
    PonPortSplitterLink,
    Splitter,
    SplitterPort,
)
from app.models.vendor import AsBuiltRoute
from app.models.wireless_mast import WirelessMast

load_dotenv: Callable[..., bool] | None
try:
    from dotenv import load_dotenv as _load_dotenv
except ImportError:  # pragma: no cover - optional dependency for local env files
    load_dotenv = None
else:
    load_dotenv = _load_dotenv

KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}


@dataclass
class PlacemarkData:
    name: str
    properties: dict[str, str | None]
    geometry_type: str
    coordinates: list[tuple[float, float]]


# ---------------------------------------------------------------------------
# Merged-KMZ helpers: classification, geo-filter, deduplication
# ---------------------------------------------------------------------------

# Abuja bounding box
_ABUJA_LAT_MIN, _ABUJA_LAT_MAX = 8.8, 9.2
_ABUJA_LON_MIN, _ABUJA_LON_MAX = 7.1, 7.6

# Name patterns for classification (compiled once)
_RE_CABINET = re.compile(r"cabinet|fdh|wall\s*cabinet", re.IGNORECASE)
_RE_CLOSURE = re.compile(r"closure|joint|(?<!\w)icc(?!\w)|handhole", re.IGNORECASE)
_RE_ACCESS_POINT = re.compile(r"pick\s*point|pickpoint|picking\s*point", re.IGNORECASE)
_RE_OLT_BTS = re.compile(r"(?<!\w)bts(?!\w)|(?<!\w)gpon(?!\w)|(?<!\w)olt(?!\w)|nigcomsat", re.IGNORECASE)
_RE_SKIP_INFRA = re.compile(r"(?<!\w)drainage(?!\w)|(?<!\w)trenching(?!\w)|(?<!\w)manhole(?!\w)|(?<!\w)duct\s", re.IGNORECASE)
_RE_JUNK_NAME = re.compile(r"^(untitled\s*(placemark|path)?|path\s*measure|line\s*measure|sightseeing)$", re.IGNORECASE)

# Entity type constants
ENTITY_CABINET = "fdh_cabinet"
ENTITY_CLOSURE = "splice_closure"
ENTITY_ACCESS_POINT = "access_point"
ENTITY_OLT = "olt_device"
ENTITY_SEGMENT = "fiber_segment"
ENTITY_BUILDING = "building"
ENTITY_SKIP = "skip"


def _is_in_abuja(coords: list[tuple[float, float]]) -> bool:
    """Check if first coordinate falls within the Abuja bounding box."""
    if not coords:
        return False
    lon, lat = coords[0]
    return _ABUJA_LAT_MIN <= lat <= _ABUJA_LAT_MAX and _ABUJA_LON_MIN <= lon <= _ABUJA_LON_MAX


def _classify_placemark(name: str, geom_type: str) -> str:
    """Classify a placemark into an entity type based on name patterns and geometry.

    Returns one of the ENTITY_* constants or ENTITY_SKIP.
    """
    n = name.strip()
    nl = n.lower()

    # Junk / empty names
    if not n or _RE_JUNK_NAME.match(nl):
        if geom_type == "LineString" and nl in ("path measure", "line measure"):
            return ENTITY_SEGMENT  # measurement traces are still real fiber routes
        return ENTITY_SKIP

    # Infrastructure: manholes → closures, ducts/trenching/drainage → segments
    if _RE_SKIP_INFRA.search(nl):
        if "manhole" in nl:
            return ENTITY_CLOSURE
        # Ducts, trenching, drainage are civil-works routes
        return ENTITY_SEGMENT if geom_type == "LineString" else ENTITY_BUILDING

    # Cabinet patterns (highest priority for points)
    if _RE_CABINET.search(nl):
        return ENTITY_SEGMENT if geom_type == "LineString" else ENTITY_CABINET

    # Closure patterns
    if _RE_CLOSURE.search(nl):
        return ENTITY_SEGMENT if geom_type == "LineString" else ENTITY_CLOSURE

    # Access point patterns
    if _RE_ACCESS_POINT.search(nl):
        return ENTITY_SEGMENT if geom_type == "LineString" else ENTITY_ACCESS_POINT

    # OLT / BTS patterns
    if _RE_OLT_BTS.search(nl):
        return ENTITY_SEGMENT if geom_type == "LineString" else ENTITY_OLT

    # By geometry type
    if geom_type == "LineString":
        return ENTITY_SEGMENT
    if geom_type == "Polygon":
        return ENTITY_BUILDING

    # Remaining points are customer/subscriber premises
    return ENTITY_BUILDING


def _dedup_key(name: str, geom_type: str, coords: list[tuple[float, float]]) -> tuple:
    """Create a deduplication key from name + geometry type + start/end coordinates."""
    start = coords[0]
    end = coords[-1] if len(coords) > 1 else coords[0]
    return (
        name.lower().strip(),
        geom_type,
        round(start[0], 5),
        round(start[1], 5),
        round(end[0], 5),
        round(end[1], 5),
    )


def _make_segment_name(name: str, coords: list[tuple[float, float]], counter: dict[str, int]) -> str:
    """Generate a unique name for segments with generic names like 'Path Measure'."""
    nl = name.lower().strip()
    _generic_segment_names = (
        "path measure", "line measure", "route", "proposed route", "untitled path",
        "new route", "", "trenching", "drainage", "duct route", "new duct route",
        "trenching route", "new trenching", "trenching part", "interlock and trenching",
        "interlock & trenching",
    )
    if nl not in _generic_segment_names:
        return name  # already has a meaningful name

    # Generate from start/end coordinates
    start_lon, start_lat = coords[0]
    end_lon, end_lat = coords[-1] if len(coords) > 1 else coords[0]
    base = f"PM-{start_lat:.4f}-{start_lon:.4f}-{end_lat:.4f}-{end_lon:.4f}"

    # Ensure uniqueness
    if base in counter:
        counter[base] += 1
        return f"{base}-{counter[base]}"
    counter[base] = 0
    return base


# Generic names for point entities that need location disambiguation
_GENERIC_CABINET_NAMES = {"cabinet", "wall cabinet", "fdh"}
_GENERIC_CLOSURE_NAMES = {"closure", "joint", "icc", "handhole", "proposed closure", "manhole", "manhole closure"}
_GENERIC_AP_NAMES = {"pick point", "pick_point", "pickpoint", "picking point"}
_GENERIC_OLT_NAMES = {"bts", "proposed bts", "olt"}
_GENERIC_BUILDING_NAMES = {"client", "building", "trenching", "drainage", "duct route", "new duct route"}


def _make_unique_point_name(name: str, lon: float, lat: float, generic_set: set[str], counter: dict[str, int]) -> str:
    """Make a point entity name unique by appending coordinates for generic names."""
    nl = name.lower().strip()
    if nl not in generic_set:
        return name
    base = f"{name}-{lat:.4f}-{lon:.4f}"
    if base in counter:
        counter[base] += 1
        return f"{base}-{counter[base]}"
    counter[base] = 0
    return base


def parse_args():
    parser = argparse.ArgumentParser(description="Import KMZ/KML data into fiber plant tables.")
    # Per-type KMZ inputs (original mode)
    parser.add_argument("--paths-kmz", action="append", default=[], help="KMZ with fiber paths (LineString).")
    parser.add_argument("--cabinet-kmz", action="append", default=[], help="KMZ with cabinets (Polygon/Point).")
    parser.add_argument("--splice-kmz", action="append", default=[], help="KMZ with splice closures (Polygon/Point).")
    parser.add_argument("--access-point-kmz", action="append", default=[], help="KMZ with fiber access points (Polygon/Point).")
    parser.add_argument("--mast-kmz", action="append", default=[], help="KMZ with wireless masts/poles (Point).")
    parser.add_argument("--building-kmz", action="append", default=[], help="KMZ with service buildings (Polygon/Point).")
    # Merged KMZ input (new mode)
    parser.add_argument(
        "--merged-kmz",
        type=str,
        default=None,
        help="Path to a merged KMZ containing all entity types. Placemarks are classified by name patterns.",
    )
    parser.add_argument("--segment-type", default="distribution", choices=[t.value for t in FiberSegmentType])
    parser.add_argument("--cable-type", default=None, choices=[t.value for t in FiberCableType])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--upsert", action="store_true", help="Update existing rows instead of skipping.")
    parser.add_argument(
        "--purge",
        action="store_true",
        help="Delete existing FiberSegment/FdhCabinet/FiberSpliceClosure rows before import.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit placemarks per file.")
    return parser.parse_args()


def _read_kmz_kml(path: Path) -> ET.Element:
    with zipfile.ZipFile(path) as kmz:
        kml_name = next((n for n in kmz.namelist() if n.lower().endswith(".kml")), None)
        if not kml_name:
            raise ValueError(f"No KML found inside {path}")
        return ET.fromstring(kmz.read(kml_name))


def _collect_properties(placemark: ET.Element) -> dict[str, str | None]:
    props: dict[str, str | None] = {}
    for simple in placemark.findall(".//kml:SimpleData", KML_NS):
        key = simple.attrib.get("name")
        if not key:
            continue
        value = (simple.text or "").strip()
        props[key] = value or None
    for data_el in placemark.findall(".//kml:Data", KML_NS):
        key = data_el.attrib.get("name")
        if not key:
            continue
        value = data_el.findtext("kml:value", default="", namespaces=KML_NS).strip()
        props[key] = value or None
    return props


def _parse_coord_text(text: str) -> list[tuple[float, float]]:
    coords: list[tuple[float, float]] = []
    for token in text.strip().split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        coords.append((lon, lat))
    return coords


def _extract_geometry(placemark: ET.Element) -> tuple[str, list[tuple[float, float]]] | None:
    point = placemark.find(".//kml:Point", KML_NS)
    if point is not None:
        text = point.findtext("kml:coordinates", default="", namespaces=KML_NS)
        coords = _parse_coord_text(text)
        return ("Point", coords)
    line = placemark.find(".//kml:LineString", KML_NS)
    if line is not None:
        text = line.findtext("kml:coordinates", default="", namespaces=KML_NS)
        coords = _parse_coord_text(text)
        return ("LineString", coords)
    polygon = placemark.find(".//kml:Polygon", KML_NS)
    if polygon is not None:
        text = polygon.findtext(".//kml:coordinates", default="", namespaces=KML_NS)
        coords = _parse_coord_text(text)
        return ("Polygon", coords)
    return None


def _iter_placemarks(root: ET.Element, limit: int | None = None) -> Iterable[PlacemarkData]:
    count = 0
    for placemark in root.findall(".//kml:Placemark", KML_NS):
        name = placemark.findtext("kml:name", default="", namespaces=KML_NS).strip()
        geom = _extract_geometry(placemark)
        if geom is None:
            continue
        geometry_type, coords = geom
        if not coords:
            continue
        properties = _collect_properties(placemark)
        yield PlacemarkData(name=name, properties=properties, geometry_type=geometry_type, coordinates=coords)
        count += 1
        if limit is not None and count >= limit:
            break


def _polygon_centroid(coords: list[tuple[float, float]]) -> tuple[float, float]:
    if coords[0] != coords[-1]:
        coords = [*coords, coords[0]]
    area = 0.0
    cx = 0.0
    cy = 0.0
    for (x0, y0), (x1, y1) in itertools.pairwise(coords):
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(area) < 1e-12:
        avg_lon = sum(x for x, _ in coords) / len(coords)
        avg_lat = sum(y for _, y in coords) / len(coords)
        return avg_lon, avg_lat
    area *= 0.5
    cx /= (6.0 * area)
    cy /= (6.0 * area)
    return cx, cy


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    rad = math.radians
    dlat = rad(lat2 - lat1)
    dlon = rad(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rad(lat1)) * math.cos(rad(lat2)) * math.sin(dlon / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(a))


def _line_length_m(coords: list[tuple[float, float]]) -> float:
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in itertools.pairwise(coords):
        total += _haversine_m(lon1, lat1, lon2, lat2)
    return total


def _geojson_to_geom(geojson: dict) -> object:
    geojson_str = json.dumps(geojson)
    return func.ST_SetSRID(func.ST_GeomFromGeoJSON(geojson_str), 4326)


def _point_geom(lon: float, lat: float) -> object:
    return func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)


def _make_notes(properties: dict[str, str | None]) -> str | None:
    if not properties:
        return None
    return json.dumps(properties, ensure_ascii=True, sort_keys=True)


def import_segments(db, paths: list[Path], segment_type: str, cable_type: str | None, upsert: bool, limit: int | None):
    created = 0
    updated = 0
    skipped = 0
    for path in paths:
        root = _read_kmz_kml(path)
        for placemark in _iter_placemarks(root, limit=limit):
            if placemark.geometry_type != "LineString":
                continue
            name = placemark.name or placemark.properties.get("spanid") or "unnamed-segment"
            existing = db.query(FiberSegment).filter(FiberSegment.name == name).first()
            coords = placemark.coordinates
            geojson = {"type": "LineString", "coordinates": coords}
            length_m = _line_length_m(coords) if len(coords) > 1 else None
            notes = _make_notes(placemark.properties)
            if existing:
                if not upsert:
                    skipped += 1
                    continue
                existing.segment_type = FiberSegmentType(segment_type)
                existing.cable_type = FiberCableType(cable_type) if cable_type else None
                existing.route_geom = _geojson_to_geom(geojson)
                existing.length_m = length_m
                existing.notes = notes
                updated += 1
            else:
                segment = FiberSegment(
                    name=name,
                    segment_type=FiberSegmentType(segment_type),
                    cable_type=FiberCableType(cable_type) if cable_type else None,
                    route_geom=_geojson_to_geom(geojson),
                    length_m=length_m,
                    notes=notes,
                )
                db.add(segment)
                created += 1
    return created, updated, skipped


def _extract_point(placemark: PlacemarkData) -> tuple[float, float] | None:
    if placemark.geometry_type == "Point":
        return placemark.coordinates[0]
    if placemark.geometry_type == "Polygon":
        return _polygon_centroid(placemark.coordinates)
    return None


def import_cabinets(db, paths: list[Path], upsert: bool, limit: int | None):
    created = 0
    updated = 0
    skipped = 0
    for path in paths:
        root = _read_kmz_kml(path)
        for placemark in _iter_placemarks(root, limit=limit):
            point = _extract_point(placemark)
            if not point:
                continue
            lon, lat = point
            name = placemark.properties.get("name") or placemark.name or "unnamed-cabinet"
            code = placemark.properties.get("fibermngrid")
            existing = None
            if code:
                existing = db.query(FdhCabinet).filter(FdhCabinet.code == code).first()
            if not existing:
                existing = db.query(FdhCabinet).filter(FdhCabinet.name == name).first()
            notes = _make_notes(placemark.properties)
            if existing:
                if not upsert:
                    skipped += 1
                    continue
                existing.name = name
                existing.code = code
                existing.latitude = lat
                existing.longitude = lon
                existing.geom = _point_geom(lon, lat)
                existing.notes = notes
                updated += 1
            else:
                cabinet = FdhCabinet(
                    name=name,
                    code=code,
                    latitude=lat,
                    longitude=lon,
                    geom=_point_geom(lon, lat),
                    notes=notes,
                )
                db.add(cabinet)
                created += 1
    return created, updated, skipped


def import_splice_closures(db, paths: list[Path], upsert: bool, limit: int | None):
    created = 0
    updated = 0
    skipped = 0
    for path in paths:
        root = _read_kmz_kml(path)
        for placemark in _iter_placemarks(root, limit=limit):
            point = _extract_point(placemark)
            if not point:
                continue
            lon, lat = point
            name = placemark.properties.get("name") or placemark.name or "unnamed-closure"
            existing = db.query(FiberSpliceClosure).filter(FiberSpliceClosure.name == name).first()
            notes = _make_notes(placemark.properties)
            if existing:
                if not upsert:
                    skipped += 1
                    continue
                existing.name = name
                existing.latitude = lat
                existing.longitude = lon
                existing.geom = _point_geom(lon, lat)
                existing.notes = notes
                updated += 1
            else:
                closure = FiberSpliceClosure(
                    name=name,
                    latitude=lat,
                    longitude=lon,
                    geom=_point_geom(lon, lat),
                    notes=notes,
                )
                db.add(closure)
                created += 1
    return created, updated, skipped


def import_access_points(db, paths: list[Path], upsert: bool, limit: int | None):
    """Import fiber access points from KMZ files."""
    created = 0
    updated = 0
    skipped = 0
    for path in paths:
        root = _read_kmz_kml(path)
        for placemark in _iter_placemarks(root, limit=limit):
            point = _extract_point(placemark)
            if not point:
                continue
            lon, lat = point
            name = placemark.properties.get("Name") or placemark.name or "unnamed-ap"
            code = placemark.properties.get("access_pointid")
            existing = None
            if code:
                existing = db.query(FiberAccessPoint).filter(FiberAccessPoint.code == code).first()
            if not existing:
                existing = db.query(FiberAccessPoint).filter(FiberAccessPoint.name == name).first()
            notes = _make_notes(placemark.properties)
            ap_type = placemark.properties.get("Type")
            placement = placemark.properties.get("Placement")
            street = placemark.properties.get("Street")
            city = placemark.properties.get("City")
            county = placemark.properties.get("County")
            state = placemark.properties.get("State")
            if existing:
                if not upsert:
                    skipped += 1
                    continue
                existing.name = name
                existing.code = code
                existing.access_point_type = ap_type
                existing.placement = placement
                existing.latitude = lat
                existing.longitude = lon
                existing.geom = _point_geom(lon, lat)
                existing.street = street
                existing.city = city
                existing.county = county
                existing.state = state
                existing.notes = notes
                updated += 1
            else:
                ap = FiberAccessPoint(
                    name=name,
                    code=code,
                    access_point_type=ap_type,
                    placement=placement,
                    latitude=lat,
                    longitude=lon,
                    geom=_point_geom(lon, lat),
                    street=street,
                    city=city,
                    county=county,
                    state=state,
                    notes=notes,
                )
                db.add(ap)
                created += 1
    return created, updated, skipped


def import_wireless_masts(db, paths: list[Path], upsert: bool, limit: int | None):
    """Import wireless masts/poles from KMZ files."""
    created = 0
    updated = 0
    skipped = 0
    for path in paths:
        root = _read_kmz_kml(path)
        for placemark in _iter_placemarks(root, limit=limit):
            point = _extract_point(placemark)
            if not point:
                continue
            lon, lat = point
            name = placemark.properties.get("name") or placemark.name or "unnamed-mast"
            # Try to find existing by name
            existing = db.query(WirelessMast).filter(WirelessMast.name == name).first()
            notes = _make_notes(placemark.properties)
            structure_type = placemark.properties.get("poletypeid")
            status = placemark.properties.get("stage") or "active"
            if existing:
                if not upsert:
                    skipped += 1
                    continue
                existing.name = name
                existing.latitude = lat
                existing.longitude = lon
                existing.geom = _point_geom(lon, lat)
                existing.structure_type = structure_type
                existing.status = status
                existing.notes = notes
                updated += 1
            else:
                mast = WirelessMast(
                    name=name,
                    latitude=lat,
                    longitude=lon,
                    geom=_point_geom(lon, lat),
                    structure_type=structure_type,
                    status=status,
                    notes=notes,
                )
                db.add(mast)
                created += 1
    return created, updated, skipped


def import_buildings(db, paths: list[Path], upsert: bool, limit: int | None):
    """Import service buildings from KMZ files."""
    created = 0
    updated = 0
    skipped = 0
    for path in paths:
        root = _read_kmz_kml(path)
        for placemark in _iter_placemarks(root, limit=limit):
            point = _extract_point(placemark)
            if not point:
                continue
            lon, lat = point
            name = placemark.properties.get("Name") or placemark.name or "unnamed-building"
            code = placemark.properties.get("buildingid")
            existing = None
            if code:
                existing = db.query(ServiceBuilding).filter(ServiceBuilding.code == code).first()
            if not existing:
                existing = db.query(ServiceBuilding).filter(ServiceBuilding.name == name).first()
            notes = _make_notes(placemark.properties)
            clli = placemark.properties.get("CLLI")
            street = placemark.properties.get("Street")
            city = placemark.properties.get("City")
            state = placemark.properties.get("State")
            zip_code = placemark.properties.get("ZIP")
            work_order = placemark.properties.get("Work Order")
            # Handle polygon geometry for boundary
            boundary_geom = None
            if placemark.geometry_type == "Polygon" and len(placemark.coordinates) >= 3:
                coords = placemark.coordinates
                if coords[0] != coords[-1]:
                    coords = [*coords, coords[0]]
                geojson = {"type": "Polygon", "coordinates": [coords]}
                boundary_geom = _geojson_to_geom(geojson)
            if existing:
                if not upsert:
                    skipped += 1
                    continue
                existing.name = name
                existing.code = code
                existing.clli = clli
                existing.latitude = lat
                existing.longitude = lon
                existing.geom = _point_geom(lon, lat)
                if boundary_geom is not None:
                    existing.boundary_geom = boundary_geom
                existing.street = street
                existing.city = city
                existing.state = state
                existing.zip_code = zip_code
                existing.work_order = work_order
                existing.notes = notes
                updated += 1
            else:
                building = ServiceBuilding(
                    name=name,
                    code=code,
                    clli=clli,
                    latitude=lat,
                    longitude=lon,
                    geom=_point_geom(lon, lat),
                    boundary_geom=boundary_geom,
                    street=street,
                    city=city,
                    state=state,
                    zip_code=zip_code,
                    work_order=work_order,
                    notes=notes,
                )
                db.add(building)
                created += 1
    return created, updated, skipped


# ---------------------------------------------------------------------------
# Batch import functions: accept list[PlacemarkData] directly (for merged mode)
# ---------------------------------------------------------------------------


def _import_segments_batch(
    db, placemarks: list[PlacemarkData], segment_type: str, cable_type: str | None, upsert: bool
) -> tuple[int, int, int]:
    """Import fiber segments from pre-classified placemarks."""
    created = updated = skipped = 0
    seg_name_counter: dict[str, int] = {}
    # Track names used in this batch to avoid unique-constraint collisions
    # (unflushed inserts are invisible to queries within the same session)
    batch_names: set[str] = set()
    for pm in placemarks:
        if pm.geometry_type != "LineString":
            continue
        name = _make_segment_name(
            pm.name or pm.properties.get("spanid") or "unnamed-segment",
            pm.coordinates,
            seg_name_counter,
        )
        # Ensure unique within batch — append suffix if name was already used
        base_name = name
        suffix = 1
        while name in batch_names:
            name = f"{base_name}-{suffix}"
            suffix += 1
        existing = db.query(FiberSegment).filter(FiberSegment.name == name).first()
        coords = pm.coordinates
        geojson = {"type": "LineString", "coordinates": coords}
        length_m = _line_length_m(coords) if len(coords) > 1 else None
        notes = _make_notes(pm.properties)
        if existing:
            if not upsert:
                skipped += 1
                continue
            existing.segment_type = FiberSegmentType(segment_type)
            existing.cable_type = FiberCableType(cable_type) if cable_type else None
            existing.route_geom = _geojson_to_geom(geojson)
            existing.length_m = length_m
            existing.notes = notes
            updated += 1
        else:
            segment = FiberSegment(
                name=name,
                segment_type=FiberSegmentType(segment_type),
                cable_type=FiberCableType(cable_type) if cable_type else None,
                route_geom=_geojson_to_geom(geojson),
                length_m=length_m,
                notes=notes,
            )
            db.add(segment)
            batch_names.add(name)
            created += 1
    return created, updated, skipped


def _import_cabinets_batch(db, placemarks: list[PlacemarkData], upsert: bool) -> tuple[int, int, int]:
    """Import FDH cabinets from pre-classified placemarks."""
    created = updated = skipped = 0
    name_counter: dict[str, int] = {}
    batch_names: set[str] = set()
    for pm in placemarks:
        point = _extract_point(pm)
        if not point:
            continue
        lon, lat = point
        name = pm.properties.get("name") or pm.name or "unnamed-cabinet"
        name = _make_unique_point_name(name, lon, lat, _GENERIC_CABINET_NAMES, name_counter)
        code = pm.properties.get("fibermngrid")
        existing = None
        if code:
            existing = db.query(FdhCabinet).filter(FdhCabinet.code == code).first()
        if not existing:
            existing = db.query(FdhCabinet).filter(FdhCabinet.name == name).first()
        notes = _make_notes(pm.properties)
        if existing:
            if not upsert:
                skipped += 1
                continue
            existing.name = name
            existing.code = code
            existing.latitude = lat
            existing.longitude = lon
            existing.geom = _point_geom(lon, lat)
            existing.notes = notes
            updated += 1
        else:
            cabinet = FdhCabinet(
                name=name,
                code=code,
                latitude=lat,
                longitude=lon,
                geom=_point_geom(lon, lat),
                notes=notes,
            )
            db.add(cabinet)
            created += 1
    return created, updated, skipped


def _import_closures_batch(db, placemarks: list[PlacemarkData], upsert: bool) -> tuple[int, int, int]:
    """Import splice closures from pre-classified placemarks."""
    created = updated = skipped = 0
    name_counter: dict[str, int] = {}
    for pm in placemarks:
        point = _extract_point(pm)
        if not point:
            continue
        lon, lat = point
        name = pm.properties.get("name") or pm.name or "unnamed-closure"
        name = _make_unique_point_name(name, lon, lat, _GENERIC_CLOSURE_NAMES, name_counter)
        existing = db.query(FiberSpliceClosure).filter(FiberSpliceClosure.name == name).first()
        notes = _make_notes(pm.properties)
        if existing:
            if not upsert:
                skipped += 1
                continue
            existing.name = name
            existing.latitude = lat
            existing.longitude = lon
            existing.geom = _point_geom(lon, lat)
            existing.notes = notes
            updated += 1
        else:
            closure = FiberSpliceClosure(
                name=name,
                latitude=lat,
                longitude=lon,
                geom=_point_geom(lon, lat),
                notes=notes,
            )
            db.add(closure)
            created += 1
    return created, updated, skipped


def _import_access_points_batch(db, placemarks: list[PlacemarkData], upsert: bool) -> tuple[int, int, int]:
    """Import fiber access points from pre-classified placemarks."""
    created = updated = skipped = 0
    name_counter: dict[str, int] = {}
    for pm in placemarks:
        point = _extract_point(pm)
        if not point:
            continue
        lon, lat = point
        name = pm.properties.get("Name") or pm.name or "unnamed-ap"
        name = _make_unique_point_name(name, lon, lat, _GENERIC_AP_NAMES, name_counter)
        code = pm.properties.get("access_pointid")
        existing = None
        if code:
            existing = db.query(FiberAccessPoint).filter(FiberAccessPoint.code == code).first()
        if not existing:
            existing = db.query(FiberAccessPoint).filter(FiberAccessPoint.name == name).first()
        notes = _make_notes(pm.properties)
        ap_type = pm.properties.get("Type")
        placement = pm.properties.get("Placement")
        street = pm.properties.get("Street")
        city = pm.properties.get("City")
        county = pm.properties.get("County")
        state = pm.properties.get("State")
        if existing:
            if not upsert:
                skipped += 1
                continue
            existing.name = name
            existing.code = code
            existing.access_point_type = ap_type
            existing.placement = placement
            existing.latitude = lat
            existing.longitude = lon
            existing.geom = _point_geom(lon, lat)
            existing.street = street
            existing.city = city
            existing.county = county
            existing.state = state
            existing.notes = notes
            updated += 1
        else:
            ap = FiberAccessPoint(
                name=name,
                code=code,
                access_point_type=ap_type,
                placement=placement,
                latitude=lat,
                longitude=lon,
                geom=_point_geom(lon, lat),
                street=street,
                city=city,
                county=county,
                state=state,
                notes=notes,
            )
            db.add(ap)
            created += 1
    return created, updated, skipped


def _import_olt_devices_batch(db, placemarks: list[PlacemarkData], upsert: bool) -> tuple[int, int, int]:
    """Import OLT/BTS devices from pre-classified placemarks."""
    created = updated = skipped = 0
    name_counter: dict[str, int] = {}
    for pm in placemarks:
        point = _extract_point(pm)
        if not point:
            continue
        lon, lat = point
        name = pm.name or "unnamed-olt"
        name = _make_unique_point_name(name, lon, lat, _GENERIC_OLT_NAMES, name_counter)
        existing = db.query(OLTDevice).filter(OLTDevice.name == name).first()
        notes = _make_notes(pm.properties)
        if existing:
            if not upsert:
                skipped += 1
                continue
            existing.latitude = lat
            existing.longitude = lon
            existing.notes = notes
            updated += 1
        else:
            olt = OLTDevice(
                name=name,
                latitude=lat,
                longitude=lon,
                notes=notes,
            )
            db.add(olt)
            created += 1
    return created, updated, skipped


def _import_buildings_batch(db, placemarks: list[PlacemarkData], upsert: bool) -> tuple[int, int, int]:
    """Import service buildings from pre-classified placemarks."""
    created = updated = skipped = 0
    name_counter: dict[str, int] = {}
    for pm in placemarks:
        point = _extract_point(pm)
        if not point:
            continue
        lon, lat = point
        name = pm.properties.get("Name") or pm.name or "unnamed-building"
        name = _make_unique_point_name(name, lon, lat, _GENERIC_BUILDING_NAMES, name_counter)
        code = pm.properties.get("buildingid")
        existing = None
        if code:
            existing = db.query(ServiceBuilding).filter(ServiceBuilding.code == code).first()
        if not existing:
            existing = db.query(ServiceBuilding).filter(ServiceBuilding.name == name).first()
        notes = _make_notes(pm.properties)
        clli = pm.properties.get("CLLI")
        street = pm.properties.get("Street")
        city = pm.properties.get("City")
        state = pm.properties.get("State")
        zip_code = pm.properties.get("ZIP")
        work_order = pm.properties.get("Work Order")
        boundary_geom = None
        if pm.geometry_type == "Polygon" and len(pm.coordinates) >= 3:
            coords = pm.coordinates
            if coords[0] != coords[-1]:
                coords = [*coords, coords[0]]
            geojson = {"type": "Polygon", "coordinates": [coords]}
            boundary_geom = _geojson_to_geom(geojson)
        if existing:
            if not upsert:
                skipped += 1
                continue
            existing.name = name
            existing.code = code
            existing.clli = clli
            existing.latitude = lat
            existing.longitude = lon
            existing.geom = _point_geom(lon, lat)
            if boundary_geom is not None:
                existing.boundary_geom = boundary_geom
            existing.street = street
            existing.city = city
            existing.state = state
            existing.zip_code = zip_code
            existing.work_order = work_order
            existing.notes = notes
            updated += 1
        else:
            building = ServiceBuilding(
                name=name,
                code=code,
                clli=clli,
                latitude=lat,
                longitude=lon,
                geom=_point_geom(lon, lat),
                boundary_geom=boundary_geom,
                street=street,
                city=city,
                state=state,
                zip_code=zip_code,
                work_order=work_order,
                notes=notes,
            )
            db.add(building)
            created += 1
    return created, updated, skipped


# ---------------------------------------------------------------------------
# Merged-KMZ orchestrator
# ---------------------------------------------------------------------------


def import_merged(
    db,
    kmz_path: Path,
    segment_type: str,
    cable_type: str | None,
    upsert: bool,
    dry_run: bool,
    limit: int | None,
) -> None:
    """Import a merged KMZ that contains all entity types in one file.

    Steps:
    1. Parse all placemarks from the (deeply nested) KML.
    2. Filter to Abuja bounding box.
    3. Deduplicate by (name, geom_type, start/end coords).
    4. Classify each placemark into an entity type.
    5. Route to the appropriate batch import function.
    6. Print per-entity stats.
    """
    print(f"[merged] Reading {kmz_path} ...")
    root = _read_kmz_kml(kmz_path)

    # Collect all placemarks (handles the deep nesting automatically via .//)
    all_placemarks = list(_iter_placemarks(root, limit=None))
    print(f"[merged] Total placemarks parsed: {len(all_placemarks)}")

    # Step 1: Abuja geographic filter
    abuja_placemarks = [pm for pm in all_placemarks if _is_in_abuja(pm.coordinates)]
    filtered_out = len(all_placemarks) - len(abuja_placemarks)
    print(f"[merged] In Abuja: {len(abuja_placemarks)} (filtered out {filtered_out} outside bounds)")

    # Step 2: Deduplicate
    seen: set[tuple] = set()
    unique: list[PlacemarkData] = []
    for pm in abuja_placemarks:
        key = _dedup_key(pm.name, pm.geometry_type, pm.coordinates)
        if key not in seen:
            seen.add(key)
            unique.append(pm)
    dedup_removed = len(abuja_placemarks) - len(unique)
    print(f"[merged] After dedup: {len(unique)} (removed {dedup_removed} duplicates)")

    # Step 3: Classify
    buckets: dict[str, list[PlacemarkData]] = {
        ENTITY_SEGMENT: [],
        ENTITY_CABINET: [],
        ENTITY_CLOSURE: [],
        ENTITY_ACCESS_POINT: [],
        ENTITY_OLT: [],
        ENTITY_BUILDING: [],
        ENTITY_SKIP: [],
    }
    for pm in unique:
        entity_type = _classify_placemark(pm.name, pm.geometry_type)
        buckets[entity_type].append(pm)

    # Apply limit if specified
    if limit is not None:
        for key in buckets:
            if key != ENTITY_SKIP:
                buckets[key] = buckets[key][:limit]

    print("\n[merged] Classification results:")
    for entity_type, items in sorted(buckets.items(), key=lambda x: -len(x[1])):
        label = entity_type.replace("_", " ").title()
        print(f"  {label:.<25} {len(items):>5}")
    print()

    # Step 4: Import each entity type
    results: dict[str, tuple[int, int, int]] = {}

    if buckets[ENTITY_SEGMENT]:
        print(f"[merged] Importing {len(buckets[ENTITY_SEGMENT])} fiber segments ...")
        results["Fiber Segments"] = _import_segments_batch(
            db, buckets[ENTITY_SEGMENT], segment_type, cable_type, upsert
        )

    if buckets[ENTITY_CABINET]:
        print(f"[merged] Importing {len(buckets[ENTITY_CABINET])} FDH cabinets ...")
        results["FDH Cabinets"] = _import_cabinets_batch(db, buckets[ENTITY_CABINET], upsert)

    if buckets[ENTITY_CLOSURE]:
        print(f"[merged] Importing {len(buckets[ENTITY_CLOSURE])} splice closures ...")
        results["Splice Closures"] = _import_closures_batch(db, buckets[ENTITY_CLOSURE], upsert)

    if buckets[ENTITY_ACCESS_POINT]:
        print(f"[merged] Importing {len(buckets[ENTITY_ACCESS_POINT])} access points ...")
        results["Access Points"] = _import_access_points_batch(db, buckets[ENTITY_ACCESS_POINT], upsert)

    if buckets[ENTITY_OLT]:
        print(f"[merged] Importing {len(buckets[ENTITY_OLT])} OLT/BTS devices ...")
        results["OLT/BTS Devices"] = _import_olt_devices_batch(db, buckets[ENTITY_OLT], upsert)

    if buckets[ENTITY_BUILDING]:
        print(f"[merged] Importing {len(buckets[ENTITY_BUILDING])} buildings ...")
        results["Buildings"] = _import_buildings_batch(db, buckets[ENTITY_BUILDING], upsert)

    # Step 5: Summary
    print(f"\n{'='*60}")
    print(f"  {'Entity':<20} {'Created':>8} {'Updated':>8} {'Skipped':>8}")
    print(f"  {'-'*20} {'-'*8} {'-'*8} {'-'*8}")
    total_c = total_u = total_s = 0
    for entity_name, (c, u, s) in results.items():
        print(f"  {entity_name:<20} {c:>8} {u:>8} {s:>8}")
        total_c += c
        total_u += u
        total_s += s
    print(f"  {'-'*20} {'-'*8} {'-'*8} {'-'*8}")
    print(f"  {'TOTAL':<20} {total_c:>8} {total_u:>8} {total_s:>8}")
    print(f"  Skipped (non-infra): {len(buckets[ENTITY_SKIP])}")
    print(f"{'='*60}")

    if dry_run:
        print("\n[merged] DRY RUN — no changes committed.")
    else:
        print(f"\n[merged] Committing {total_c} new + {total_u} updated records ...")


def main():
    if load_dotenv is not None:
        load_dotenv()
    args = parse_args()
    db = SessionLocal()
    try:
        if args.purge:
            # Delete in dependency order: child tables before parent tables
            # Splitter hierarchy: PonPortSplitterLink -> SplitterPort -> Splitter -> FdhCabinet
            db.query(PonPortSplitterLink).delete()
            db.query(SplitterPort).delete()
            db.query(Splitter).delete()
            db.query(FdhCabinet).delete()
            # Splice hierarchy: FiberSplice -> FiberSpliceTray -> FiberSpliceClosure
            db.query(FiberSplice).delete()
            db.query(FiberSpliceTray).delete()
            db.query(FiberSpliceClosure).delete()
            # Clear FK references before deleting segments
            db.query(AsBuiltRoute).filter(AsBuiltRoute.fiber_segment_id.isnot(None)).update(
                {AsBuiltRoute.fiber_segment_id: None}
            )
            db.query(FiberSegment).delete()
            # New tables (no dependencies)
            db.query(FiberAccessPoint).delete()
            db.query(WirelessMast).delete()
            db.query(ServiceBuilding).delete()

        # ── Merged KMZ mode ─────────────────────────────────────
        if args.merged_kmz:
            import_merged(
                db,
                Path(args.merged_kmz),
                args.segment_type,
                args.cable_type,
                args.upsert,
                args.dry_run,
                args.limit,
            )
            if args.dry_run:
                db.rollback()
            else:
                db.commit()
            return

        # ── Per-type KMZ mode (original) ────────────────────────
        cabinet_paths = [Path(p) for p in args.cabinet_kmz]
        splice_paths = [Path(p) for p in args.splice_kmz]
        segment_paths = [Path(p) for p in args.paths_kmz]
        access_point_paths = [Path(p) for p in args.access_point_kmz]
        mast_paths = [Path(p) for p in args.mast_kmz]
        building_paths = [Path(p) for p in args.building_kmz]

        total_created = total_updated = total_skipped = 0

        if cabinet_paths:
            created, updated, skipped = import_cabinets(db, cabinet_paths, args.upsert, args.limit)
            total_created += created
            total_updated += updated
            total_skipped += skipped

        if splice_paths:
            created, updated, skipped = import_splice_closures(db, splice_paths, args.upsert, args.limit)
            total_created += created
            total_updated += updated
            total_skipped += skipped

        if segment_paths:
            created, updated, skipped = import_segments(
                db,
                segment_paths,
                args.segment_type,
                args.cable_type,
                args.upsert,
                args.limit,
            )
            total_created += created
            total_updated += updated
            total_skipped += skipped

        if access_point_paths:
            created, updated, skipped = import_access_points(db, access_point_paths, args.upsert, args.limit)
            total_created += created
            total_updated += updated
            total_skipped += skipped

        if mast_paths:
            created, updated, skipped = import_wireless_masts(db, mast_paths, args.upsert, args.limit)
            total_created += created
            total_updated += updated
            total_skipped += skipped

        if building_paths:
            created, updated, skipped = import_buildings(db, building_paths, args.upsert, args.limit)
            total_created += created
            total_updated += updated
            total_skipped += skipped

        if args.dry_run:
            db.rollback()
        else:
            db.commit()

    finally:
        db.close()


if __name__ == "__main__":
    main()
