from app.logic.private_note_logic import LogicService, PrivateNoteContext


def test_private_note_allowed():
    logic = LogicService()
    ctx = PrivateNoteContext(
        body="Customer requested an update.",
        is_system_conversation=False,
        author_is_admin=True,
        requested_visibility="admins",
    )
    decision = logic.decide_create_note(ctx)
    assert decision.status == "allow"
    assert decision.visibility == "admins"
    assert decision.reason is None


def test_private_note_denied_empty_body():
    logic = LogicService()
    ctx = PrivateNoteContext(
        body="   ",
        is_system_conversation=False,
        author_is_admin=True,
        requested_visibility="team",
    )
    decision = logic.decide_create_note(ctx)
    assert decision.status == "deny"
    assert decision.visibility is None
    assert "empty" in (decision.reason or "")


def test_private_note_denied_system_conversation():
    logic = LogicService()
    ctx = PrivateNoteContext(
        body="This should not be allowed.",
        is_system_conversation=True,
        author_is_admin=True,
        requested_visibility="team",
    )
    decision = logic.decide_create_note(ctx)
    assert decision.status == "deny"
    assert decision.visibility is None
    assert "system" in (decision.reason or "")


def test_private_note_visibility_normalized_for_non_admin():
    logic = LogicService()
    ctx = PrivateNoteContext(
        body="Only admins requested this.",
        is_system_conversation=False,
        author_is_admin=False,
        requested_visibility="admins",
    )
    decision = logic.decide_create_note(ctx)
    assert decision.status == "allow"
    assert decision.visibility == "team"
