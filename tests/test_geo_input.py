"""Tests for geo input helpers: GeoJSON validation and KMZ conversion."""

import io
import json
import zipfile

import pytest
from fastapi import HTTPException

from app.services.geo_input import kmz_to_geojson, validate_geojson, validate_geojson_bytes


# ---------------------------------------------------------------------------
# GeoJSON validation
# ---------------------------------------------------------------------------

class TestValidateGeoJSON:
    def test_valid_linestring(self):
        data = {"type": "LineString", "coordinates": [[3.0, 6.0], [3.001, 6.001]]}
        result = validate_geojson(data)
        assert result["type"] == "LineString"
        assert len(result["coordinates"]) == 2

    def test_valid_feature_unwrap(self):
        data = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        }
        result = validate_geojson(data)
        assert result["type"] == "LineString"

    def test_valid_feature_collection_unwrap(self):
        data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
                }
            ],
        }
        result = validate_geojson(data)
        assert result["type"] == "LineString"

    def test_rejects_empty_feature_collection(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_geojson({"type": "FeatureCollection", "features": []})
        assert exc_info.value.status_code == 400

    def test_rejects_missing_type(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_geojson({"coordinates": [[0, 0]]})
        assert exc_info.value.status_code == 400

    def test_rejects_too_few_coordinates(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_geojson({"type": "LineString", "coordinates": [[0, 0]]})
        assert exc_info.value.status_code == 400

    def test_rejects_out_of_range_coordinates(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_geojson({"type": "LineString", "coordinates": [[200, 0], [0, 0]]})
        assert exc_info.value.status_code == 400

    def test_accepts_point(self):
        result = validate_geojson({"type": "Point", "coordinates": [3.0, 6.0]})
        assert result["type"] == "Point"

    def test_rejects_non_dict(self):
        with pytest.raises(HTTPException):
            validate_geojson("not a dict")


class TestValidateGeoJSONBytes:
    def test_valid_bytes(self):
        raw = json.dumps({"type": "LineString", "coordinates": [[0, 0], [1, 1]]}).encode()
        result = validate_geojson_bytes(raw)
        assert result["type"] == "LineString"

    def test_rejects_oversized(self):
        raw = b"x" * (5 * 1024 * 1024 + 1)
        with pytest.raises(HTTPException) as exc_info:
            validate_geojson_bytes(raw)
        assert "5 MB" in exc_info.value.detail

    def test_rejects_invalid_json(self):
        with pytest.raises(HTTPException):
            validate_geojson_bytes(b"not json{{{")


# ---------------------------------------------------------------------------
# KMZ conversion
# ---------------------------------------------------------------------------

def _make_kmz(kml_content: str) -> bytes:
    """Helper to create a valid KMZ (zipped KML) from a KML string."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc.kml", kml_content)
    return buf.getvalue()


_VALID_KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <LineString>
        <coordinates>3.0,6.0,0 3.001,6.001,0 3.002,6.002,0</coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>"""


class TestKMZToGeoJSON:
    def test_valid_kmz_conversion(self):
        raw = _make_kmz(_VALID_KML)
        result = kmz_to_geojson(raw)
        assert result["type"] == "LineString"
        assert len(result["coordinates"]) == 3
        assert result["coordinates"][0][0] == 3.0
        assert result["coordinates"][0][1] == 6.0

    def test_rejects_oversized(self):
        with pytest.raises(HTTPException) as exc_info:
            kmz_to_geojson(b"x" * (10 * 1024 * 1024 + 1))
        assert "10 MB" in exc_info.value.detail

    def test_rejects_invalid_zip(self):
        with pytest.raises(HTTPException) as exc_info:
            kmz_to_geojson(b"not a zip file")
        assert "ZIP" in exc_info.value.detail

    def test_rejects_no_kml_file(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("readme.txt", "hello")
        with pytest.raises(HTTPException) as exc_info:
            kmz_to_geojson(buf.getvalue())
        assert "no KML" in exc_info.value.detail

    def test_rejects_kml_without_coordinates(self):
        kml = """<?xml version="1.0"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document><name>Empty</name></Document>
</kml>"""
        with pytest.raises(HTTPException) as exc_info:
            kmz_to_geojson(_make_kmz(kml))
        assert "coordinates" in exc_info.value.detail.lower()

    def test_rejects_single_coordinate(self):
        kml = """<?xml version="1.0"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <LineString><coordinates>3.0,6.0,0</coordinates></LineString>
    </Placemark>
  </Document>
</kml>"""
        with pytest.raises(HTTPException) as exc_info:
            kmz_to_geojson(_make_kmz(kml))
        assert "at least 2" in exc_info.value.detail

    def test_rejects_path_traversal(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../../../etc/passwd.kml", _VALID_KML)
        with pytest.raises(HTTPException) as exc_info:
            kmz_to_geojson(buf.getvalue())
        assert "unsafe" in exc_info.value.detail.lower()
