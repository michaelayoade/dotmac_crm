"""Tests for social comment reply flow."""

import asyncio
import concurrent.futures
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qsl, urlparse

import pytest
from starlette.requests import Request

from app.models.crm.comments import SocialComment, SocialCommentPlatform, SocialCommentReply


def _run_async(coro):
    """Run an async coroutine in a sync test, safely isolated from running loops."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


@pytest.fixture()
def fb_comment(db_session):
    comment = SocialComment(
        platform=SocialCommentPlatform.facebook,
        external_id="fb_comment_123",
        external_post_id="fb_post_456",
        source_account_id="page_789",
        author_id="user_111",
        author_name="Alice",
        message="Hello from Facebook!",
        created_time=datetime.now(UTC),
    )
    db_session.add(comment)
    db_session.commit()
    db_session.refresh(comment)
    return comment


@pytest.fixture()
def ig_comment(db_session):
    comment = SocialComment(
        platform=SocialCommentPlatform.instagram,
        external_id="ig_comment_123",
        external_post_id="ig_media_456",
        source_account_id="ig_account_789",
        author_id="alice_ig",
        author_name="alice_ig",
        message="Hello from Instagram!",
        created_time=datetime.now(UTC),
    )
    db_session.add(comment)
    db_session.commit()
    db_session.refresh(comment)
    return comment


# ---------------------------------------------------------------------------
# Model relationship tests
# ---------------------------------------------------------------------------


class TestSocialCommentModel:
    def test_comment_has_replies_relationship(self, db_session, fb_comment):
        reply = SocialCommentReply(
            comment_id=fb_comment.id,
            platform=SocialCommentPlatform.facebook,
            external_id="reply_001",
            message="Thanks!",
            created_time=datetime.now(UTC),
        )
        db_session.add(reply)
        db_session.commit()
        db_session.refresh(fb_comment)
        assert len(fb_comment.replies) == 1
        assert fb_comment.replies[0].message == "Thanks!"

    def test_reply_has_comment_backref(self, db_session, fb_comment):
        reply = SocialCommentReply(
            comment_id=fb_comment.id,
            platform=SocialCommentPlatform.facebook,
            external_id="reply_002",
            message="Reply text",
            created_time=datetime.now(UTC),
        )
        db_session.add(reply)
        db_session.commit()
        db_session.refresh(reply)
        assert reply.comment.id == fb_comment.id

    def test_reply_stores_author_fields(self, db_session, fb_comment):
        reply = SocialCommentReply(
            comment_id=fb_comment.id,
            platform=SocialCommentPlatform.facebook,
            external_id="reply_003",
            author_id="agent_42",
            author_name="Support Agent",
            message="We'll help!",
            created_time=datetime.now(UTC),
        )
        db_session.add(reply)
        db_session.commit()
        db_session.refresh(reply)
        assert reply.author_id == "agent_42"
        assert reply.author_name == "Support Agent"


# ---------------------------------------------------------------------------
# Service layer tests
# ---------------------------------------------------------------------------


class TestReplyToSocialComment:
    @patch("app.services.meta_pages.reply_to_comment", new_callable=AsyncMock)
    def test_facebook_reply_calls_api_and_stores(self, mock_reply, db_session, fb_comment):
        mock_reply.return_value = {"id": "fb_reply_ext_id"}
        from app.services.crm.conversations.comments import reply_to_social_comment

        reply = _run_async(reply_to_social_comment(db_session, fb_comment, "Thank you!"))

        mock_reply.assert_awaited_once_with(
            db_session,
            page_id="page_789",
            comment_id="fb_comment_123",
            message="Thank you!",
        )
        assert reply.external_id == "fb_reply_ext_id"
        assert reply.message == "Thank you!"
        assert reply.platform == SocialCommentPlatform.facebook
        assert reply.comment_id == fb_comment.id

    @patch("app.services.meta_pages.reply_to_instagram_comment", new_callable=AsyncMock)
    def test_instagram_reply_calls_api_and_stores(self, mock_reply, db_session, ig_comment):
        mock_reply.return_value = {"id": "ig_reply_ext_id"}
        from app.services.crm.conversations.comments import reply_to_social_comment

        reply = _run_async(reply_to_social_comment(db_session, ig_comment, "Thanks!"))

        mock_reply.assert_awaited_once_with(
            db_session,
            ig_account_id="ig_account_789",
            comment_id="ig_comment_123",
            message="Thanks!",
        )
        assert reply.external_id == "ig_reply_ext_id"
        assert reply.platform == SocialCommentPlatform.instagram

    @patch("app.services.meta_pages.reply_to_comment", new_callable=AsyncMock)
    def test_reply_stores_author_fields(self, mock_reply, db_session, fb_comment):
        mock_reply.return_value = {"id": "fb_reply_ext_id_2"}
        from app.services.crm.conversations.comments import reply_to_social_comment

        reply = _run_async(
            reply_to_social_comment(
                db_session,
                fb_comment,
                "Got it!",
                author_id="agent_1",
                author_name="Agent Smith",
            )
        )
        assert reply.author_id == "agent_1"
        assert reply.author_name == "Agent Smith"

    def test_reply_rejects_missing_identifiers(self, db_session, fb_comment):
        fb_comment.external_id = None
        from app.services.crm.conversations.comments import reply_to_social_comment

        with pytest.raises(RuntimeError, match="Missing comment identifiers"):
            _run_async(reply_to_social_comment(db_session, fb_comment, "Test"))

    def test_reply_rejects_oversized_facebook_message(self, db_session, fb_comment):
        from app.services.crm.conversations.comments import reply_to_social_comment

        long_msg = "x" * 8001
        with pytest.raises(ValueError, match="exceeds facebook limit"):
            _run_async(reply_to_social_comment(db_session, fb_comment, long_msg))

    def test_reply_rejects_oversized_instagram_message(self, db_session, ig_comment):
        from app.services.crm.conversations.comments import reply_to_social_comment

        long_msg = "x" * 2201
        with pytest.raises(ValueError, match="exceeds instagram limit"):
            _run_async(reply_to_social_comment(db_session, ig_comment, long_msg))

    @patch("app.services.meta_pages.reply_to_comment", new_callable=AsyncMock)
    def test_reply_accepts_message_at_exact_limit(self, mock_reply, db_session, fb_comment):
        mock_reply.return_value = {"id": "fb_ok"}
        from app.services.crm.conversations.comments import reply_to_social_comment

        reply = _run_async(reply_to_social_comment(db_session, fb_comment, "x" * 8000))
        assert reply.message == "x" * 8000


# ---------------------------------------------------------------------------
# Inbox service layer tests
# ---------------------------------------------------------------------------


class TestInboxReplyToSocialComment:
    @patch("app.services.crm.comments.reply_to_social_comment", new_callable=AsyncMock)
    def test_success_returns_success_kind(self, mock_reply, db_session, fb_comment):
        mock_reply.return_value = SocialCommentReply(
            comment_id=fb_comment.id,
            platform=SocialCommentPlatform.facebook,
            external_id="reply_ext",
            message="Done",
            created_time=datetime.now(UTC),
        )
        from app.services.crm.inbox.comment_replies import reply_to_social_comment

        result = _run_async(
            reply_to_social_comment(
                db_session,
                comment_id=str(fb_comment.id),
                message="Done",
                actor_id="agent_1",
            )
        )
        assert result.kind == "success"

    def test_not_found_for_bad_comment_id(self, db_session):
        from app.services.crm.inbox.comment_replies import reply_to_social_comment

        result = _run_async(
            reply_to_social_comment(
                db_session,
                comment_id="00000000-0000-0000-0000-000000000000",
                message="Test",
            )
        )
        assert result.kind == "not_found"

    def test_forbidden_when_lacking_scopes(self, db_session, fb_comment):
        from app.services.crm.inbox.comment_replies import reply_to_social_comment

        result = _run_async(
            reply_to_social_comment(
                db_session,
                comment_id=str(fb_comment.id),
                message="Test",
                roles=[],
                scopes=[],
            )
        )
        assert result.kind == "forbidden"

    @patch("app.services.crm.comments.reply_to_social_comment", new_callable=AsyncMock)
    def test_error_kind_on_exception(self, mock_reply, db_session, fb_comment):
        mock_reply.side_effect = RuntimeError("Meta API down")
        from app.services.crm.inbox.comment_replies import reply_to_social_comment

        result = _run_async(
            reply_to_social_comment(
                db_session,
                comment_id=str(fb_comment.id),
                message="Test",
            )
        )
        assert result.kind == "error"
        assert "Meta API down" in (result.error_detail or "")


# ---------------------------------------------------------------------------
# Upsert tests
# ---------------------------------------------------------------------------


class TestUpsertSocialCommentReply:
    def test_upsert_creates_reply_with_author(self, db_session, fb_comment):
        from app.services.crm.conversations.comments import upsert_social_comment_reply

        reply = upsert_social_comment_reply(
            db_session,
            platform=SocialCommentPlatform.facebook,
            parent_external_id="fb_comment_123",
            external_id="webhook_reply_001",
            message="Webhook reply",
            created_time=datetime.now(UTC),
            raw_payload={"test": True},
            author_id="ext_user_99",
            author_name="Bob",
        )
        assert reply is not None
        assert reply.author_id == "ext_user_99"
        assert reply.author_name == "Bob"
        assert reply.comment_id == fb_comment.id

    def test_upsert_returns_none_for_missing_parent(self, db_session):
        from app.services.crm.conversations.comments import upsert_social_comment_reply

        reply = upsert_social_comment_reply(
            db_session,
            platform=SocialCommentPlatform.facebook,
            parent_external_id="nonexistent_comment",
            external_id="orphan_reply",
            message="Orphan",
            created_time=datetime.now(UTC),
            raw_payload=None,
        )
        assert reply is None

    def test_upsert_updates_existing_reply(self, db_session, fb_comment):
        from app.services.crm.conversations.comments import upsert_social_comment_reply

        reply1 = upsert_social_comment_reply(
            db_session,
            platform=SocialCommentPlatform.facebook,
            parent_external_id="fb_comment_123",
            external_id="dup_reply_001",
            message="Original",
            created_time=datetime.now(UTC),
            raw_payload=None,
        )
        reply2 = upsert_social_comment_reply(
            db_session,
            platform=SocialCommentPlatform.facebook,
            parent_external_id="fb_comment_123",
            external_id="dup_reply_001",
            message="Updated",
            created_time=datetime.now(UTC),
            raw_payload=None,
        )
        assert reply1.id == reply2.id
        assert reply2.message == "Updated"


# ---------------------------------------------------------------------------
# List and get tests
# ---------------------------------------------------------------------------


class TestListAndGetComments:
    def test_list_social_comments_filters_inactive(self, db_session, fb_comment):
        from app.services.crm.conversations.comments import list_social_comments

        inactive = SocialComment(
            platform=SocialCommentPlatform.facebook,
            external_id="inactive_001",
            message="Deleted comment",
            is_active=False,
        )
        db_session.add(inactive)
        db_session.commit()
        results = list_social_comments(db_session)
        ext_ids = [c.external_id for c in results]
        assert "fb_comment_123" in ext_ids
        assert "inactive_001" not in ext_ids

    def test_list_social_comment_replies_filters_inactive(self, db_session, fb_comment):
        from app.services.crm.conversations.comments import list_social_comment_replies

        active_reply = SocialCommentReply(
            comment_id=fb_comment.id,
            platform=SocialCommentPlatform.facebook,
            external_id="active_r",
            message="Active",
            is_active=True,
            created_time=datetime.now(UTC),
        )
        inactive_reply = SocialCommentReply(
            comment_id=fb_comment.id,
            platform=SocialCommentPlatform.facebook,
            external_id="inactive_r",
            message="Inactive",
            is_active=False,
            created_time=datetime.now(UTC),
        )
        db_session.add_all([active_reply, inactive_reply])
        db_session.commit()
        results = list_social_comment_replies(db_session, str(fb_comment.id))
        ext_ids = [r.external_id for r in results]
        assert "active_r" in ext_ids
        assert "inactive_r" not in ext_ids

    def test_get_social_comment_returns_none_for_invalid_uuid(self, db_session):
        from app.services.crm.conversations.comments import get_social_comment

        assert get_social_comment(db_session, "not-a-uuid") is None


class TestFetchAndStoreSocialComments:
    @patch("app.services.meta_pages.get_connected_pages", return_value=[])
    @patch("app.services.meta_pages.get_connected_instagram_accounts", return_value=[{"account_id": "ig_1"}])
    @patch("app.services.meta_pages.get_instagram_media", new_callable=AsyncMock)
    @patch("app.services.meta_pages.get_instagram_media_comments", new_callable=AsyncMock)
    def test_fetch_stores_instagram_nested_replies_with_parent_comment_id(
        self,
        mock_media_comments,
        mock_media,
        _mock_accounts,
        _mock_pages,
        db_session,
    ):
        from app.services.crm.conversations.comments import fetch_and_store_social_comments

        mock_media.return_value = [{"id": "media_1", "permalink": "https://instagram.test/p/abc"}]
        mock_media_comments.return_value = [
            {
                "id": "parent_comment_1",
                "username": "customer_1",
                "text": "Parent comment",
                "timestamp": "2026-04-01T00:00:00Z",
                "replies": {
                    "data": [
                        {
                            "id": "nested_reply_1",
                            "username": "support_1",
                            "text": "Nested reply",
                            "timestamp": "2026-04-01T00:01:00Z",
                        }
                    ]
                },
            }
        ]

        _run_async(fetch_and_store_social_comments(db_session, post_limit=1, comment_limit=1))

        parent = (
            db_session.query(SocialComment)
            .filter(SocialComment.platform == SocialCommentPlatform.instagram)
            .filter(SocialComment.external_id == "parent_comment_1")
            .first()
        )
        assert parent is not None

        reply = (
            db_session.query(SocialCommentReply)
            .filter(SocialCommentReply.platform == SocialCommentPlatform.instagram)
            .filter(SocialCommentReply.external_id == "nested_reply_1")
            .first()
        )
        assert reply is not None
        assert reply.comment_id == parent.id


class TestCommentReplyRouteRedirects:
    def test_get_reply_route_merges_next_query_parameters(self):
        from app.web.admin.crm_inbox_comment_reply import reply_to_social_comment_get

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/admin/crm/inbox/comments/123/reply",
                "headers": [],
                "query_string": b"",
            }
        )

        response = reply_to_social_comment_get(
            request=request,
            comment_id="123",
            next="/admin/crm/inbox?search=alice",
        )

        location = response.headers["location"]
        parsed = urlparse(location)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        assert parsed.path == "/admin/crm/inbox"
        assert params["search"] == "alice"
        assert params["comment_id"] == "123"
        assert params["reply_error"] == "1"
        assert params["channel"] == "comments"
