from app.services.crm.inbox.permissions import (
    can_manage_inbox_settings,
    can_view_inbox_settings,
    can_view_private_note,
    is_admin,
)


def test_is_admin_by_role():
    assert is_admin(roles=["admin"])
    assert not is_admin(roles=["agent"])


def test_can_view_private_note_author():
    assert can_view_private_note(
        visibility="author",
        author_id="user-1",
        actor_id="user-1",
        roles=[],
    )
    assert not can_view_private_note(
        visibility="author",
        author_id="user-1",
        actor_id="user-2",
        roles=[],
    )


def test_can_view_private_note_admin():
    assert can_view_private_note(
        visibility="admins",
        author_id="user-1",
        actor_id="user-2",
        roles=["admin"],
    )


def test_can_view_private_note_team_default():
    assert can_view_private_note(
        visibility="team",
        author_id="user-1",
        actor_id="user-2",
        roles=[],
    )


def test_can_view_inbox_settings():
    assert can_view_inbox_settings(roles=["admin"])
    assert can_view_inbox_settings(scopes=["crm:inbox:settings:read"])
    assert not can_view_inbox_settings(scopes=["other:read"])


def test_can_manage_inbox_settings():
    assert can_manage_inbox_settings(roles=["admin"])
    assert can_manage_inbox_settings(scopes=["crm:inbox:settings:write"])
    assert not can_manage_inbox_settings(scopes=["crm:inbox:settings:read"])
