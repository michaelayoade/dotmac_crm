"""Geo input helpers for variation workflow.

Validates GeoJSON input and provides guarded KMZ→GeoJSON conversion.
"""

import contextlib
import io
import json
import logging
import zipfile
from typing import Any
from xml.etree import ElementTree

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Maximum file size for KMZ uploads (10 MB)
MAX_KMZ_BYTES = 10 * 1024 * 1024
# Maximum file size for GeoJSON uploads (5 MB)
MAX_GEOJSON_BYTES = 5 * 1024 * 1024

_GEOJSON_GEOMETRY_TYPES = frozenset({
    "Point", "MultiPoint", "LineString", "MultiLineString",
    "Polygon", "MultiPolygon", "GeometryCollection",
})


def validate_geojson(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a GeoJSON dict, extracting the first LineString geometry.

    Accepts raw geometry objects, Feature, or FeatureCollection.
    Returns a GeoJSON geometry dict suitable for PostGIS ingestion.

    Raises:
        HTTPException(400) on invalid input.
    """
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="GeoJSON must be a JSON object")

    geom_type = data.get("type")
    if not geom_type:
        raise HTTPException(status_code=400, detail="GeoJSON missing 'type' field")

    # Unwrap FeatureCollection → first Feature
    if geom_type == "FeatureCollection":
        features = data.get("features")
        if not isinstance(features, list) or not features:
            raise HTTPException(status_code=400, detail="FeatureCollection has no features")
        data = features[0]
        geom_type = data.get("type")

    # Unwrap Feature → geometry
    if geom_type == "Feature":
        geometry = data.get("geometry")
        if not isinstance(geometry, dict):
            raise HTTPException(status_code=400, detail="Feature has no geometry")
        data = geometry
        geom_type = data.get("type")

    if geom_type not in _GEOJSON_GEOMETRY_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported GeoJSON geometry type: {geom_type}")

    coords = data.get("coordinates")
    if not isinstance(coords, list):
        raise HTTPException(status_code=400, detail="GeoJSON geometry missing coordinates")

    # For LineString, validate coordinate structure
    if geom_type == "LineString":
        if len(coords) < 2:
            raise HTTPException(status_code=400, detail="LineString must have at least 2 coordinates")
        for i, coord in enumerate(coords):
            if not isinstance(coord, list) or len(coord) < 2:
                raise HTTPException(status_code=400, detail=f"Invalid coordinate at index {i}")
            lon, lat = coord[0], coord[1]
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise HTTPException(status_code=400, detail=f"Coordinate out of range at index {i}")

    return {"type": geom_type, "coordinates": coords}


def validate_geojson_bytes(raw: bytes) -> dict[str, Any]:
    """Parse raw bytes as GeoJSON and validate."""
    if len(raw) > MAX_GEOJSON_BYTES:
        raise HTTPException(status_code=400, detail="GeoJSON file exceeds 5 MB limit")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON in GeoJSON file") from exc
    return validate_geojson(data)


def kmz_to_geojson(raw: bytes) -> dict[str, Any]:
    """Convert KMZ bytes to a GeoJSON LineString geometry.

    KMZ is a zipped KML file. We extract the first KML document,
    parse out LineString coordinates, and return valid GeoJSON.

    Raises:
        HTTPException(400) on invalid/unsupported input.
    """
    if len(raw) > MAX_KMZ_BYTES:
        raise HTTPException(status_code=400, detail="KMZ file exceeds 10 MB limit")

    try:
        with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
            # Security: check for path traversal
            for info in zf.infolist():
                if info.filename.startswith("/") or ".." in info.filename:
                    raise HTTPException(status_code=400, detail="KMZ contains unsafe file paths")

            kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
            if not kml_names:
                raise HTTPException(status_code=400, detail="KMZ archive contains no KML file")
            kml_data = zf.read(kml_names[0])
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid KMZ file (not a valid ZIP archive)") from exc

    return _parse_kml_linestring(kml_data)


def _parse_kml_linestring(kml_bytes: bytes) -> dict[str, Any]:
    """Extract first LineString from KML XML."""
    try:
        root = ElementTree.fromstring(kml_bytes)
    except ElementTree.ParseError as exc:
        raise HTTPException(status_code=400, detail="Invalid KML XML") from exc

    # KML uses namespaces; find coordinate elements
    ns = {"kml": "http://www.opengis.net/kml/2.2"}
    # Try with namespace first, then without
    coord_elements = root.findall(".//kml:coordinates", ns)
    if not coord_elements:
        coord_elements = root.findall(".//{http://www.opengis.net/kml/2.2}coordinates")
    if not coord_elements:
        # Try without namespace for older KML
        coord_elements = root.findall(".//coordinates")
    if not coord_elements:
        raise HTTPException(status_code=400, detail="No coordinates found in KML")

    coords_text = coord_elements[0].text
    if not coords_text or not coords_text.strip():
        raise HTTPException(status_code=400, detail="Empty coordinates in KML")

    coordinates = []
    for point in coords_text.strip().split():
        parts = point.strip().split(",")
        if len(parts) < 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            continue
        coord = [lon, lat]
        if len(parts) >= 3:
            with contextlib.suppress(ValueError):
                coord.append(float(parts[2]))
        coordinates.append(coord)

    if len(coordinates) < 2:
        raise HTTPException(status_code=400, detail="KML must contain at least 2 valid coordinate points")

    return {"type": "LineString", "coordinates": coordinates}
