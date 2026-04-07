# Social Comment Reply Gaps — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all identified gaps in the Instagram/Facebook comment reply system — missing FK/relationship, missing author on replies, message length validation, IG nested reply processing, error detail leakage, logger misuse, and comprehensive tests.

**Architecture:** Six focused changes across model, service, route, and template layers, plus a new test file. Each task is self-contained and independently committable. The migration adds a FK + columns to `crm_social_comment_replies`.

**Tech Stack:** SQLAlchemy 2.0, Alembic, FastAPI, Jinja2, pytest (SQLite-backed `db_session`)

---

### Task 1: Add ForeignKey, relationship, and author columns to SocialCommentReply

The `comment_id` column on `SocialCommentReply` has no `ForeignKey` constraint, no SQLAlchemy `relationship()`, and no author tracking columns. This task adds them.

**Files:**
- Modify: `app/models/crm/comments.py`
- Create: `alembic/versions/sc1a2b3c4d5e6_add_comment_reply_fk_and_author.py`

- [ ] **Step 1: Add ForeignKey, relationship, and author columns to model**

In `app/models/crm/comments.py`, update imports and both models:

```python
import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SocialCommentPlatform(enum.Enum):
    facebook = "facebook"
    instagram = "instagram"


class SocialComment(Base):
    __tablename__ = "crm_social_comments"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "external_id",
            name="uq_crm_social_comments_platform_external",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[SocialCommentPlatform] = mapped_column(Enum(SocialCommentPlatform), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    external_post_id: Mapped[str | None] = mapped_column(String(200))
    source_account_id: Mapped[str | None] = mapped_column(String(200))
    author_id: Mapped[str | None] = mapped_column(String(200))
    author_name: Mapped[str | None] = mapped_column(String(200))
    message: Mapped[str | None] = mapped_column(Text)
    created_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    permalink_url: Mapped[str | None] = mapped_column(String(500))
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    replies: Mapped[list["SocialCommentReply"]] = relationship(
        "SocialCommentReply", back_populates="comment", lazy="selectin",
    )


class SocialCommentReply(Base):
    __tablename__ = "crm_social_comment_replies"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "external_id",
            name="uq_crm_social_comment_replies_platform_external",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    comment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_social_comments.id"), nullable=False,
    )
    platform: Mapped[SocialCommentPlatform] = mapped_column(Enum(SocialCommentPlatform), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(200))
    author_id: Mapped[str | None] = mapped_column(String(200))
    author_name: Mapped[str | None] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    comment: Mapped["SocialComment"] = relationship("SocialComment", back_populates="replies")
```

- [ ] **Step 2: Create migration**

Create `alembic/versions/sc1a2b3c4d5e6_add_comment_reply_fk_and_author.py`:

```python
"""Add FK, author columns to social comment replies.

Revision ID: sc1a2b3c4d5e6
Revises: <FILL_IN_CURRENT_HEAD>
Create Date: 2026-04-02
"""

from alembic import op
import sqlalchemy as sa

revision = "sc1a2b3c4d5e6"
down_revision = None  # FILL IN: run `alembic heads` to get current head
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_social_comment_replies",
        sa.Column("author_id", sa.String(200), nullable=True),
    )
    op.add_column(
        "crm_social_comment_replies",
        sa.Column("author_name", sa.String(200), nullable=True),
    )
    # Add FK only if not already present
    op.create_foreign_key(
        "fk_crm_social_comment_replies_comment_id",
        "crm_social_comment_replies",
        "crm_social_comments",
        ["comment_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_crm_social_comment_replies_comment_id",
        "crm_social_comment_replies",
        type_="foreignkey",
    )
    op.drop_column("crm_social_comment_replies", "author_name")
    op.drop_column("crm_social_comment_replies", "author_id")
```

- [ ] **Step 3: Verify tests still pass**

Run: `poetry run pytest tests/ -x -q`
Expected: all existing tests pass (model change is additive)

- [ ] **Step 4: Commit**

