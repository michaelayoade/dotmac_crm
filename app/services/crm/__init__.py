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

# Contacts submodule
from app.services.crm.contacts import (
    Contacts,
    ContactChannels,
    contacts,
    contact_channels,
)
from app.services.crm.contacts.service import get_or_create_contact_by_channel

# Conversations submodule
from app.services.crm.conversations import (
    Conversations,
    ConversationAssignments,
    ConversationTags,
    Messages,
    MessageAttachments,
    PrivateNotes,
    SocialComments,
    SocialCommentReplies,
    conversations,
    conversation_assignments,
    conversation_tags,
    messages,
    message_attachments,
    private_notes,
    social_comments,
    social_comment_replies,
)
from app.services.crm.conversations import comments as comments
from app.services.crm.conversations.service import (
    resolve_conversation_contact,
    resolve_open_conversation,
)

# Teams submodule
from app.services.crm.teams import (
    Teams,
    Agents,
    AgentTeams,
    TeamChannels,
    RoutingRules,
    teams,
    agents,
    agent_teams,
    team_channels,
    routing_rules,
)
from app.services.crm.teams import service as team
from app.services.crm.teams.service import get_agent_labels, get_agent_team_options

# Presence submodule
from app.services.crm.presence import AgentPresenceManager, agent_presence

# Sales submodule
from app.services.crm.sales import (
    Pipelines,
    PipelineStages,
    Leads,
    Quotes,
    CrmQuoteLineItems,
    pipelines,
    pipeline_stages,
    leads,
    quotes,
    quote_line_items,
)

# Campaigns submodule
from app.services.crm.campaigns import (
    Campaigns,
    CampaignSteps,
    CampaignRecipients,
    campaigns,
    campaign_steps,
    campaign_recipients,
)

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

# Inbox submodule (keep as namespace import for complex operations)
from app.services.crm import inbox
from app.services.crm import smtp_inbound

from typing import Callable, Any

# Widget message functions (if they exist in widget service)
receive_widget_message: Callable[..., Any] | None
send_widget_message: Callable[..., Any] | None
try:
    from app.services.crm.widget.service import receive_widget_message, send_widget_message
except ImportError:
    receive_widget_message = None
    send_widget_message = None

__all__ = [
    # Contacts
    "Contacts",
    "ContactChannels",
    "contacts",
    "contact_channels",
    "get_or_create_contact_by_channel",
    # Conversations
    "Conversations",
    "ConversationAssignments",
    "ConversationTags",
    "Messages",
    "MessageAttachments",
    "PrivateNotes",
    "SocialComments",
    "SocialCommentReplies",
    "conversations",
    "conversation_assignments",
    "conversation_tags",
    "messages",
    "message_attachments",
    "private_notes",
    "social_comments",
    "social_comment_replies",
    "comments",
    "resolve_conversation_contact",
    "resolve_open_conversation",
    # Teams
    "Teams",
    "Agents",
    "AgentTeams",
    "TeamChannels",
    "RoutingRules",
    "teams",
    "agents",
    "agent_teams",
    "team_channels",
    "routing_rules",
    "get_agent_labels",
    "get_agent_team_options",
    # Presence
    "AgentPresenceManager",
    "agent_presence",
    # Sales
    "Pipelines",
    "PipelineStages",
    "Leads",
    "Quotes",
    "CrmQuoteLineItems",
    "pipelines",
    "pipeline_stages",
    "leads",
    "quotes",
    "quote_line_items",
    # Campaigns
    "Campaigns",
    "CampaignSteps",
    "CampaignRecipients",
    "campaigns",
    "campaign_steps",
    "campaign_recipients",
    # Widget
    "ChatWidgetConfigs",
    "WidgetVisitorSessions",
    "chat_widget_configs",
    "widget_visitor_sessions",
    "widget_configs",  # Backward compatibility
    "widget_visitors",  # Backward compatibility
    "receive_widget_message",
    "send_widget_message",
    # Inbox
    "inbox",
    "smtp_inbound",
]
