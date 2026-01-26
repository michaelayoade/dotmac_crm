from app.services.crm.contact import Contacts, ContactChannels, get_or_create_contact_by_channel
from app.services.crm.conversation import (
    Conversations,
    ConversationAssignments,
    ConversationTags,
    Messages,
    MessageAttachments,
    resolve_conversation_contact,
    resolve_open_conversation,
)
from app.services.crm.team import Teams, Agents, AgentTeams, TeamChannels, RoutingRules
from app.services.crm.sales import Pipelines, PipelineStages, Leads, Quotes, CrmQuoteLineItems
from app.services.crm import inbox

contacts = Contacts()
contact_channels = ContactChannels()
conversations = Conversations()
conversation_assignments = ConversationAssignments()
conversation_tags = ConversationTags()
messages = Messages()
message_attachments = MessageAttachments()
teams = Teams()
agents = Agents()
agent_teams = AgentTeams()
team_channels = TeamChannels()
routing_rules = RoutingRules()
pipelines = Pipelines()
pipeline_stages = PipelineStages()
leads = Leads()
quotes = Quotes()
quote_line_items = CrmQuoteLineItems()

__all__ = [
    "contacts",
    "contact_channels",
    "conversations",
    "conversation_assignments",
    "conversation_tags",
    "messages",
    "message_attachments",
    "teams",
    "agents",
    "agent_teams",
    "team_channels",
    "routing_rules",
    "pipelines",
    "pipeline_stages",
    "leads",
    "quotes",
    "quote_line_items",
    "get_or_create_contact_by_channel",
    "resolve_conversation_contact",
    "resolve_open_conversation",
    "inbox",
]
