from app.models.person import Person, PersonStatus
from app.web.admin.system import _build_changed_person_update_payload


def test_build_changed_person_update_payload_skips_unchanged_normalized_profile_fields():
    person = Person(
        first_name="Jane",
        last_name="Doe",
        display_name="Jane D",
        email="jane@example.com",
        phone="+2348012345678",
        is_active=True,
        status=PersonStatus.active,
    )

    payload = _build_changed_person_update_payload(
        person=person,
        first_name="Jane",
        last_name="Doe",
        display_name="Jane D",
        email=" Jane@Example.com ",
        phone="+234 801 234 5678",
        is_active=True,
        status="active",
    )

    assert payload is None


def test_build_changed_person_update_payload_only_includes_changed_fields():
    person = Person(
        first_name="Jane",
        last_name="Doe",
        display_name="Jane D",
        email="jane@example.com",
        phone="+2348012345678",
        is_active=True,
        status=PersonStatus.active,
    )

    payload = _build_changed_person_update_payload(
        person=person,
        first_name="Janet",
        last_name="Doe",
        display_name="",
        email="janet@example.com",
        phone=None,
        is_active=False,
        status="inactive",
    )

    assert payload is not None
    assert payload.model_dump(exclude_unset=True) == {
        "first_name": "Janet",
        "display_name": None,
        "email": "janet@example.com",
        "phone": None,
        "is_active": False,
        "status": "inactive",
    }
