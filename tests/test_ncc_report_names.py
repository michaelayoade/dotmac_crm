from app.web.admin import reports


def test_ncc_name_parts_join_standalone_title_with_first_last_name_token():
    assert reports._normalize_person_name_parts("Mr", "Andrew Uduimoh") == ("Mr Andrew", "Uduimoh")
    assert reports._normalize_person_name_parts("Dr", "John Okafor") == ("Dr John", "Okafor")
    assert reports._normalize_person_name_parts("Miss", "Ada Bello") == ("Miss Ada", "Bello")


def test_ncc_name_parts_drop_title_period_when_joining():
    assert reports._normalize_person_name_parts("Dr.", "John Okafor") == ("Dr John", "Okafor")
    assert reports._normalize_person_name_parts("Mr.", "Andrew Uduimoh") == ("Mr Andrew", "Uduimoh")


def test_ncc_name_parts_leave_title_that_already_has_real_first_name():
    assert reports._normalize_person_name_parts("Dr John", "Okafor") == ("Dr John", "Okafor")


def test_ncc_label_to_name_parts_normalizes_title_fallback_names():
    assert reports._label_to_name_parts("Mr Olaitan Akerele") == ("Mr Olaitan", "Akerele")
    assert reports._label_to_name_parts("Dr Michael Odokoro") == ("Dr Michael", "Odokoro")
