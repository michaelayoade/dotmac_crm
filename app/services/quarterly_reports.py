from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET  # nosec B405 - parses trusted bundled XLSX XML only
from zipfile import ZipFile

CUSTOMER_WORKBOOK = "Dotmac Customer internet usage.xlsx"
PLAN_WORKBOOK = "Internet plan usagey.xlsx"
XML_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _candidate_base_dirs() -> list[Path]:
    repo_root = _repo_root()
    return [
        repo_root,
        repo_root / "static" / "uploads" / "reports",
        Path("/app"),
        Path("/app/static/uploads/reports"),
    ]


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def _normalize_plan_name(name: str) -> str:
    normalized = _normalize_text(name)
    replacements = {
        "Unlimted Premium": "Unlimited Premium",
        "100 Mbps fiber": "100 Mbps Fiber",
    }
    return replacements.get(normalized, normalized)


def _plan_family(name: str) -> str:
    lowered = name.lower()
    if "unlimited" in lowered:
        return "Unlimited"
    if "dedicated" in lowered:
        return "Dedicated"
    if "homeflex" in lowered:
        return "Homeflex"
    if "fiber" in lowered:
        return "Fiber"
    if "gb" in lowered:
        return "Data Bundle"
    return "Other"


def _is_summary_plan_row(name: str) -> bool:
    lowered = _normalize_text(name).lower()
    return lowered in {"total", "grand total", "subtotal"}


def _round(value: float, digits: int = 2) -> float:
    return round(value, digits)


def _gb_to_tb(value_gb: float) -> float:
    return value_gb / 1024


def _column_index(cell_ref: str) -> int:
    letters = "".join(char for char in cell_ref if char.isalpha())
    index = 0
    for char in letters:
        index = index * 26 + (ord(char.upper()) - 64)
    return max(index - 1, 0)


def _parse_number(raw: str) -> int | float | str:
    value = raw.strip()
    if value == "":
        return ""
    try:
        number = float(value)
    except ValueError:
        return value
    if number.is_integer():
        return int(number)
    return number


def _shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    # Quarterly workbooks are admin-supplied XLSX files parsed from internal storage.
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))  # nosec B314
    strings: list[str] = []
    for item in root.findall("x:si", XML_NS):
        parts = [node.text or "" for node in item.findall(".//x:t", XML_NS)]
        strings.append("".join(parts))
    return strings


def _sheet_rows(path: Path) -> list[list[Any]]:
    with ZipFile(path) as archive:
        shared = _shared_strings(archive)
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))  # nosec B314
        rows: list[list[Any]] = []
        for row in root.findall(".//x:sheetData/x:row", XML_NS):
            current: list[Any] = []
            cells = row.findall("x:c", XML_NS)
            for cell in cells:
                cell_ref = cell.attrib.get("r", "")
                column = _column_index(cell_ref)
                while len(current) <= column:
                    current.append(None)
                cell_type = cell.attrib.get("t")
                value_node = cell.find("x:v", XML_NS)
                inline_node = cell.find("x:is/x:t", XML_NS)
                if cell_type == "inlineStr" and inline_node is not None:
                    value: Any = inline_node.text or ""
                elif value_node is None or value_node.text is None:
                    value = None
                elif cell_type == "s":
                    shared_index = int(value_node.text)
                    value = shared[shared_index] if shared_index < len(shared) else value_node.text
                else:
                    value = _parse_number(value_node.text)
                current[column] = value
            rows.append(current)
    return rows


def build_quarterly_report(base_dir: Path | None = None) -> dict[str, Any]:
    if base_dir is not None:
        roots = [base_dir]
    else:
        roots = _candidate_base_dirs()

    customer_path = _resolve_workbook_path(CUSTOMER_WORKBOOK, roots)
    plan_path = _resolve_workbook_path(PLAN_WORKBOOK, roots)
    customer_report = _build_customer_report(customer_path)
    usage_report = _build_usage_report(plan_path)
    return {
        "sources": {
            "customer_workbook": str(customer_path),
            "plan_workbook": str(plan_path),
        },
        "customer": customer_report,
        "usage": usage_report,
    }


def _resolve_workbook_path(filename: str, roots: list[Path]) -> Path:
    for root in roots:
        candidate = root / filename
        if candidate.exists():
            return candidate
    raise FileNotFoundError(filename)


