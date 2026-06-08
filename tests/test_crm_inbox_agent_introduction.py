from app.models.person import Person
from app.services.crm.inbox.agent_introduction import (
    DEFAULT_INTRODUCTION_TEMPLATE,
    INTRODUCTION_TEMPLATE_METADATA_KEY,
    get_introduction_template,
    render_introduction_template,
    save_introduction_template,
    validate_introduction_template,
)


def _current_user(person: Person) -> dict:
    return {
        "id": str(person.id),
        "person_id": str(person.id),
        "name": f"{person.first_name} {person.last_name}".strip(),
    }


def test_default_introduction_renders_display_name(db_session):
    person = Person(
        first_name="Fiyin",
        last_name="Ade",
        display_name="Fiyin",
        email="fiyin@example.com",
    )
    db_session.add(person)
    db_session.commit()

    rendered = render_introduction_template(db_session, _current_user(person))

    assert rendered == "Hi, my name is Fiyin and I will be assisting you today."


def test_introduction_name_falls_back_to_full_name(db_session):
    person = Person(
        first_name="Fiyin",
        last_name="Ade",
        display_name=None,
        email="fiyin-full@example.com",
    )
    db_session.add(person)
    db_session.commit()

    rendered = render_introduction_template(db_session, _current_user(person))

    assert rendered == "Hi, my name is Fiyin Ade and I will be assisting you today."


def test_introduction_name_falls_back_gracefully(db_session):
    rendered = render_introduction_template(db_session, {"id": "", "person_id": "", "name": "Unknown User"})

    assert rendered == "Hi, my name is your support agent and I will be assisting you today."


def test_custom_introduction_template_is_saved_and_rendered(db_session):
    person = Person(
        first_name="Fiyin",
        last_name="Ade",
        display_name="Fiyin",
        email="fiyin-custom@example.com",
    )
    db_session.add(person)
    db_session.commit()

    result = save_introduction_template(
        db_session,
        _current_user(person),
        "Good day, my name is {agent_name} and I will be handling your request.",
    )

    db_session.refresh(person)
    assert result.ok
    assert (
        person.metadata_[INTRODUCTION_TEMPLATE_METADATA_KEY]
        == "Good day, my name is {agent_name} and I will be handling your request."
    )
    assert render_introduction_template(db_session, _current_user(person)) == (
        "Good day, my name is Fiyin and I will be handling your request."
    )


def test_default_template_clears_custom_metadata(db_session):
    person = Person(
        first_name="Fiyin",
        last_name="Ade",
        display_name="Fiyin",
        email="fiyin-default@example.com",
        metadata_={INTRODUCTION_TEMPLATE_METADATA_KEY: "Hello, my name is {agent_name}."},
    )
    db_session.add(person)
    db_session.commit()

    result = save_introduction_template(db_session, _current_user(person), DEFAULT_INTRODUCTION_TEMPLATE)

    db_session.refresh(person)
    assert result.ok
    assert INTRODUCTION_TEMPLATE_METADATA_KEY not in person.metadata_
    assert get_introduction_template(db_session, _current_user(person)) == DEFAULT_INTRODUCTION_TEMPLATE


def test_introduction_template_rejects_unsupported_variables():
    result = validate_introduction_template("Hi {agent_name}, customer is {customer_name}.")

    assert not result.ok
    assert "Only {agent_name} is supported" in result.error_detail


def test_message_thread_has_insert_introduction_button():
    with open("templates/admin/crm/_message_thread.html", encoding="utf-8") as template_file:
        template = template_file.read()

    assert "Insert Introduction" in template
    assert "insertIntroduction()" in template
    assert "data-introduction-text" in template
