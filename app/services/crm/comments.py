"""Compatibility wrapper for social comment services."""

from app.services.crm.conversations.comments import (
    SocialCommentReplies,
    SocialComments,
    fetch_and_store_social_comments,
    list_social_comment_replies,
    list_social_comments,
    reply_to_social_comment,
    social_comment_replies,
    social_comments,
)

__all__ = [
    "SocialCommentReplies",
    "SocialComments",
    "fetch_and_store_social_comments",
    "list_social_comment_replies",
    "list_social_comments",
    "reply_to_social_comment",
    "social_comment_replies",
    "social_comments",
]