```bash
git add app/models/crm/comments.py alembic/versions/sc1a2b3c4d5e6_add_comment_reply_fk_and_author.py
git commit -m "feat: add FK, relationship, and author columns to SocialCommentReply"
```

---

### Task 2: Populate author fields on outbound replies and inbound webhook replies

Now that the model has `author_id` and `author_name`, populate them everywhere replies are created.

**Files:**
- Modify: `app/services/crm/conversations/comments.py:304-343` (outbound reply)
- Modify: `app/services/crm/conversations/comments.py:76-95` (`_upsert_comment_reply`)
- Modify: `app/services/meta_webhooks.py:1424-1436` (FB webhook reply)
- Modify: `app/services/meta_webhooks.py:1501-1514` (IG webhook reply)

- [ ] **Step 1: Add author fields to outbound reply creation**

In `app/services/crm/conversations/comments.py`, update `reply_to_social_comment()` to accept and store author info:

Change the `SocialCommentReply(...)` construction (around line 332) from:

```python
    reply = SocialCommentReply(
        comment_id=comment.id,
        platform=comment.platform,
        external_id=str(external_id) if external_id else None,
        message=message,
        created_time=datetime.now(UTC),
        raw_payload=result,
    )
```

to:

```python
    reply = SocialCommentReply(
        comment_id=comment.id,
        platform=comment.platform,
        external_id=str(external_id) if external_id else None,
        author_id=author_id,
        author_name=author_name,
        message=message,
        created_time=datetime.now(UTC),
        raw_payload=result,
    )
```

And update the function signature to:

```python
async def reply_to_social_comment(
    db: Session,
    comment: SocialComment,
    message: str,
    *,
    author_id: str | None = None,
    author_name: str | None = None,
) -> SocialCommentReply:
```

Also update `SocialCommentReplies.reply()` to pass through:

```python
    @staticmethod
    async def reply(
        db: Session,
        comment: SocialComment,
        message: str,
        *,
        author_id: str | None = None,
        author_name: str | None = None,
    ) -> SocialCommentReply:
        return await reply_to_social_comment(
            db, comment, message, author_id=author_id, author_name=author_name,
        )
```

- [ ] **Step 2: Add author fields to webhook reply upserts**

In `app/services/meta_webhooks.py`, update the Facebook comment reply upsert (around line 1426):

```python
                reply = comments_service.upsert_social_comment_reply(
                    db=db,
                    platform=SocialCommentPlatform.facebook,
                    parent_external_id=payload.parent_id,
                    external_id=payload.comment_id,
                    message=payload.message,
                    created_time=payload.created_time,
                    raw_payload=value,
                    author_id=payload.from_id,
                    author_name=payload.from_name,
                )
```

And the Instagram comment reply upsert (around line 1504):

```python
                reply = comments_service.upsert_social_comment_reply(
                    db=db,
                    platform=SocialCommentPlatform.instagram,
                    parent_external_id=parent_id,
                    external_id=payload.comment_id,
                    message=payload.text,
                    created_time=payload.timestamp,
                    raw_payload=value,
                    author_id=payload.from_id,
                    author_name=payload.from_username,
                )
```

- [ ] **Step 3: Update `upsert_social_comment_reply` to accept author fields**

In `app/services/crm/conversations/comments.py`, update the function signature and payload:

```python
def upsert_social_comment_reply(
    db: Session,
    platform: SocialCommentPlatform,
    parent_external_id: str | None,
    external_id: str | None,
    message: str | None,
    created_time: datetime | None,
    raw_payload: dict | None,
    author_id: str | None = None,
    author_name: str | None = None,
) -> SocialCommentReply | None:
    if not parent_external_id or not external_id:
        return None
    parent = (
        db.query(SocialComment)
        .filter(SocialComment.platform == platform)
        .filter(SocialComment.external_id == parent_external_id)
        .first()
    )
    if not parent:
        return None
    payload = {
        "platform": platform,
        "external_id": external_id,
        "message": message or "",
        "created_time": created_time,
        "raw_payload": raw_payload,
        "is_active": True,
        "author_id": author_id,
        "author_name": author_name,
    }
    reply = _upsert_comment_reply(db, parent, payload)
    db.commit()
    db.refresh(reply)
    return reply
```

