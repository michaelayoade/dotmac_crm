"""CRM Conversations submodule.

Handles conversation threads, messages, assignments, and tags.
"""

from app.services.crm.conversations.service import (
    Conversations,
    ConversationAssignments,
    ConversationTags,
    Messages,
    conversations,
    conversation_assignments,
    conversation_tags,
    messages,
)
from app.services.crm.conversations.message_attachments import (
    MessageAttachments,
    message_attachments,
)
from app.services.crm.conversations.private_notes import (
    PrivateNotes,
    private_notes,
)
from app.services.crm.conversations.comments import (
    SocialComments,
    SocialCommentReplies,
    social_comments,
    social_comment_replies,
)

__all__ = [
    # Core conversation services
    "Conversations",
    "ConversationAssignments",
    "ConversationTags",
    "Messages",
    "conversations",
    "conversation_assignments",
    "conversation_tags",
    "messages",
    # Attachments
    "MessageAttachments",
    "message_attachments",
    # Private notes
    "PrivateNotes",
    "private_notes",
    # Social comments
    "SocialComments",
    "SocialCommentReplies",
    "social_comments",
    "social_comment_replies",
]
