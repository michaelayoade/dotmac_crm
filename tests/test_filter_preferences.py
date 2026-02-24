from app.services import filter_preferences as preferences


def test_save_and_get_preference(db_session, person):
    preferences.save_preference(
        db_session,
        person.id,
        preferences.TICKETS_PAGE.key,
        {"status": "open", "filters": '[["Ticket","priority","=","high"]]'},
    )

    saved = preferences.get_preference(db_session, person.id, preferences.TICKETS_PAGE.key)

    assert saved == {
        "status": "open",
        "filters": '[["Ticket","priority","=","high"]]',
    }


def test_save_empty_state_clears_preference(db_session, person):
    preferences.save_preference(
        db_session,
        person.id,
        preferences.PROJECTS_PAGE.key,
        {"status": "active"},
    )

    preferences.save_preference(
        db_session,
        person.id,
        preferences.PROJECTS_PAGE.key,
        {"status": " "},
    )

    assert preferences.get_preference(db_session, person.id, preferences.PROJECTS_PAGE.key) is None


def test_managed_state_extraction_and_merge():
    query = {
        "status": "open",
        "search": "outage",
        "page": "2",
        "per_page": "50",
    }

    assert preferences.has_managed_params(query, preferences.TICKETS_PAGE) is True

    state = preferences.extract_managed_state(query, preferences.TICKETS_PAGE)
    assert state == {"search": "outage", "status": "open", "per_page": "50"}

    merged = preferences.merge_query_with_state({"notice": "1"}, preferences.TICKETS_PAGE, state)
    assert merged == {
        "notice": "1",
        "search": "outage",
        "status": "open",
        "per_page": "50",
    }
