from types import SimpleNamespace
from uuid import uuid4

from app.models.person import Person
from app.models.tickets import TicketLink, TicketMerge, TicketStatus
from app.schemas.tickets import TicketCommentCreate, TicketCreate
from app.services import tickets as tickets_service
from app.web import admin as admin_web
from app.web.admin import tickets as tickets_web


class _FakeRequest:
    def __init__(self, path: str):
        self.headers = {}
        self.url = SimpleNamespace(path=path)
        self.client = SimpleNamespace(host="127.0.0.1")
        self.state = SimpleNamespace()


def test_ticket_merge_moves_activity_and_marks_source_merged(db_session, person):
    second_person = Person(first_name="Second", last_name="Agent", email=f"second-{uuid4().hex}@example.com")
    db_session.add(second_person)
    db_session.commit()
    db_session.refresh(second_person)

    target = tickets_service.tickets.create(
        db_session,
        TicketCreate(
            title="Primary outage",
            assigned_to_person_id=person.id,
            metadata_={"attachments": [{"stored_name": "target.png", "key": "target", "url": "/target"}]},
        ),
    )
    source = tickets_service.tickets.create(
        db_session,
        TicketCreate(
            title="Duplicate outage",
            assigned_to_person_id=second_person.id,
            metadata_={"attachments": [{"stored_name": "source.png", "key": "source", "url": "/source"}]},
            tags=["dup"],
        ),
    )
    tickets_service.ticket_comments.create(
        db_session,
        TicketCommentCreate(ticket_id=source.id, author_person_id=person.id, body="Source comment"),
    )

    merged = tickets_service.tickets.merge(
        db_session,
        source_ticket_id=str(source.id),
        target_ticket_id=str(target.id),
        actor_id=str(person.id),
        reason="Duplicate customer report",
    )

    db_session.refresh(source)
    db_session.refresh(merged)

    assert source.status == TicketStatus.merged
    assert source.merged_into_ticket_id == merged.id
    assert {str(assignee.person_id) for assignee in merged.assignees} == {str(person.id), str(second_person.id)}
    assert sorted(item["stored_name"] for item in merged.metadata_["attachments"]) == ["source.png", "target.png"]
    assert db_session.query(TicketMerge).filter(TicketMerge.source_ticket_id == source.id).count() == 1
    copied_comments = [comment.body for comment in merged.comments]
    assert "Source comment" in copied_comments
    assert any("Merged ticket" in body for body in copied_comments)


def test_ticket_link_related_outage_upserts_single_relationship(db_session, person):
    outage = tickets_service.tickets.create(db_session, TicketCreate(title="Area outage"))
    child = tickets_service.tickets.create(db_session, TicketCreate(title="Subscriber impact"))
    replacement = tickets_service.tickets.create(db_session, TicketCreate(title="Replacement outage"))

    first_link = tickets_service.tickets.link_related_outage(
        db_session,
        from_ticket_id=str(child.id),
        to_ticket_id=str(outage.id),
        actor_id=str(person.id),
    )
    second_link = tickets_service.tickets.link_related_outage(
        db_session,
        from_ticket_id=str(child.id),
        to_ticket_id=str(replacement.id),
        actor_id=str(person.id),
    )

    db_session.refresh(child)
    assert first_link.id == second_link.id
    assert second_link.to_ticket_id == replacement.id
    assert db_session.query(TicketLink).filter(TicketLink.from_ticket_id == child.id).count() == 1


def test_status_update_redirects_merged_ticket_to_canonical(db_session, person, monkeypatch):
    target = tickets_service.tickets.create(db_session, TicketCreate(title="Canonical"))
    source = tickets_service.tickets.create(db_session, TicketCreate(title="To merge"))
    tickets_service.tickets.merge(
        db_session,
        source_ticket_id=str(source.id),
        target_ticket_id=str(target.id),
        actor_id=str(person.id),
    )

    monkeypatch.setattr(admin_web, "get_current_user", lambda _request: {"person_id": str(person.id)})
    request = _FakeRequest(f"/admin/support/tickets/{source.id}/status")

    response = tickets_web.update_ticket_status(
        request,
        str(source.id),
        status="open",
        db=db_session,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/admin/support/tickets/{target.number or target.id}?merged_from={source.number or source.id}"
    )


def test_ticket_list_relationship_map_includes_merged_and_linked_markers(db_session, person):
    primary = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Primary outage", number="TKT-PRIMARY"),
    )
    merged_target = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Canonical merge target", number="TKT-TARGET"),
    )
    merged_source = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Merged source", number="TKT-MERGED"),
    )
    linked_child = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Linked child", number="TKT-CHILD"),
    )

    tickets_service.tickets.merge(
        db_session,
        source_ticket_id=str(merged_source.id),
        target_ticket_id=str(merged_target.id),
        actor_id=str(person.id),
    )
    tickets_service.tickets.link_related_outage(
        db_session,
        from_ticket_id=str(linked_child.id),
        to_ticket_id=str(primary.id),
        actor_id=str(person.id),
    )

    merged_source = tickets_service.tickets.get(db_session, str(merged_source.id))
    merged_target = tickets_service.tickets.get(db_session, str(merged_target.id))
    primary = tickets_service.tickets.get(db_session, str(primary.id))
    linked_child = tickets_service.tickets.get(db_session, str(linked_child.id))

    relationship_map = tickets_web._build_ticket_relationships_map(
        db_session,
        [merged_source, merged_target, primary, linked_child],
    )

    assert relationship_map[str(merged_source.id)]["merged_into_ref"] == "TKT-TARGET"
    assert relationship_map[str(linked_child.id)]["linked_to_ref"] == "TKT-PRIMARY"
    assert relationship_map[str(primary.id)]["linked_children_count"] == 1