- [ ] **Step 4: Pass author through from inbox service**

In `app/services/crm/inbox/comment_replies.py`, pass author info:

```python
        await comments_service.reply_to_social_comment(
            db, comment, message.strip(),
            author_id=actor_id,
            author_name=None,
        )
```

- [ ] **Step 5: Run tests**

Run: `poetry run pytest tests/ -x -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/services/crm/conversations/comments.py app/services/meta_webhooks.py app/services/crm/inbox/comment_replies.py
git commit -m "feat: populate author fields on social comment replies"
```

---

### Task 3: Add message length validation

Meta enforces character limits: ~8,000 for Facebook comments, ~2,200 for Instagram comments. Validate before hitting the API.

**Files:**
- Modify: `app/services/crm/conversations/comments.py:304-310`
- Modify: `templates/admin/crm/_comment_thread.html:71`

- [ ] **Step 1: Add server-side validation in the reply function**

In `app/services/crm/conversations/comments.py`, add validation constants and check at the top of `reply_to_social_comment()`:

After the existing imports, add:

```python
# Meta Graph API comment length limits
_FB_COMMENT_MAX_LENGTH = 8000
_IG_COMMENT_MAX_LENGTH = 2200
```

At the start of `reply_to_social_comment()`, after the existing identifier check:

```python
    max_len = (
        _FB_COMMENT_MAX_LENGTH
        if comment.platform == SocialCommentPlatform.facebook
        else _IG_COMMENT_MAX_LENGTH
    )
    if len(message) > max_len:
        raise ValueError(
            f"Reply exceeds {comment.platform.value} limit of {max_len} characters "
            f"({len(message)} provided)"
        )
```

- [ ] **Step 2: Add maxlength to template textarea**

In `templates/admin/crm/_comment_thread.html`, add `maxlength` to the textarea (line 71):

Change:
```html
                <textarea name="message"
                          rows="2"
                          placeholder="Write a reply..."
                          required
                          class="flex-1 resize-none rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 shadow-sm focus:border-primary-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"></textarea>
```

to:
```html
                <textarea name="message"
                          rows="2"
                          placeholder="Write a reply..."
                          required
                          maxlength="2200"
                          class="flex-1 resize-none rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 shadow-sm focus:border-primary-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"></textarea>
```

Use 2200 (the stricter IG limit) as the HTML-side guard. Server-side uses the platform-specific limit.

- [ ] **Step 3: Run tests**

Run: `poetry run pytest tests/ -x -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add app/services/crm/conversations/comments.py templates/admin/crm/_comment_thread.html
git commit -m "feat: add message length validation for social comment replies"
```

---

### Task 4: Process nested IG replies in fetch_and_store, fix error detail leak, fix logger.exception

Three small independent fixes grouped in one task.

**Files:**
- Modify: `app/services/crm/conversations/comments.py:215-238` (IG nested replies)
- Modify: `app/web/admin/crm_inbox_comment_reply.py:116` (logger.exception → logger.error)
- Modify: `app/web/admin/crm_inbox_comment_reply.py:84-85` (sanitize error detail)

- [ ] **Step 1: Process nested IG replies in fetch_and_store_social_comments**

In `app/services/crm/conversations/comments.py`, inside the Instagram comment loop in `fetch_and_store_social_comments()`, after the `_upsert_comment(...)` call for IG comments (around line 237), add nested reply processing:

Replace the entire Instagram comment inner loop (lines 216-238) with:

