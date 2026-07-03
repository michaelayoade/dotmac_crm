from app.web.admin import reports


def test_ncc_msisdn_formats_nigerian_numbers_without_plus_prefix():
    assert reports._complete_ncc_msisdn_or_empty("+2348012345678") == "2348012345678"
    assert reports._complete_ncc_msisdn_or_empty("08012345678") == "2348012345678"
    assert reports._complete_ncc_msisdn_or_empty("8012345678") == "2348012345678"
    assert reports._complete_ncc_msisdn_or_empty("07035133928 08022891990") == "2347035133928"
    assert reports._complete_ncc_msisdn_or_empty("+23407065008473") == "2347065008473"


def test_ncc_ticket_msisdn_uses_phone_channel_when_primary_phone_is_blank():
    person = type("Person", (), {"phone": ""})()
    channels = [type("Channel", (), {"channel_type": "whatsapp", "address": "08012345678"})()]

    assert reports._ticket_msisdn(person, channels) == "2348012345678"


def test_ncc_ticket_msisdn_uses_exact_display_name_match_when_phone_is_blank():
    person = type(
        "Person",
        (),
        {
            "phone": "",
            "display_name": "HARUNA B. KWAGHE",
            "first_name": "HARUNA",
            "last_name": "B. KWAGHE",
            "email": "haruna.kwaghe@nrs.gov.ng",
        },
    )()
    matched_person = type("Person", (), {"phone": "07037476363"})()
    ticket = type("Ticket", (), {"subscriber": None})()

    assert (
        reports._ticket_msisdn_from_exact_person_matches(
            ticket,
            person,
            {"haruna b kwaghe": [person, matched_person]},
            {},
        )
        == "2347037476363"
    )


def test_ncc_ticket_msisdn_exact_display_match_ignores_honorifics():
    subscriber = type(
        "Subscriber",
        (),
        {
            "display_name": "Dr Michael Odokoro",
            "subscriber_number": "Dr Michael Odokoro",
            "external_id": "Dr Michael Odokoro",
            "organization": None,
        },
    )()
    person = type(
        "Person",
        (),
        {
            "phone": "",
            "display_name": "Michael Odokoro",
            "first_name": "Michael",
            "last_name": "Odokoro",
            "email": "",
        },
    )()
    matched_person = type("Person", (), {"phone": "+2347063600444"})()
    ticket = type("Ticket", (), {"subscriber": subscriber})()

    assert (
        reports._ticket_msisdn_from_exact_person_matches(
            ticket,
            person,
            {"michael odokoro": [matched_person]},
            {},
        )
        == "2347063600444"
    )


def test_ncc_ticket_msisdn_uses_exact_email_match_when_phone_is_blank():
    person = type(
        "Person",
        (),
        {
            "phone": "",
            "display_name": "Mariagoretti.Onyeka",
            "first_name": "Mariagoretti",
            "last_name": "Onyeka",
            "email": "mariagoretti.onyeka@ntel.com.ng",
        },
    )()
    matched_person = type("Person", (), {"phone": "08064975952"})()
    ticket = type("Ticket", (), {"subscriber": None})()

    assert (
        reports._ticket_msisdn_from_exact_person_matches(
            ticket,
            person,
            {},
            {"mariagoretti.onyeka@ntel.com.ng": [matched_person]},
        )
        == "2348064975952"
    )


def test_ncc_msisdn_allows_compact_alphanumeric_device_ids():
    assert reports._complete_ncc_msisdn_or_empty("isp onu-42") == "ISPONU42"
    assert reports._complete_ncc_msisdn_or_empty("DEV-AB12") == "DEVAB12"


def test_ncc_msisdn_rejects_incomplete_or_non_ncc_values():
    assert reports._complete_ncc_msisdn_or_empty("+234801234") == ""
    assert reports._complete_ncc_msisdn_or_empty("080123") == ""
    assert reports._complete_ncc_msisdn_or_empty("not available") == ""
