"""Compatibility wrapper for social comment services."""

from app.services.crm.conversations.comments import (
    SocialComments,
    SocialCommentReplies,
    fetch_and_store_social_comments,
    list_social_comments,
    list_social_comment_replies,
    reply_to_social_comment,
    social_comments,
    social_comment_replies,
)

__all__ = [
    "SocialComments",
    "SocialCommentReplies",
    "fetch_and_store_social_comments",
    "list_social_comments",
    "list_social_comment_replies",
    "reply_to_social_comment",
    "social_comments",
    "social_comment_replies",
]
