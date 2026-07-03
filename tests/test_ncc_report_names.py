from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

from app.web.admin import reports


def test_ncc_name_parts_drop_standalone_title_and_split_real_name():
    assert reports._normalize_person_name_parts("Mr", "Andrew Uduimoh") == ("Andrew", "Uduimoh")
    assert reports._normalize_person_name_parts("Dr", "John Okafor") == ("John", "Okafor")
    assert reports._normalize_person_name_parts("Miss", "Ada Bello") == ("Ada", "Bello")


def test_ncc_name_parts_drop_title_period_before_splitting():
    assert reports._normalize_person_name_parts("Dr.", "John Okafor") == ("John", "Okafor")
    assert reports._normalize_person_name_parts("Mr.", "Andrew Uduimoh") == ("Andrew", "Uduimoh")


def test_ncc_name_parts_drop_leading_title_when_first_name_has_real_name():
    assert reports._normalize_person_name_parts("Dr John", "Okafor") == ("John", "Okafor")
    assert reports._normalize_person_name_parts("Mr Abdulrazak Ibrahim", "") == ("Abdulrazak", "Ibrahim")


def test_ncc_label_to_name_parts_normalizes_title_fallback_names():
    assert reports._label_to_name_parts("Mr Olaitan Akerele") == ("Olaitan", "Akerele")
    assert reports._label_to_name_parts("Dr Michael Odokoro") == ("Michael", "Odokoro")


def test_ncc_ticket_name_parts_drops_standalone_title_from_customer_fields():
    person = SimpleNamespace(first_name="Dr.", last_name="Abdulrazak Ibrahim", display_name="Dr. Abdulrazak Ibrahim")
    ticket = SimpleNamespace(subscriber=None)

    assert reports._ticket_name_parts(ticket, person) == ("Abdulrazak", "Ibrahim")


def test_ncc_ticket_name_parts_splits_multiword_first_name_when_last_name_blank():
    person = SimpleNamespace(first_name="Abdulrazak Ibrahim", last_name="", display_name="Abdulrazak Ibrahim")
    ticket = SimpleNamespace(subscriber=None)

    assert reports._ticket_name_parts(ticket, person) == ("Abdulrazak", "Ibrahim")


def test_ncc_ticket_name_parts_drops_trailing_town_from_customer_name():
    person = SimpleNamespace(first_name="Melvin", last_name="Usman Jabi", display_name="Melvin Usman Jabi")
    ticket = SimpleNamespace(subscriber=None)

    assert reports._ticket_name_parts(ticket, person) == ("Melvin", "Usman")


def test_ncc_ticket_name_parts_extracts_valid_tokens_from_business_names():
    person = SimpleNamespace(
        first_name="Sam - Vic Insurance Brokers Limited",
        last_name="",
        display_name="Sam - Vic Insurance Brokers Limited",
    )
    ticket = SimpleNamespace(subscriber=None)

    assert reports._ticket_name_parts(ticket, person) == ("Sam", "Limited")


def test_ncc_ticket_name_parts_uses_ticket_title_as_last_name_fallback():
    person = SimpleNamespace(first_name="", last_name="", display_name="", email="")
    ticket = SimpleNamespace(
        subscriber=None,
        title="Customer Link Disconnection - Hon. Emmanuel Ibeshi",
        ticket_type="Customer Link Disconnection",
    )

    assert reports._ticket_name_parts(ticket, person) == ("Emmanuel", "Ibeshi")


def test_ncc_ticket_name_parts_does_not_use_network_title_as_customer_name():
    person = None
    ticket = SimpleNamespace(
        subscriber=None,
        title="Multiple Cabinet Disconnection-BOI Asokoro OLT",
        ticket_type="Multiple Cabinet Disconnection",
    )

    assert reports._ticket_name_parts(ticket, person) == ("", "")

    ticket = SimpleNamespace(
        subscriber=None,
        title="Multiple BTS Down",
        ticket_type="BTS Issues",
    )

    assert reports._ticket_name_parts(ticket, person) == ("", "")

    ticket = SimpleNamespace(
        subscriber=None,
        title="Garki AP Outage",
        ticket_type="AP/Air Fiber Outage",
    )

    assert reports._ticket_name_parts(ticket, person) == ("", "")

    ticket = SimpleNamespace(
        subscriber=None,
        title="Test",
        ticket_type="Bandwidth Complaint",
    )

    assert reports._ticket_name_parts(ticket, person) == ("", "")