def _build_customer_report(path: Path) -> dict[str, Any]:
    rows = _sheet_rows(path)
    records: list[dict[str, str]] = []
    for row in rows[2:]:
        if not row or all(value is None for value in row):
            continue
        padded = list(row) + [None] * max(0, 4 - len(row))
        _, full_name, location, category = padded[:4]
        records.append(
            {
                "full_name": _normalize_text(full_name),
                "location": _normalize_text(location) or "Unknown",
                "category": _normalize_text(category) or "Unknown",
            }
        )

    category_counts = Counter(record["category"] for record in records)
    location_counts = Counter(record["location"] for record in records)
    location_category_counts = Counter((record["location"], record["category"]) for record in records)
    total = len(records)

    return {
        "total_customers": total,
        "total_locations": len(location_counts),
        "total_categories": len(category_counts),
        "individual_count": category_counts.get("Individual", 0),
        "business_count": category_counts.get("Business", 0),
        "abuja_count": location_counts.get("Abuja", 0),
        "lagos_count": location_counts.get("Lagos", 0),
        "abuja_share_pct": _round((location_counts.get("Abuja", 0) / total) * 100 if total else 0),
        "category_breakdown": [
            {
                "label": label,
                "count": count,
                "pct": _round((count / total) * 100 if total else 0, 1),
            }
            for label, count in sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "location_breakdown": [
            {
                "label": label,
                "count": count,
                "pct": _round((count / total) * 100 if total else 0, 1),
            }
            for label, count in sorted(location_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "location_category_breakdown": [
            {
                "location": location,
                "category": category,
                "count": count,
            }
            for (location, category), count in sorted(
                location_category_counts.items(),
                key=lambda item: (-item[1], item[0][0], item[0][1]),
            )
        ],
    }


def _build_usage_report(path: Path) -> dict[str, Any]:
    rows = _sheet_rows(path)
    plans: list[dict[str, Any]] = []
    family_rollup: dict[str, dict[str, float | int]] = defaultdict(lambda: {"count": 0, "gb": 0.0, "plans": 0})

    for row in rows[2:]:
        if not row or all(value is None for value in row):
            continue
        padded = list(row) + [None] * max(0, 3 - len(row))
        plan_name, service_count, total_gb = padded[:3]
        if plan_name is None:
            continue

        normalized_name = _normalize_plan_name(str(plan_name))
        if _is_summary_plan_row(normalized_name):
            continue
        count = int(service_count or 0)
        gb = float(total_gb or 0)
        family = _plan_family(normalized_name)

        plans.append(
            {
                "plan": normalized_name,
                "family": family,
                "count": count,
                "gb": _round(gb),
                "tb": _round(_gb_to_tb(gb)),
                "avg_gb_per_service": _round(gb / count if count else 0),
            }
        )
        family_rollup[family]["count"] += count
        family_rollup[family]["gb"] += gb
        family_rollup[family]["plans"] += 1

    total_services = sum(plan["count"] for plan in plans)
    total_gb = sum(float(plan["gb"]) for plan in plans)
    top_by_usage = sorted(plans, key=lambda item: (-float(item["gb"]), -int(item["count"]), str(item["plan"])))
    top_by_count = sorted(plans, key=lambda item: (-int(item["count"]), -float(item["gb"]), str(item["plan"])))

    family_breakdown = []
    for family, values in sorted(family_rollup.items(), key=lambda item: (-item[1]["gb"], item[0])):
        gb = float(values["gb"])
        count = int(values["count"])
        family_breakdown.append(
            {
                "family": family,
                "plans": int(values["plans"]),
                "count": count,
                "gb": _round(gb),
                "tb": _round(_gb_to_tb(gb)),
                "usage_share_pct": _round((gb / total_gb) * 100 if total_gb else 0, 1),
                "service_share_pct": _round((count / total_services) * 100 if total_services else 0, 1),
            }
        )

    return {
        "total_plans": len(plans),
        "total_services": total_services,
        "total_usage_gb": _round(total_gb),
        "total_usage_tb": _round(_gb_to_tb(total_gb)),
        "top_plan_by_usage": top_by_usage[0] if top_by_usage else None,
        "top_plan_by_count": top_by_count[0] if top_by_count else None,
        "plan_breakdown": top_by_usage,
        "top_usage_plans": top_by_usage[:10],
        "top_count_plans": top_by_count[:10],
        "family_breakdown": family_breakdown,
    }
