import pytest

from app.models.person import Person
from app.models.tickets import TicketComment, TicketStatus
from app.schemas.tickets import TicketCommentCreate, TicketCreate
from app.services import tickets as tickets_service


def _person(db_session, email: str) -> Person:
    person = Person(first_name="Test", last_name="Agent", email=email)
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)
    return person


def test_merge_tickets_copies_core_records_and_marks_source(db_session):
    source_author = _person(db_session, "source-agent@example.com")
    target_author = _person(db_session, "target-agent@example.com")

    target = tickets_service.tickets.create(
        db_session,
        TicketCreate(
            title="Cabinet outage",
            tags=["outage"],
            metadata_={"attachments": [{"stored_name": "target.pdf", "file_name": "target.pdf"}]},
            created_by_person_id=target_author.id,
            assigned_to_person_ids=[target_author.id],
        ),
    )
    source = tickets_service.tickets.create(
        db_session,
        TicketCreate(
            title="Customer complaint",
            tags=["customer-impact"],
            metadata_={"attachments": [{"stored_name": "source.pdf", "file_name": "source.pdf"}]},
            created_by_person_id=source_author.id,
            assigned_to_person_ids=[source_author.id],
        ),
    )
    tickets_service.ticket_comments.create(
        db_session,
        TicketCommentCreate(
            ticket_id=source.id,
            author_person_id=source_author.id,
            body="Customer says service is down",
            attachments=[{"stored_name": "comment.txt", "file_name": "comment.txt"}],
        ),
    )

    merged_ticket = tickets_service.tickets.merge(
        db_session,
        source_ticket_id=str(source.id),
        target_ticket_id=str(target.id),
        actor_id=str(target_author.id),
        reason="Duplicate ticket",
    )

    db_session.refresh(source)
    db_session.refresh(target)

    assert merged_ticket.id == target.id
    assert source.status == TicketStatus.merged
    assert source.merged_into_ticket_id == target.id
    assert set(target.tags or []) == {"outage", "customer-impact"}
    assert {str(assignee.person_id) for assignee in target.assignees} == {str(source_author.id), str(target_author.id)}

    attachment_names = {
        item.get("stored_name") for item in (target.metadata_ or {}).get("attachments", []) if isinstance(item, dict)
    }
    assert attachment_names == {"source.pdf", "target.pdf"}

    merged_comments = db_session.query(TicketComment).filter(TicketComment.ticket_id == target.id).all()
    assert any(comment.body == "Customer says service is down" for comment in merged_comments)
    assert any("Merged ticket" in comment.body for comment in merged_comments)


def test_link_related_outage_replaces_existing_parent_and_builds_context(db_session):
    actor = _person(db_session, "actor@example.com")
    first_outage = tickets_service.tickets.create(db_session, TicketCreate(title="Outage A"))
    second_outage = tickets_service.tickets.create(db_session, TicketCreate(title="Outage B"))
    child = tickets_service.tickets.create(db_session, TicketCreate(title="Affected customer"))
    sibling = tickets_service.tickets.create(db_session, TicketCreate(title="Another affected customer"))

    tickets_service.tickets.link_related_outage(
        db_session,
        from_ticket_id=str(child.id),
        to_ticket_id=str(first_outage.id),
        actor_id=str(actor.id),
    )
    tickets_service.tickets.link_related_outage(
        db_session,
        from_ticket_id=str(sibling.id),
        to_ticket_id=str(second_outage.id),
        actor_id=str(actor.id),
    )
    tickets_service.tickets.link_related_outage(
        db_session,
        from_ticket_id=str(child.id),
        to_ticket_id=str(second_outage.id),
        actor_id=str(actor.id),
    )

    context = tickets_service.tickets.related_outage_context(db_session, ticket_id=str(child.id))

    assert context["primary_ticket"].id == second_outage.id
    assert {ticket.id for ticket in context["sibling_tickets"]} == {sibling.id}


def test_comment_create_rejects_merged_ticket(db_session):
    actor = _person(db_session, "merge-actor@example.com")
    survivor = tickets_service.tickets.create(db_session, TicketCreate(title="Primary"))
    source = tickets_service.tickets.create(db_session, TicketCreate(title="Duplicate"))
    tickets_service.tickets.merge(
        db_session,
        source_ticket_id=str(source.id),
        target_ticket_id=str(survivor.id),
        actor_id=str(actor.id),
    )

    with pytest.raises(Exception) as exc_info:
        tickets_service.ticket_comments.create(
            db_session,
            TicketCommentCreate(
                ticket_id=source.id,
                author_person_id=actor.id,
                body="Should not be allowed",
            ),
        )
    assert "merged" in str(exc_info.value).lower()