def test_ncc_ticket_name_parts_ignores_unknown_placeholder_before_fallbacks():
    person = SimpleNamespace(
        first_name="Unknown",
        last_name="",
        display_name="RCC Maitama GH",
        email="sunnybrown24@gmail.com",
    )
    ticket = SimpleNamespace(subscriber=None, title="RCC Maitama GH", ticket_type="")

    assert reports._ticket_name_parts(ticket, person) == ("Rcc", "Gh")


def test_ncc_ticket_person_uses_exact_subscriber_display_name_fallback():
    fallback_person = SimpleNamespace(display_name="NTEL CBD")
    subscriber = SimpleNamespace(
        display_name="NTEL CBD",
        subscriber_number="NTEL CBD",
        external_id="NTEL CBD",
        organization=None,
        person=None,
    )
    ticket = SimpleNamespace(customer=None, subscriber=subscriber)

    assert reports._ticket_ncc_person(ticket, {"ntel cbd": [fallback_person]}) is fallback_person


def test_ncc_clean_record_splits_multiword_first_name_instead_of_blanking_it():
    cleaned = reports._clean_ncc_record({"First Name": "Sa\u2019Adatu Lecky", "Last Name": ""})

    assert cleaned["First Name"] == "Saadatu"
    assert cleaned["Last Name"] == "Lecky"


def test_ncc_email_cleaning_suppresses_placeholder_addresses():
    assert reports._ncc_clean_email("contact-25827@placeholder.local") == ""
    assert reports._ncc_clean_email("chatwoot-6599@placeholder.local") == ""
    assert reports._ncc_clean_email("whatsapp--2348032799644@example.invalid") == ""


def test_ncc_ticket_email_uses_real_channel_email_when_primary_is_placeholder():
    person = SimpleNamespace(email="contact-25827@placeholder.local")
    channels = [
        SimpleNamespace(channel_type="whatsapp", address="+2348012345678"),
        SimpleNamespace(channel_type="email", address="real.customer@example.com"),
    ]

    assert reports._ticket_email(person, channels) == "real.customer@example.com"


def test_ncc_clean_record_preserves_temporary_na_age_and_gender():
    cleaned = reports._clean_ncc_record({"Age": "N/A", "Gender": "N/A"})

    assert cleaned["Age"] == "N/A"
    assert cleaned["Gender"] == "N/A"


def test_ncc_clean_record_fills_blank_last_name_with_unknown():
    cleaned = reports._clean_ncc_record({"First Name": "Ada", "Last Name": ""})

    assert cleaned["Last Name"] == "Ada"


def test_ncc_name_contains_test_detects_first_or_last_name_test_data():
    assert reports._ncc_name_contains_test("Test")
    assert reports._ncc_name_contains_test("Customer Test")
    assert reports._ncc_name_contains_test("test-customer")
    assert not reports._ncc_name_contains_test("Contest")


def test_ncc_ticket_id_uses_operator_date_and_number():
    ticket = SimpleNamespace(created_at=datetime(2026, 7, 3, 9, 15, tzinfo=UTC), number="19655", id="fallback")

    assert reports._ncc_ticket_id(ticket) == "DOTMAC-20260703-19655"


def test_ncc_validation_status_has_specific_column_message():
    cleaned = reports._clean_ncc_record(
        {
            "MSISDN": "8012345678",
            "First Name": "Ada",
            "Last Name": "Bello",
            "Age": "N/A",
            "Gender": "N/A",
            "created date time": "03-07-2026 09:00:00",
            "Category": "Billing",
            "category code (auto)": "A",
            "sub category code": "A50 - Others (Billing)",
            "Ticket ID": "BAD",
            "Complaint type": "First Level",
            "Status": "Pending",
            "Ticket source": "Phone Call",
        }
    )

    assert cleaned["VALIDATION STATUS"].startswith("[FAIL]")
    assert "Last Name is required" not in cleaned["VALIDATION STATUS"]
    assert "Ticket ID must use format DOTMAC-YYYYMMDD-Number (col M)" in cleaned["VALIDATION STATUS"]


def test_ncc_validation_status_rejects_test_names():
    cleaned = reports._clean_ncc_record(
        {
            "MSISDN": "2348012345678",
            "First Name": "Test",
            "Last Name": "Bello",
            "Age": "N/A",
            "Gender": "N/A",
            "created date time": "03-07-2026 09:00:00",
            "Category": "Billing",
            "category code (auto)": "A",
            "sub category code": "A50 - Others (Billing)",
            "Ticket ID": "DOTMAC-20260703-19655",
            "Complaint type": "First Level",
            "Status": "Pending",
            "Ticket source": "Phone Call",
        }
    )

    assert cleaned["VALIDATION STATUS"].startswith("[FAIL]")
    assert "First Name must not contain test data" in cleaned["VALIDATION STATUS"]


