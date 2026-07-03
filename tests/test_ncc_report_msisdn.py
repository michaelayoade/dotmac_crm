from app.web.admin import reports


def test_ncc_msisdn_formats_nigerian_numbers_without_plus_prefix():
    assert reports._complete_ncc_msisdn_or_empty("+2348012345678") == "2348012345678"
    assert reports._complete_ncc_msisdn_or_empty("08012345678") == "2348012345678"
    assert reports._complete_ncc_msisdn_or_empty("8012345678") == "2348012345678"


def test_ncc_msisdn_allows_compact_alphanumeric_device_ids():
    assert reports._complete_ncc_msisdn_or_empty("isp onu-42") == "ISPONU42"
    assert reports._complete_ncc_msisdn_or_empty("DEV-AB12") == "DEVAB12"


def test_ncc_msisdn_rejects_incomplete_or_non_ncc_values():
    assert reports._complete_ncc_msisdn_or_empty("+234801234") == ""
    assert reports._complete_ncc_msisdn_or_empty("080123") == ""
    assert reports._complete_ncc_msisdn_or_empty("not available") == ""
