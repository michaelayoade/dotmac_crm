import enum


class ChannelType(enum.Enum):
    email = "email"
    whatsapp = "whatsapp"
    facebook_messenger = "facebook_messenger"
    instagram_dm = "instagram_dm"
    note = "note"
    chat_widget = "chat_widget"


class ConversationStatus(enum.Enum):
    open = "open"
    pending = "pending"
    snoozed = "snoozed"
    resolved = "resolved"


class MessageDirection(enum.Enum):
    inbound = "inbound"
    outbound = "outbound"
    internal = "internal"


class MessageStatus(enum.Enum):
    received = "received"
    queued = "queued"
    sent = "sent"
    failed = "failed"


class LeadStatus(enum.Enum):
    new = "new"
    contacted = "contacted"
    qualified = "qualified"
    proposal = "proposal"
    negotiation = "negotiation"
    won = "won"
    lost = "lost"


class QuoteStatus(enum.Enum):
    draft = "draft"
    sent = "sent"
    accepted = "accepted"
    rejected = "rejected"
    expired = "expired"


class CampaignStatus(enum.Enum):
    draft = "draft"
    scheduled = "scheduled"
    sending = "sending"
    sent = "sent"
    completed = "completed"
    cancelled = "cancelled"


class CampaignType(enum.Enum):
    one_time = "one_time"
    nurture = "nurture"


class CampaignChannel(enum.Enum):
    email = "email"
    whatsapp = "whatsapp"


class CampaignRecipientStatus(enum.Enum):
    pending = "pending"
    sent = "sent"
    delivered = "delivered"
    failed = "failed"
    bounced = "bounced"
    unsubscribed = "unsubscribed"


class AgentPresenceStatus(enum.Enum):
    online = "online"
    away = "away"
    offline = "offline"