def test_ncc_validation_status_requires_language_state_and_lga():
    cleaned = reports._clean_ncc_record(
        {
            "MSISDN": "2348012345678",
            "First Name": "Ada",
            "Last Name": "Bello",
            "Age": "N/A",
            "Gender": "N/A",
            "created date time": "03-07-2026 09:00:00",
            "Category": "Billing",
            "category code (auto)": "A",
            "sub category code": "A50 - Others (Billing)",
            "Ticket ID": "DOTMAC-20260703-19655",
            "Complaint type": "First Level",
            "Status": "Pending",
            "Ticket source": "Phone Call",
        }
    )

    assert cleaned["VALIDATION STATUS"].startswith("[FAIL]")
    assert "Language is required (col U)" in cleaned["VALIDATION STATUS"]
    assert "State is required (col Y)" in cleaned["VALIDATION STATUS"]
    assert "LGA is required (col Z)" in cleaned["VALIDATION STATUS"]


def test_ncc_location_maps_area_from_full_service_address():
    assert reports._map_ncc_location("10 Clement Akpamgbo Close, Guzape") == (
        "Municipal Area Council",
        "Guzape",
        "FEDERAL CAPITAL TERRITORY",
    )


def test_ncc_location_maps_newly_accepted_asokoro_town():
    assert reports._map_ncc_location("12 Ukpabi Asika St, Asokoro, Abuja FCT") == (
        "Municipal Area Council",
        "Asokoro",
        "FEDERAL CAPITAL TERRITORY",
    )


def test_ncc_location_maps_jabi_from_customer_address():
    assert reports._map_ncc_location("53 Alex Ekwueme Way, Jabi") == (
        "Municipal Area Council",
        "Jabi",
        "FEDERAL CAPITAL TERRITORY",
    )


def test_ncc_location_uses_region_when_address_has_no_accepted_town_match():
    ticket = SimpleNamespace(
        region="Gudu",
        customer=None,
        subscriber=SimpleNamespace(
            service_city="",
            service_region="",
            service_address_line1="12 Example Street, Unknown Area, Abuja FCT",
            service_address_line2="",
        ),
    )

    assert reports._ticket_ncc_location(ticket) == (
        "Municipal Area Council",
        "Gudu",
        "FEDERAL CAPITAL TERRITORY",
    )


def test_ncc_location_matches_rightmost_accepted_town_in_address():
    assert reports._map_ncc_location(
        "Flat 201, Block D26, CBN Estate by Apo Bridge, 43 Birni Kebbi crescent, Garki 2 Abuja"
    ) == (
        "Municipal Area Council",
        "Garki",
        "FEDERAL CAPITAL TERRITORY",
    )


def test_ncc_ticket_location_prefers_subscriber_address_over_ticket_region():
    ticket = SimpleNamespace(
        region="Gudu",
        customer=None,
        subscriber=SimpleNamespace(
            service_city="",
            service_region="",
            service_address_line1="10 Clement Akpamgbo Close, Guzape",
            service_address_line2="",
        ),
    )

    assert reports._ticket_ncc_location(ticket) == (
        "Municipal Area Council",
        "Guzape",
        "FEDERAL CAPITAL TERRITORY",
    )


def test_ncc_ticket_location_does_not_use_ticket_region_as_customer_town():
    ticket = SimpleNamespace(
        region="Gudu",
        customer=None,
        subscriber=SimpleNamespace(
            service_city="",
            service_region="",
            service_address_line1="",
            service_address_line2="",
        ),
    )

    assert reports._ticket_ncc_location(ticket) == (
        "Municipal Area Council",
        "Gudu",
        "FEDERAL CAPITAL TERRITORY",
    )


def test_ncc_workbook_colors_rows_by_validation_status():
    workbook = reports._build_ncc_workbook(
        [
            {"VALIDATION STATUS": "[OK] All validations passed", "First Name": "Ada"},
            {"VALIDATION STATUS": "[FAIL] MSISDN must start with 234 (col A)", "First Name": "Bola"},
        ],
        reports._NCC_COLUMNS,
    )

    with ZipFile(BytesIO(workbook)) as archive:
        sheet_xml = archive.read("xl/worksheets/sheet1.xml")
        styles_xml = archive.read("xl/styles.xml")

    assert b's="10"' in sheet_xml
    assert b's="11"' in sheet_xml
    assert b'cellXfs count="12"' in styles_xml
