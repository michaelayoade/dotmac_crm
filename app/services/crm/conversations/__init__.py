"""CRM Conversations submodule.

Handles conversation threads, messages, assignments, and tags.
"""

from app.services.crm.conversations.comments import (
    SocialCommentReplies,
    SocialComments,
    social_comment_replies,
    social_comments,
)
from app.services.crm.conversations.message_attachments import (
    MessageAttachments,
    message_attachments,
)
from app.services.crm.conversations.private_notes import (
    PrivateNotes,
    private_notes,
)
from app.services.crm.conversations.service import (
    ConversationAssignments,
    Conversations,
    ConversationTags,
    Messages,
    conversation_assignments,
    conversation_tags,
    conversations,
    messages,
)

__all__ = [
    "ConversationAssignments",
    "ConversationTags",
    # Core conversation services
    "Conversations",
    # Attachments
    "MessageAttachments",
    "Messages",
    # Private notes
    "PrivateNotes",
    "SocialCommentReplies",
    # Social comments
    "SocialComments",
    "conversation_assignments",
    "conversation_tags",
    "conversations",
    "message_attachments",
    "messages",
    "private_notes",
    "social_comment_replies",
    "social_comments",
]
