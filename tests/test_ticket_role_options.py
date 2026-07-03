"""Ticket manager/assistant dropdown options sourced from NOC/SPC roles."""

from types import SimpleNamespace

from app.models.person import Person
from app.models.rbac import PersonRole, Role
from app.web.admin.tickets import _ticket_role_options


def _make_person(db_session, first_name, last_name, job_title=None):
    person = Person(
        first_name=first_name,
        last_name=last_name,
        email=f"{first_name}.{last_name}@example.test".lower(),
        job_title=job_title,
    )
    db_session.add(person)
    db_session.commit()
    return person


def _grant_role(db_session, person, role_name, role_active=True):
    role = db_session.query(Role).filter(Role.name == role_name).first()
    if role is None:
        role = Role(name=role_name, is_active=role_active)
        db_session.add(role)
        db_session.commit()
    db_session.add(PersonRole(person_id=person.id, role_id=role.id))
    db_session.commit()
    return role


def test_includes_noc_and_spc_role_holders(db_session):
    noc = _make_person(db_session, "Ngozi", "Okeke", job_title="NOC Engineer")
    spc = _make_person(db_session, "Sade", "Adewale")
    _grant_role(db_session, noc, "NOC")  # matched case-insensitively
    _grant_role(db_session, spc, "spc")

    options = _ticket_role_options(db_session, technicians=[])

    labels = {item["label"] for item in options}
    assert "Ngozi Okeke - NOC Engineer" in labels
    assert "Sade Adewale" in labels


def test_excludes_people_without_ticket_roles_and_inactive_roles(db_session):
    unrelated = _make_person(db_session, "Uche", "Eze")
    _grant_role(db_session, unrelated, "billing")
    retired = _make_person(db_session, "Rotimi", "Balogun")
    _grant_role(db_session, retired, "noc-legacy", role_active=False)

    options = _ticket_role_options(db_session, technicians=[])

    ids = {item["person_id"] for item in options}
    assert str(unrelated.id) not in ids
    assert str(retired.id) not in ids


def test_technicians_and_selected_people_are_included_and_deduped(db_session):
    tech_person = _make_person(db_session, "Tayo", "Ade")
    selected = _make_person(db_session, "Bisi", "Ola")
    both = _make_person(db_session, "Kemi", "Obi")
    _grant_role(db_session, both, "noc")

    technicians = [SimpleNamespace(person=tech_person), SimpleNamespace(person=both), SimpleNamespace(person=None)]
    options = _ticket_role_options(
        db_session,
        technicians,
        selected_person_ids=(str(selected.id), None, ""),
    )

    ids = [item["person_id"] for item in options]
    assert str(tech_person.id) in ids
    assert str(selected.id) in ids
    assert ids.count(str(both.id)) == 1


def test_options_sorted_by_display_name(db_session):
    zed = _make_person(db_session, "Zed", "Zulu")
    amy = _make_person(db_session, "Amy", "Abara")
    _grant_role(db_session, zed, "noc")
    _grant_role(db_session, amy, "spc")

    options = _ticket_role_options(db_session, technicians=[])

    sort_labels = [item["sort_label"] for item in options]
    assert sort_labels == sorted(sort_labels)
