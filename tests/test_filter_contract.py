import pytest

from app.services.filter_contract import FilterExpression, FilterTerm


def test_filter_term_accepts_valid_ticket_row():
    term = FilterTerm.from_row(["Ticket", "status", "=", "open"])
    assert term.doctype == "Ticket"
    assert term.field == "status"
    assert term.operator == "="
    assert term.value == "open"


def test_filter_term_accepts_doctype_alias():
    term = FilterTerm.from_row(["tickets", "priority", "=", "high"])
    assert term.doctype == "Ticket"


def test_filter_term_rejects_unknown_doctype():
    with pytest.raises(ValueError, match="Unsupported doctype"):
        FilterTerm.from_row(["Incident", "status", "=", "open"])


def test_filter_term_rejects_unknown_field_for_doctype():
    with pytest.raises(ValueError, match="not allowed for doctype"):
        FilterTerm.from_row(["Ticket", "project_type", "=", "fiber_optics_installation"])


def test_filter_term_rejects_invalid_operator():
    with pytest.raises(ValueError):
        FilterTerm.from_row(["Ticket", "status", "contains", "open"])


def test_filter_term_rejects_invalid_select_option():
    with pytest.raises(ValueError, match="Invalid option"):
        FilterTerm.from_row(["Ticket", "status", "=", "not_a_real_status"])


def test_filter_term_rejects_like_on_datetime():
    with pytest.raises(ValueError, match="not allowed for field type"):
        FilterTerm.from_row(["Ticket", "created_at", "like", "2026"])


def test_filter_term_accepts_datetime_range_operator():
    term = FilterTerm.from_row(["Ticket", "created_at", ">=", "2026-02-16T09:00:00+00:00"])
    assert term.operator == ">="


def test_filter_term_rejects_in_without_array():
    with pytest.raises(ValueError, match="requires a non-empty array"):
        FilterTerm.from_row(["Ticket", "status", "in", "open"])


def test_filter_term_accepts_in_with_array():
    term = FilterTerm.from_row(["Ticket", "status", "in", ["open", "pending"]])
    assert term.operator == "in"
    assert term.value == ["open", "pending"]


def test_filter_term_normalizes_is_null_values():
    term = FilterTerm.from_row(["Ticket", "due_at", "is", "null"])
    assert term.value is None


def test_filter_expression_parses_and_and_or():
    expression = FilterExpression.parse_payload(
        [
            ["Ticket", "status", "=", "open"],
            {"or": [["Ticket", "priority", "=", "high"], ["Ticket", "priority", "=", "urgent"]]},
        ]
    )
    assert len(expression.and_terms) == 1
    assert len(expression.or_groups) == 1
    assert len(expression.or_groups[0].items) == 2


def test_filter_expression_rejects_bad_entry_shape():
    with pytest.raises(ValueError, match="row array or an OR group object"):
        FilterExpression.parse_payload([{"and": []}])


def test_filter_expression_round_trip_payload():
    payload = [
        ["Project", "status", "=", "active"],
        {"or": [["Project", "priority", "=", "high"], ["Project", "priority", "=", "urgent"]]},
    ]
    expression = FilterExpression.parse_payload(payload)
    assert expression.to_payload() == payload
