from app.services.crm.inbox.admin_ui import _normalize_whatsapp_address_for_country


def test_normalize_whatsapp_address_uses_nigeria_default_for_local_numbers():
    assert _normalize_whatsapp_address_for_country("08012345678", "NG") == "+2348012345678"
    assert _normalize_whatsapp_address_for_country("0812 345 6789", "NG") == "+2348123456789"
    assert _normalize_whatsapp_address_for_country("080-1234-5678", "NG") == "+2348012345678"
    assert _normalize_whatsapp_address_for_country("8012345678", "NG") == "+2348012345678"


def test_normalize_whatsapp_address_keeps_international_numbers():
    assert _normalize_whatsapp_address_for_country("+2348012345678", "NG") == "+2348012345678"
    assert _normalize_whatsapp_address_for_country("2348012345678", "NG") == "+2348012345678"
    assert _normalize_whatsapp_address_for_country("002348012345678", "NG") == "+2348012345678"


def test_normalize_whatsapp_address_uses_selected_country_for_local_numbers():
    assert _normalize_whatsapp_address_for_country("0241234567", "GH") == "+233241234567"
    assert _normalize_whatsapp_address_for_country("07123456789", "GB") == "+447123456789"
    assert _normalize_whatsapp_address_for_country("(202) 555-0142", "US") == "+12025550142"
    assert _normalize_whatsapp_address_for_country("0712345678", "ZA") == "+27712345678"
    assert _normalize_whatsapp_address_for_country("0712345678", "KE") == "+254712345678"


def test_normalize_whatsapp_address_falls_back_to_nigeria_for_unknown_country():
    assert _normalize_whatsapp_address_for_country("08012345678", "XX") == "+2348012345678"
