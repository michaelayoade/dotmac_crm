from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.services.quarterly_reports import build_quarterly_report


def _xlsx_bytes(rows: list[list[object | None]]) -> bytes:
    def col_name(index: int) -> str:
        index += 1
        result = ""
        while index:
            index, remainder = divmod(index - 1, 26)
            result = chr(65 + remainder) + result
        return result

    def cell_xml(value: object | None, row_idx: int, col_idx: int) -> str:
        ref = f"{col_name(col_idx)}{row_idx}"
        if value is None:
            return f'<c r="{ref}"/>'
        if isinstance(value, (int, float)):
            return f'<c r="{ref}"><v>{value}</v></c>'
        escaped = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f'<c r="{ref}" t="inlineStr"><is><t>{escaped}</t></is></c>'

    sheet_rows = []
    for row_idx, row in enumerate(rows, start=1):
        cells = "".join(cell_xml(value, row_idx, col_idx) for col_idx, value in enumerate(row))
        sheet_rows.append(f'<row r="{row_idx}">{cells}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData>"
        "</worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    root_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )

    import io

    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml)
        archive.writestr("_rels/.rels", root_rels_xml)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buffer.getvalue()


def _write_customer_workbook(path: Path) -> None:
    path.write_bytes(
        _xlsx_bytes(
            [
                [None, None, None, None],
                ["S/N", "Full name", "Location", "Category"],
                [1, "Alice A", "Abuja", "Individual"],
                [2, "Beta Corp", "Abuja", "Business"],
                [3, "Chris B", "Lagos", "Individual"],
            ]
        )
    )


def _write_plan_workbook(path: Path) -> None:
    path.write_bytes(
        _xlsx_bytes(
            [
                ["Dotmac: Internet plan usage", None, None],
                ["Plan", "Count of services", "Total Down/Up (GB)"],
                ["Unlimted Premium", 2, 3000],
                ["100 Mbps fiber", 1, 500],
                ["Homeflex Starter", 3, 1200],
                ["Total", 6, 4700],
            ]
        )
    )


def test_build_quarterly_report_normalizes_and_aggregates(tmp_path: Path):
    _write_customer_workbook(tmp_path / "Dotmac Customer internet usage.xlsx")
    _write_plan_workbook(tmp_path / "Internet plan usagey.xlsx")

    report = build_quarterly_report(tmp_path)

    assert report["customer"]["total_customers"] == 3
    assert report["customer"]["abuja_count"] == 2
    assert report["customer"]["lagos_count"] == 1
    assert report["customer"]["individual_count"] == 2
    assert report["customer"]["business_count"] == 1

    assert report["usage"]["total_services"] == 6
    assert report["usage"]["total_usage_gb"] == 4700.0
    assert report["usage"]["total_usage_tb"] == 4.59
    assert report["usage"]["top_plan_by_usage"]["plan"] == "Unlimited Premium"
    assert report["usage"]["top_plan_by_count"]["plan"] == "Homeflex Starter"
    assert report["usage"]["plan_breakdown"][1]["plan"] == "Homeflex Starter"
    assert report["usage"]["plan_breakdown"][2]["plan"] == "100 Mbps Fiber"

    family_rows = {row["family"]: row for row in report["usage"]["family_breakdown"]}
    assert family_rows["Unlimited"]["tb"] == 2.93
    assert family_rows["Fiber"]["tb"] == 0.49
    assert family_rows["Homeflex"]["tb"] == 1.17
