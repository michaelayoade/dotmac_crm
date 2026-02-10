"""CRM Service Module.

Provides omni-channel customer relationship management including:
- Contacts: Person and channel management
- Conversations: Message threads and assignments
- Teams: Agent teams and routing rules
- Sales: Pipelines, leads, and quotes
- Inbox: Email/message processing
- Widget: Chat widget configuration

Submodule Structure:
    crm/
    ├── contacts/      - Contact and channel management
    ├── conversations/ - Message threads, assignments, tags
    ├── teams/         - Teams, agents, routing rules
    ├── sales/         - Pipelines, leads, quotes
    ├── inbox/         - Email/message ingestion
    ├── widget/        - Chat widget
    └── reports.py     - CRM analytics and reporting
"""

from collections.abc import Callable
from typing import Any

# Inbox submodule (keep as namespace import for complex operations)
from app.services.crm import inbox, smtp_inbound

# Contacts submodule
# Campaigns submodule
from app.services.crm.campaigns import (
    CampaignRecipients,
    Campaigns,
    CampaignSteps,
    campaign_recipients,
    campaign_steps,
    campaigns,
)
from app.services.crm.contacts import (
    ContactChannels,
    Contacts,
    contact_channels,
    contacts,
)
from app.services.crm.contacts.service import get_or_create_contact_by_channel

# Conversations submodule
from app.services.crm.conversations import (
    ConversationAssignments,
    Conversations,
    ConversationTags,
    MessageAttachments,
    Messages,
    PrivateNotes,
    SocialCommentReplies,
    SocialComments,
    conversation_assignments,
    conversation_tags,
    conversations,
    message_attachments,
    messages,
    private_notes,
    social_comment_replies,
    social_comments,
)
from app.services.crm.conversations import comments as comments
from app.services.crm.conversations.service import (
    resolve_conversation_contact,
    resolve_open_conversation,
)

# Presence submodule
from app.services.crm.presence import AgentPresenceManager, agent_presence

# Sales submodule
from app.services.crm.sales import (
    CrmQuoteLineItems,
    Leads,
    Pipelines,
    PipelineStages,
    Quotes,
    leads,
    pipeline_stages,
    pipelines,
    quote_line_items,
    quotes,
)

# Teams submodule
from app.services.crm.teams import (
    Agents,
    AgentTeams,
    RoutingRules,
    TeamChannels,
    Teams,
    agent_teams,
    agents,
    routing_rules,
    team_channels,
    teams,
)
from app.services.crm.teams import service as team
from app.services.crm.teams.service import get_agent_labels, get_agent_team_options

# Widget submodule
from app.services.crm.widget import (
    ChatWidgetConfigs,
    WidgetVisitorSessions,
    chat_widget_configs,
    widget_visitor_sessions,
)

# Backward compatibility aliases
widget_configs = chat_widget_configs
widget_visitors = widget_visitor_sessions

# Widget message functions (if they exist in widget service)
receive_widget_message: Callable[..., Any] | None
send_widget_message: Callable[..., Any] | None
try:
    from app.services.crm.widget.service import receive_widget_message, send_widget_message
except ImportError:
    receive_widget_message = None
    send_widget_message = None

__all__ = [
    "AgentPresenceManager",
    "AgentTeams",
    "Agents",
    "CampaignRecipients",
    "CampaignSteps",
    "Campaigns",
    "ChatWidgetConfigs",
    "ContactChannels",
    "Contacts",
    "ConversationAssignments",
    "ConversationTags",
    "Conversations",
    "CrmQuoteLineItems",
    "Leads",
    "MessageAttachments",
    "Messages",
    "PipelineStages",
    "Pipelines",
    "PrivateNotes",
    "Quotes",
    "RoutingRules",
    "SocialCommentReplies",
    "SocialComments",
    "TeamChannels",
    "Teams",
    "WidgetVisitorSessions",
    "agent_presence",
    "agent_teams",
    "agents",
    "campaign_recipients",
    "campaign_steps",
    "campaigns",
    "chat_widget_configs",
    "comments",
    "contact_channels",
    "contacts",
    "conversation_assignments",
    "conversation_tags",
    "conversations",
    "get_agent_labels",
    "get_agent_team_options",
    "get_or_create_contact_by_channel",
    "inbox",
    "leads",
    "message_attachments",
    "messages",
    "pipeline_stages",
    "pipelines",
    "private_notes",
    "quote_line_items",
    "quotes",
    "receive_widget_message",
    "resolve_conversation_contact",
    "resolve_open_conversation",
    "routing_rules",
    "send_widget_message",
    "smtp_inbound",
    "social_comment_replies",
    "social_comments",
    "team",
    "team_channels",
    "teams",
    "widget_configs",
    "widget_visitor_sessions",
    "widget_visitors",
]