```python
            for comment in comments:
                if not comment.get("id"):
                    continue
                fetched += 1
                parent = _upsert_comment(
                    db,
                    {
                        "platform": SocialCommentPlatform.instagram,
                        "external_id": str(comment.get("id")),
                        "external_post_id": str(media_id),
                        "source_account_id": str(ig_account_id),
                        "author_id": comment.get("username"),
                        "author_name": comment.get("username"),
                        "message": comment.get("text"),
                        "created_time": _parse_meta_datetime(comment.get("timestamp")),
                        "permalink_url": media.get("permalink"),
                        "raw_payload": {
                            "media": media,
                            "comment": comment,
                        },
                    },
                )
                stored += 1
                # Process nested replies returned by the API
                nested_replies = (comment.get("replies") or {}).get("data") or []
                for nested in nested_replies:
                    if not nested.get("id"):
                        continue
                    _upsert_comment_reply(
                        db,
                        parent,
                        {
                            "platform": SocialCommentPlatform.instagram,
                            "external_id": str(nested.get("id")),
                            "author_id": nested.get("username"),
                            "author_name": nested.get("username"),
                            "message": nested.get("text") or "",
                            "created_time": _parse_meta_datetime(nested.get("timestamp")),
                            "raw_payload": nested,
                            "is_active": True,
                        },
                    )
                    stored += 1
```

- [ ] **Step 2: Fix logger.exception → logger.error in route**

In `app/web/admin/crm_inbox_comment_reply.py` line 116, change:

```python
        logger.exception(
```

to:

```python
        logger.error(
```

(`logger.exception` logs a traceback, but there is no active exception in scope here — the error is in `result.error_detail`.)

- [ ] **Step 3: Sanitize error detail in redirect URL**

In `app/web/admin/crm_inbox_comment_reply.py`, change the error redirect (around line 121-127) to truncate and sanitize the error detail:

```python
    if result.kind == "error":
        logger.error(
            "social_comment_reply_failed comment_id=%s error=%s",
            comment_id,
            result.error_detail,
        )
        return RedirectResponse(
            url=_build_reply_redirect(
                reply_error=True,
                reply_error_detail="Reply failed. Please try again.",
            ),
            status_code=303,
        )
```

This replaces the raw `result.error_detail` (which may contain Meta API internals) with a generic user-facing message while still logging the real error.

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/ -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/crm/conversations/comments.py app/web/admin/crm_inbox_comment_reply.py
git commit -m "fix: process IG nested replies, sanitize error detail, fix logger misuse"
```

---

### Task 5: Write comprehensive tests

No tests currently exist for the social comment reply flow. This task adds them covering the service layer, inbox layer, and webhook processing.

**Files:**
- Create: `tests/test_social_comment_replies.py`

- [ ] **Step 1: Write the test file**

Create `tests/test_social_comment_replies.py`:

```python
"""Tests for social comment reply flow."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.models.crm.comments import SocialComment, SocialCommentPlatform, SocialCommentReply


def _run_async(coro):
    """Run an async coroutine in a sync test."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


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
                db_session, fb_comment, "Got it!",
                author_id="agent_1", author_name="Agent Smith",
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
    @patch("app.services.crm.conversations.comments.reply_to_social_comment", new_callable=AsyncMock)
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

    @patch("app.services.crm.conversations.comments.reply_to_social_comment", new_callable=AsyncMock)
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
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `poetry run pytest tests/test_social_comment_replies.py -v`
Expected: all tests PASS

- [ ] **Step 3: Run full test suite**

Run: `poetry run pytest tests/ -x -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_social_comment_replies.py
git commit -m "test: add comprehensive tests for social comment reply flow"
```

---

### Task 6: Lint and verify

**Files:** All modified files

- [ ] **Step 1: Run ruff**

Run: `poetry run ruff check app/models/crm/comments.py app/services/crm/conversations/comments.py app/services/meta_webhooks.py app/web/admin/crm_inbox_comment_reply.py app/services/crm/inbox/comment_replies.py tests/test_social_comment_replies.py --fix`
Expected: no errors (or auto-fixed)

- [ ] **Step 2: Run full test suite one final time**

Run: `poetry run pytest tests/ -x -q`
Expected: all PASS

- [ ] **Step 3: Commit any lint fixes if needed**

```bash
git add -u
git commit -m "style: lint fixes for social comment reply changes"
```
