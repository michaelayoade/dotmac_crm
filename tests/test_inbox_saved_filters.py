from app.services.crm.inbox import saved_filters


def test_save_and_list_saved_inbox_filters(db_session, person):
    created = saved_filters.save_saved_filter(
        db_session,
        person.id,
        name="Urgent Open",
        params={
            "status": "open",
            "search": "urgent",
            "assignment": "",
        },
    )
    assert created is not None
    items = saved_filters.list_saved_filters(db_session, person.id)
    assert len(items) == 1
    assert items[0]["name"] == "Urgent Open"
    assert items[0]["params"] == {"search": "urgent", "status": "open"}


def test_get_and_delete_saved_inbox_filter(db_session, person):
    created = saved_filters.save_saved_filter(
        db_session,
        person.id,
        name="Pending Team",
        params={"status": "pending", "assignment": "my_team"},
    )
    assert created is not None
    filter_id = created["id"]

    item = saved_filters.get_saved_filter(db_session, person.id, filter_id)
    assert item is not None
    assert item["name"] == "Pending Team"

    deleted = saved_filters.delete_saved_filter(db_session, person.id, filter_id)
    assert deleted is True
    assert saved_filters.get_saved_filter(db_session, person.id, filter_id) is None


def test_merge_query_with_saved_filter_replaces_managed_keys():
    merged = saved_filters.merge_query_with_saved_filter(
        {"status": "resolved", "notice": "1"},
        {"status": "open", "search": "outage"},
    )
    assert merged == {"notice": "1", "status": "open", "search": "outage"}
