"""Admin material request web route helpers."""

from uuid import uuid4

from app.web.admin.material_requests import _parse_material_request_items


def test_parse_material_request_items_ignores_blank_quantity_for_empty_row():
    first_item_id = str(uuid4())
    second_item_id = str(uuid4())

    items = _parse_material_request_items(
        item_id=[
            first_item_id,
            second_item_id,
            "",
            "",
            "",
            "",
            "",
        ],
        quantity=["2", "1", "", "", "", "", ""],
        item_notes=["first", "second", "", "", "", "", ""],
    )

    assert len(items) == 2
    assert str(items[0].item_id) == first_item_id
    assert items[0].quantity == 2
    assert items[0].notes == "first"
    assert str(items[1].item_id) == second_item_id
    assert items[1].quantity == 1


def test_parse_material_request_items_skips_selected_row_with_invalid_quantity():
    valid_item_id = str(uuid4())
    invalid_item_id = str(uuid4())

    items = _parse_material_request_items(
        item_id=[valid_item_id, invalid_item_id],
        quantity=["3", ""],
        item_notes=["valid", "invalid"],
    )

    assert len(items) == 1
    assert str(items[0].item_id) == valid_item_id
    assert items[0].quantity == 3
