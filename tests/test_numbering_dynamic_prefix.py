from unittest.mock import patch

from app.models.domain_settings import SettingDomain
from app.services.numbering import generate_number


def test_generate_number_renders_dynamic_prefix_and_scopes_sequence_key(db_session):
    settings = {
        "material_request_number_enabled": True,
        "material_request_number_prefix": "MR{YYYYMM}-",
        "material_request_number_padding": 5,
        "material_request_number_start": 1,
    }

    captured = {}

    def fake_resolve_setting(_db, _domain, key):
        return settings[key]

    def fake_next_sequence_value(_db, key, start_value):
        captured["key"] = key
        captured["start"] = start_value
        return 1

    with (
        patch("app.services.numbering._resolve_setting", side_effect=fake_resolve_setting),
        patch("app.services.numbering._next_sequence_value", side_effect=fake_next_sequence_value),
    ):
        number = generate_number(
            db=db_session,
            domain=SettingDomain.numbering,
            sequence_key="material_request_number",
            enabled_key="material_request_number_enabled",
            prefix_key="material_request_number_prefix",
            padding_key="material_request_number_padding",
            start_key="material_request_number_start",
        )

    assert number is not None
    assert number.startswith("MR")
    assert number.endswith("-00001")
    assert captured["start"] == 1
    assert captured["key"].startswith("material_request_number:MR")


def test_generate_number_static_prefix_keeps_base_sequence_key(db_session):
    settings = {
        "inventory_item_number_enabled": True,
        "inventory_item_number_prefix": "ITEM-",
        "inventory_item_number_padding": 5,
        "inventory_item_number_start": 10,
    }

    captured = {}

    def fake_resolve_setting(_db, _domain, key):
        return settings[key]

    def fake_next_sequence_value(_db, key, start_value):
        captured["key"] = key
        captured["start"] = start_value
        return 10

    with (
        patch("app.services.numbering._resolve_setting", side_effect=fake_resolve_setting),
        patch("app.services.numbering._next_sequence_value", side_effect=fake_next_sequence_value),
    ):
        number = generate_number(
            db=db_session,
            domain=SettingDomain.numbering,
            sequence_key="inventory_item_number",
            enabled_key="inventory_item_number_enabled",
            prefix_key="inventory_item_number_prefix",
            padding_key="inventory_item_number_padding",
            start_key="inventory_item_number_start",
        )

    assert number == "ITEM-00010"
    assert captured["start"] == 10
    assert captured["key"] == "inventory_item_number"
