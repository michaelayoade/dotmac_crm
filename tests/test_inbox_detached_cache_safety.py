"""Regression tests for inbox cache detached-instance safety."""

from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy import inspect as sa_inspect

from app.models.crm.comments import SocialComment, SocialCommentPlatform
from app.models.crm.conversation import Conversation
from app.models.person import Person
from app.services.crm.inbox import cache as inbox_cache
from app.services.crm.inbox import listing as inbox_listing
from app.services.crm.inbox.comments_context import CommentsContext, load_comments_context


def _clear_inbox_cache() -> None:
    inbox_cache.invalidate_prefix("")


def _new_person() -> Person:
    suffix = uuid.uuid4().hex
    return Person(
        first_name="Inbox",
        last_name="Cache",
        email=f"inbox-cache-{suffix}@example.com",
    )


def _run_in_thread(coro):
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()


def test_load_comments_context_cache_hit_rehydrates_comment_models(db_session):
    async def _run() -> None:
        _clear_inbox_cache()
        person = _new_person()
        db_session.add(person)
        db_session.flush()
        comment = SocialComment(
            platform=SocialCommentPlatform.facebook,
            external_id=f"ext-{uuid.uuid4().hex}",
            external_post_id=f"post-{uuid.uuid4().hex}",
            source_account_id="page-1",
            author_id="author-1",
            author_name="Author One",
            message="hello from cached comment",
            created_time=datetime.now(UTC),
            is_active=True,
        )
        db_session.add(comment)
        db_session.commit()
        db_session.refresh(comment)

        first = await load_comments_context(
            db_session,
            search=None,
            comment_id=str(comment.id),
            offset=0,
            limit=25,
            fetch=False,
            target_id=None,
            include_thread=False,
        )
        assert first.selected_comment is not None
        assert len(first.grouped_comments) == 1

        db_session.expunge_all()

        second = await load_comments_context(
            db_session,
            search=None,
            comment_id=str(comment.id),
            offset=0,
            limit=25,
            fetch=False,
            target_id=None,
            include_thread=False,
        )

        assert second.selected_comment is not None
        assert str(second.selected_comment.id) == str(comment.id)
        assert sa_inspect(second.selected_comment).detached is False
        assert second.grouped_comments
        assert str(second.grouped_comments[0]["comment"].id) == str(comment.id)

    _run_in_thread(_run())


def test_load_inbox_list_cache_hit_rehydrates_conversation_models(db_session, monkeypatch):
    async def _run() -> None:
        _clear_inbox_cache()
        person = _new_person()
        db_session.add(person)
        db_session.flush()
        conversation = Conversation(person_id=person.id)
        db_session.add(conversation)
        db_session.commit()
        db_session.refresh(conversation)

        async def _fake_comments_context(*_args, **_kwargs):
            return CommentsContext(
                grouped_comments=[],
                selected_comment=None,
                comment_replies=[],
                offset=0,
                limit=0,
                has_more=False,
                next_offset=None,
            )

        calls = {"count": 0}

        def _fake_list_inbox_conversations(*_args, **_kwargs):
            calls["count"] += 1
            if calls["count"] > 1:
                raise AssertionError("expected cache hit for second load_inbox_list call")
            return [
                (
                    conversation,
                    {"body": "Latest", "channel_type": None},
                    0,
                    None,
                )
            ]

        monkeypatch.setattr(inbox_listing, "reopen_due_snoozed_conversations", lambda _db: None)
        monkeypatch.setattr(inbox_listing, "load_comments_context", _fake_comments_context)
        monkeypatch.setattr(inbox_listing, "list_inbox_conversations", _fake_list_inbox_conversations)

        first = await inbox_listing.load_inbox_list(
            db_session,
            channel=None,
            status=None,
            outbox_status=None,
            search=None,
            assignment=None,
            assigned_person_id=None,
            target_id=None,
            filter_agent_id=None,
            assigned_from=None,
            assigned_to=None,
            sort_by=None,
            missing=None,
            offset=0,
            limit=25,
            include_thread=False,
            fetch_comments=False,
        )
        assert len(first.conversations_raw) == 1

        db_session.expunge_all()

        second = await inbox_listing.load_inbox_list(
            db_session,
            channel=None,
            status=None,
            outbox_status=None,
            search=None,
            assignment=None,
            assigned_person_id=None,
            target_id=None,
            filter_agent_id=None,
            assigned_from=None,
            assigned_to=None,
            sort_by=None,
            missing=None,
            offset=0,
            limit=25,
            include_thread=False,
            fetch_comments=False,
        )

        assert len(second.conversations_raw) == 1
        hydrated_conversation = second.conversations_raw[0][0]
        assert str(hydrated_conversation.id) == str(conversation.id)
        assert sa_inspect(hydrated_conversation).detached is False

    _run_in_thread(_run())
