from app.models.analytics import (  # noqa: F401
    KPIAggregate,
    KPIConfig,
)
from app.models.audit import AuditActorType, AuditEvent  # noqa: F401
from app.models.auth import ApiKey, MFAMethod, Session, UserCredential  # noqa: F401
from app.models.bandwidth import BandwidthSample, QueueMapping  # noqa: F401
from app.models.comms import (  # noqa: F401
    CustomerNotificationEvent,
    CustomerNotificationStatus,
    CustomerSurveyStatus,
    EtaUpdate,
    Survey,
    SurveyInvitation,
    SurveyInvitationStatus,
    SurveyQuestionType,
    SurveyResponse,
    SurveyTriggerType,
)
from app.models.connector import (  # noqa: F401
    ConnectorAuthType,
    ConnectorConfig,
    ConnectorType,
)
from app.models.contracts import ContractSignature  # noqa: F401
from app.models.crm import (  # noqa: F401
    AgentPresence,
    AgentPresenceStatus,
    Campaign,
    CampaignRecipient,
    CampaignRecipientStatus,
    CampaignStatus,
    CampaignStep,
    CampaignType,
    ChannelType,
    Conversation,
    ConversationAssignment,
    ConversationStatus,
    ConversationTag,
    CrmAgent,
    CrmAgentTeam,
    CrmQuoteLineItem,
    CrmRoutingRule,
    CrmTeam,
    CrmTeamChannel,
    Lead,
    LeadStatus,
    Message,
    MessageAttachment,
    MessageDirection,
    MessageStatus,
    Pipeline,
    PipelineStage,
    Quote,
    QuoteStatus,
    SocialComment,
    SocialCommentPlatform,
    SocialCommentReply,
)
from app.models.dispatch import (  # noqa: F401
    AvailabilityBlock,
    DispatchQueueStatus,
    DispatchRule,
    Shift,
    Skill,
    TechnicianProfile,
    TechnicianSkill,
    WorkOrderAssignmentQueue,
)
from app.models.domain_settings import (  # noqa: F401
    DomainSetting,
    SettingDomain,
    SettingValueType,
)
from app.models.event_store import (  # noqa: F401
    EventStatus,
    EventStore,
)
from app.models.external import (  # noqa: F401
    ExternalEntityType,
    ExternalReference,
)
from app.models.fiber_change_request import (  # noqa: F401
    FiberChangeRequest,
    FiberChangeRequestOperation,
    FiberChangeRequestStatus,
)
from app.models.gis import (  # noqa: F401
    GeoArea,
    GeoAreaType,
    GeoLayer,
    GeoLayerSource,
    GeoLayerType,
    GeoLocation,
    GeoLocationType,
    ServiceBuilding,
)
from app.models.integration import (  # noqa: F401
    IntegrationJob,
    IntegrationJobType,
    IntegrationRun,
    IntegrationRunStatus,
    IntegrationScheduleType,
    IntegrationTarget,
    IntegrationTargetType,
)
from app.models.inventory import (  # noqa: F401
    InventoryItem,
    InventoryLocation,
    InventoryStock,
    MaterialStatus,
    Reservation,
    ReservationStatus,
    WorkOrderMaterial,
)
from app.models.material_request import (  # noqa: F401
    MaterialRequest,
    MaterialRequestItem,
    MaterialRequestPriority,
    MaterialRequestStatus,
)
from app.models.legal import (  # noqa: F401
    LegalDocument,
    LegalDocumentType,
)
from app.models.network import (  # noqa: F401
    FdhCabinet,
    FiberAccessPoint,
    FiberEndpointType,
    FiberSegment,
    FiberSegmentType,
    FiberSplice,
    FiberSpliceClosure,
    FiberSpliceTray,
    FiberStrand,
    FiberStrandStatus,
    FiberTerminationPoint,
    ODNEndpointType,
    OltCard,
    OltCardPort,
    OLTDevice,
    OltPortType,
    OltPowerUnit,
    OltSfpModule,
    OltShelf,
    OntAssignment,
    OntUnit,
    PonPort,
    PonPortSplitterLink,
    Splitter,
    SplitterPort,
    SplitterPortType,
)
from app.models.notification import (  # noqa: F401
    AlertNotificationLog,
    AlertNotificationPolicy,
    AlertNotificationPolicyStep,
    DeliveryStatus,
    Notification,
    NotificationChannel,
    NotificationDelivery,
    NotificationStatus,
    NotificationTemplate,
    OnCallRotation,
    OnCallRotationMember,
)
from app.models.oauth_token import OAuthToken  # noqa: F401
from app.models.person import (  # noqa: F401
    ChannelType as PersonChannelType,
)
from app.models.person import (  # noqa: F401
    PartyStatus,
    Person,
    PersonChannel,
    PersonMergeLog,
    PersonStatusLog,
)
from app.models.projects import (  # noqa: F401
    Project,
    ProjectComment,
    ProjectPriority,
    ProjectStatus,
    ProjectTask,
    ProjectTaskAssignee,
    ProjectTaskComment,
    ProjectTaskDependency,
    ProjectTemplate,
    ProjectTemplateTask,
    ProjectTemplateTaskDependency,
    ProjectType,
    TaskDependencyType,
    TaskPriority,
    TaskStatus,
)
from app.models.qualification import (  # noqa: F401
    BuildoutMilestone,
    BuildoutMilestoneStatus,
    BuildoutProject,
    BuildoutProjectStatus,
    BuildoutRequest,
    BuildoutRequestStatus,
    BuildoutStatus,
    BuildoutUpdate,
    CoverageArea,
    QualificationStatus,
    ServiceQualification,
)
from app.models.rbac import (  # noqa: F401
    Permission,
    PersonRole,
    Role,
    RolePermission,
)
from app.models.sales_order import (  # noqa: F401
    SalesOrder,
    SalesOrderLine,
    SalesOrderPaymentStatus,
    SalesOrderStatus,
)
from app.models.scheduler import ScheduledTask, ScheduleType  # noqa: F401
from app.models.service_team import (  # noqa: F401
    ServiceTeam,
    ServiceTeamMember,
    ServiceTeamMemberRole,
    ServiceTeamType,
)
from app.models.subscriber import (  # noqa: F401
    AccountStatus,
    AccountType,
    AddressType,
    Organization,
    Reseller,
    ResellerUser,
    Subscriber,
    SubscriberStatus,
)
from app.models.tickets import (  # noqa: F401
    Ticket,
    TicketAssignee,
    TicketChannel,
    TicketComment,
    TicketPriority,
    TicketSlaEvent,
    TicketStatus,
)
from app.models.timecost import (  # noqa: F401
    BillingRate,
    CostRate,
    ExpenseLine,
    WorkLog,
)
from app.models.vendor import (  # noqa: F401
    AsBuiltRoute,
    AsBuiltRouteStatus,
    InstallationProject,
    InstallationProjectNote,
    InstallationProjectStatus,
    ProjectQuote,
    ProjectQuoteStatus,
    ProposedRouteRevision,
    ProposedRouteRevisionStatus,
    QuoteLineItem,
    Vendor,
    VendorAssignmentType,
    VendorUser,
)
from app.models.webhook import (  # noqa: F401
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookEndpoint,
    WebhookEventType,
    WebhookSubscription,
)
from app.models.wireless_mast import WirelessMast  # noqa: F401
from app.models.wireless_survey import (  # noqa: F401
    SurveyLosPath,
    SurveyPoint,
    SurveyPointType,
    SurveyStatus,
    WirelessSiteSurvey,
)
from app.models.workflow import (  # noqa: F401
    ProjectTaskStatusTransition,
    SlaBreach,
    SlaBreachStatus,
    SlaClock,
    SlaClockStatus,
    SlaPolicy,
    SlaTarget,
    TicketStatusTransition,
    WorkflowEntityType,
    WorkOrderStatusTransition,
)
from app.models.workforce import (  # noqa: F401
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderNote,
    WorkOrderPriority,
    WorkOrderStatus,
    WorkOrderType,
)
