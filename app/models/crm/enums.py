import enum


class ChannelType(enum.Enum):
    email = "email"
    whatsapp = "whatsapp"
    facebook_messenger = "facebook_messenger"
    instagram_dm = "instagram_dm"


class ConversationStatus(enum.Enum):
    open = "open"
    pending = "pending"
    snoozed = "snoozed"
    resolved = "resolved"


class MessageDirection(enum.Enum):
    inbound = "inbound"
    outbound = "outbound"


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
