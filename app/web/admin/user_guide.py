"""Admin user guide routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.web.admin import get_current_user, get_sidebar_stats

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/user-guide", tags=["web-admin-user-guide"])
GUIDE_FILE = Path("docs/USER_GUIDE.md")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


GUIDE_MODULES = [
    {
        "key": "getting-started",
        "title": "Getting Started",
        "summary": "Login, navigation basics, roles, and daily startup checklist.",
        "href": "/admin/user-guide?module=getting-started",
    },
    {
        "key": "crm-inbox",
        "title": "CRM Inbox",
        "summary": "Handle conversations, assignment, notes, tags, and SLAs.",
        "href": "/admin/user-guide?module=crm-inbox",
    },
    {
        "key": "contacts-leads",
        "title": "Contacts & Leads",
        "summary": "Create entities, qualify leads, and move into opportunities.",
        "href": "/admin/user-guide?module=contacts-leads",
    },
    {
        "key": "support-tickets",
        "title": "Support Tickets",
        "summary": "Open, assign, escalate, and resolve support requests.",
        "href": "/admin/user-guide?module=support-tickets",
    },
    {
        "key": "dispatch-work-orders",
        "title": "Dispatch & Work Orders",
        "summary": "Schedule field tasks, assign technicians, and close jobs.",
        "href": "/admin/user-guide?module=dispatch-work-orders",
    },
    {
        "key": "projects",
        "title": "Projects",
        "summary": "Manage templates, milestones, tasks, and delivery tracking.",
        "href": "/admin/user-guide?module=projects",
    },
    {
        "key": "fiber-map",
        "title": "Fiber Map",
        "summary": "Use layers, search, measure, plan routes, and edit assets.",
        "href": "/admin/user-guide?module=fiber-map",
    },
    {
        "key": "inventory",
        "title": "Inventory",
        "summary": "Track stock, movements, assignment, and reconciliation.",
        "href": "/admin/user-guide?module=inventory",
    },
    {
        "key": "system-admin",
        "title": "System & Permissions",
        "summary": "Users, roles, permissions, automations, and configuration.",
        "href": "/admin/user-guide?module=system-admin",
    },
]

DEFAULT_GUIDE_FIELDS: dict[str, list[object]] = {
    "audience": [],
    "permissions": [],
    "quick_links": [],
    "workflows": [],
    "pitfalls": [],
    "troubleshooting": [],
    "role_playbooks": [],
    "sla_checkpoints": [],
    "completion_criteria": [],
}

GUIDE_DETAILS = {
    "getting-started": {
        "audience": ["Admin", "Supervisor", "Agent"],
        "permissions": [
            "Auth-enabled account",
            "Role assignment (Admin/Manager/Agent)",
            "Basic read access to assigned modules",
        ],
        "quick_links": [
            {"label": "Dashboard", "href": "/admin/dashboard"},
            {"label": "My Profile", "href": "/admin/system/users/profile"},
            {"label": "Settings", "href": "/admin/system/settings"},
        ],
        "workflows": [
            {
                "title": "First Login and Workspace Setup",
                "objective": "Prepare your workspace before handling operations.",
                "steps": [
                    "Sign in with your assigned credentials.",
                    "Open profile and confirm your name, email, and preferred timezone.",
                    "Review role permissions with your supervisor.",
                    "Open Dashboard and confirm your main cards are visible.",
                    "Pin your daily modules: Inbox, Tickets, Dispatch, or Projects.",
                ],
            },
            {
                "title": "Daily Startup Checklist",
                "objective": "Start each shift with correct operational context.",
                "steps": [
                    "Check Dashboard alerts and unresolved warnings.",
                    "Open CRM Inbox and review unread conversations.",
                    "Open Support Tickets and filter by open/high priority.",
                    "Open Dispatch board for today's field assignments.",
                    "Confirm escalations from previous shift are acknowledged.",
                ],
            },
        ],
        "pitfalls": [
            "Working on tickets without checking open escalations first.",
            "Changing status without leaving an internal note for next shift.",
            "Using the wrong module for customer communication history.",
        ],
        "troubleshooting": [
            {
                "issue": "Missing menu items",
                "fix": "Request role/permission update from System Admin.",
            },
            {
                "issue": "Cannot open a page after login",
                "fix": "Log out and in again, then verify account is active.",
            },
        ],
        "role_playbooks": [
            {
                "role": "Agent",
                "steps": [
                    "Complete profile and timezone setup on first login.",
                    "Review assigned modules and daily queue expectations.",
                    "Run startup checklist before taking customer tasks.",
                    "Leave handover notes at end of each shift.",
                ],
            },
            {
                "role": "Supervisor",
                "steps": [
                    "Validate team roster and active coverage windows.",
                    "Review backlog across inbox, tickets, and dispatch.",
                    "Set shift priorities and escalation path for the day.",
                    "Confirm unresolved critical items have named owners.",
                ],
            },
            {
                "role": "Admin",
                "steps": [
                    "Verify onboarding accounts, roles, and security policies.",
                    "Confirm MFA and password standards are enforced.",
                    "Audit module visibility for least-privilege access.",
                    "Update onboarding checklist when workflows change.",
                ],
            },
        ],
        "sla_checkpoints": [
            "New users complete setup checklist before production access.",
            "Shift startup review completed before first customer response.",
            "Critical unresolved items acknowledged at every shift handover.",
            "Role/access issues escalated same day.",
        ],
        "completion_criteria": [
            "User profile and security setup are complete.",
            "User can access only required modules for their role.",
            "Startup checklist completed with no blocking issues.",
            "Handover notes captured for pending items.",
        ],
    },
    "crm-inbox": {
        "audience": ["Agent", "Supervisor", "Admin"],
        "permissions": [
            "crm:conversation:read",
            "crm:conversation:write",
            "crm:contact:read (recommended)",
        ],
        "quick_links": [
            {"label": "Inbox", "href": "/admin/crm/inbox"},
            {"label": "Inbox Settings", "href": "/admin/crm/inbox/settings"},
            {"label": "Contacts", "href": "/admin/crm/contacts"},
        ],
        "workflows": [
            {
                "title": "Handle New Inbound Conversation",
                "objective": "Respond quickly and route correctly.",
                "steps": [
                    "Open Inbox and filter to unread/unassigned conversations.",
                    "Open thread and review full context before replying.",
                    "Set owner/assignee and status (open/pending/resolved).",
                    "Reply using approved template or custom response.",
                    "Add internal note with decision, next step, and ETA.",
                ],
            },
            {
                "title": "Escalate Conversation to Ticket",
                "objective": "Convert unresolved issues into trackable support work.",
                "steps": [
                    "Confirm issue needs action beyond messaging.",
                    "Create or link support ticket from conversation context.",
                    "Set priority/SLA in ticket and copy key customer details.",
                    "Post confirmation message in conversation with ticket ref.",
                    "Mark conversation pending while ticket is active.",
                ],
            },
        ],
        "pitfalls": [
            "Replying before reading previous agent/internal notes.",
            "Closing conversation while linked ticket is still open.",
            "Sending channel-incompatible attachments or message formats.",
        ],
        "troubleshooting": [
            {
                "issue": "Messages not sending",
                "fix": "Check connector status in Inbox Settings and retry.",
            },
            {
                "issue": "Duplicate conversations",
                "fix": "Use contact merge/dedup flow and keep canonical thread.",
            },
        ],
        "role_playbooks": [
            {
                "role": "Agent",
                "steps": [
                    "Acknowledge new inbound messages within target first-response window.",
                    "Apply conversation owner and status immediately after first review.",
                    "Use approved templates for policy-sensitive replies; personalize as needed.",
                    "Leave internal note when handing off or pausing a thread.",
                ],
            },
            {
                "role": "Supervisor",
                "steps": [
                    "Monitor unassigned and aging conversations each hour.",
                    "Rebalance queue ownership when an agent is overloaded.",
                    "Review quality of responses and enforce response standards.",
                    "Escalate high-risk threads to ticket/operations quickly.",
                ],
            },
            {
                "role": "Admin",
                "steps": [
                    "Maintain connector uptime and channel health checks.",
                    "Review routing rules and template integrity weekly.",
                    "Audit failed outbound attempts and resolve root causes.",
                    "Update permissions for new teams and seasonal coverage.",
                ],
            },
        ],
        "sla_checkpoints": [
            "First response sent within channel SLA target.",
            "Customer update posted before conversation enters pending > 24h.",
            "Escalated issue linked to ticket with priority and owner.",
            "Resolved status only after customer confirmation or policy timeout.",
        ],
        "completion_criteria": [
            "Customer question addressed with clear next step or final outcome.",
            "Conversation owner, status, and tags are correct.",
            "Linked ticket/work order reference is attached when applicable.",
            "Internal note includes summary, decision, and handoff context.",
        ],
    },
    "contacts-leads": {
        "audience": ["Sales", "Agent", "Admin"],
        "permissions": [
            "crm:contact:read/write",
            "crm:lead:read/write",
            "subscriber:read (for conversion validation)",
        ],
        "quick_links": [
            {"label": "Contacts", "href": "/admin/crm/contacts"},
            {"label": "Leads", "href": "/admin/crm/leads"},
            {"label": "Quotes", "href": "/admin/crm/quotes"},
        ],
        "workflows": [
            {
                "title": "Create and Qualify a New Lead",
                "objective": "Capture enough data for reliable follow-up.",
                "steps": [
                    "Create contact/person and organization if needed.",
                    "Create lead with source, demand, and location context.",
                    "Set lead status and owner immediately.",
                    "Add qualification notes (budget, timeline, fit).",
                    "Create next action (call, survey, quote request).",
                ],
            },
            {
                "title": "Convert Lead to Opportunity/Quote",
                "objective": "Move qualified demand into commercial workflow.",
                "steps": [
                    "Validate serviceability and customer details.",
                    "Generate quote with scope and expected turnaround.",
                    "Record assumptions in notes and share with stakeholder.",
                    "Track quote status and schedule follow-up.",
                    "On approval, handoff to operations/project flow.",
                ],
            },
        ],
        "pitfalls": [
            "Creating duplicate contacts instead of updating existing record.",
            "Missing lead owner assignment, causing follow-up gaps.",
            "Advancing lead stage without qualification evidence.",
        ],
        "troubleshooting": [
            {
                "issue": "Cannot find contact after creation",
                "fix": "Clear filters and check status/entity-type filters first.",
            },
            {
                "issue": "Lead stuck in pipeline",
                "fix": "Confirm required fields and owner are set before stage move.",
            },
        ],
        "role_playbooks": [
            {
                "role": "Sales Agent",
                "steps": [
                    "Search before create to prevent duplicate contacts.",
                    "Capture mandatory lead qualification fields.",
                    "Set owner and next action date on every active lead.",
                    "Advance stage only after qualification evidence exists.",
                ],
            },
            {
                "role": "Sales Supervisor",
                "steps": [
                    "Review stale leads and enforce follow-up discipline.",
                    "Validate stage changes for pipeline hygiene.",
                    "Coach team on lead notes quality and conversion readiness.",
                    "Escalate blocked high-value opportunities quickly.",
                ],
            },
            {
                "role": "Admin",
                "steps": [
                    "Maintain lead/contact field standards and required rules.",
                    "Monitor duplicate trends and tune dedup process.",
                    "Audit owner coverage and unassigned lead counts.",
                    "Align quote handoff rules with operations intake.",
                ],
            },
        ],
        "sla_checkpoints": [
            "New leads receive owner assignment within intake target.",
            "Next action date is set for every open lead.",
            "Stage transition includes qualifying note or attachment.",
            "Approved quotes are handed off to operations without delay.",
        ],
        "completion_criteria": [
            "Contact and organization records are complete and deduplicated.",
            "Lead owner, status, and next action are populated.",
            "Qualification evidence is documented before conversion.",
            "Handoff artifacts are present for quote/project transition.",
        ],
    },
    "support-tickets": {
        "audience": ["Support Agent", "Supervisor", "Admin"],
        "permissions": [
            "support:ticket:read",
            "support:ticket:create",
            "support:ticket:update",
        ],
        "quick_links": [
            {"label": "Tickets List", "href": "/admin/support/tickets"},
            {"label": "Create Ticket", "href": "/admin/support/tickets/create"},
            {"label": "Dispatch", "href": "/admin/operations/dispatch"},
        ],
        "workflows": [
            {
                "title": "Open and Triage Ticket",
                "objective": "Classify correctly and start SLA clock with ownership.",
                "steps": [
                    "Create ticket with accurate customer and channel details.",
                    "Set type, severity, priority, and category.",
                    "Assign owner/team and due date according to SLA.",
                    "Add reproducible issue details and attachments.",
                    "Notify customer of receipt and expected next update.",
                ],
            },
            {
                "title": "Resolve and Close Ticket",
                "objective": "Close only when service is restored and documented.",
                "steps": [
                    "Confirm root cause and action performed.",
                    "If field work needed, ensure linked work order is closed.",
                    "Post resolution summary for customer.",
                    "Capture closure note for internal learning.",
                    "Set status to resolved/closed and verify no pending tasks.",
                ],
            },
        ],
        "pitfalls": [
            "Changing ticket status without customer-visible update.",
            "Closing ticket before field team completion confirmation.",
            "No cause code at closure, reducing reporting quality.",
        ],
        "troubleshooting": [
            {
                "issue": "Ticket cannot be updated",
                "fix": "Verify permissions and whether ticket is locked/final state.",
            },
            {
                "issue": "Customer not linked",
                "fix": "Search contacts/subscribers first, then relink ticket.",
            },
        ],
        "role_playbooks": [
            {
                "role": "Support Agent",
                "steps": [
                    "Capture complete issue details at ticket creation.",
                    "Set severity/priority with evidence from impact.",
                    "Communicate status changes to customer on each major transition.",
                    "Escalate to field or network teams when remote resolution fails.",
                ],
            },
            {
                "role": "Supervisor",
                "steps": [
                    "Review high-priority queue every hour.",
                    "Reassign stalled tickets and enforce due-date discipline.",
                    "Approve escalation levels and external communication tone.",
                    "Close QA loop on tickets with repeated recurrence.",
                ],
            },
            {
                "role": "Admin",
                "steps": [
                    "Maintain ticket taxonomy, statuses, and form integrity.",
                    "Validate SLA policy mapping to priority classes.",
                    "Review audit trail for unauthorized state changes.",
                    "Tune automation rules for assignment/notifications.",
                ],
            },
        ],
        "sla_checkpoints": [
            "Ticket triaged and owned within intake SLA.",
            "Customer receives progress updates before each SLA threshold breach.",
            "Escalation triggered automatically or manually at breach risk.",
            "Resolution timestamp captured with closure note and cause code.",
        ],
        "completion_criteria": [
            "Root cause and remediation documented.",
            "Customer-visible resolution message delivered.",
            "Related work order or task is complete and verified.",
            "Ticket status is closed with accurate category/priority history.",
        ],
    },
    "dispatch-work-orders": {
        "audience": ["Dispatcher", "Field Supervisor", "Admin"],
        "permissions": [
            "operations:work_order:read/create/update",
            "operations:work_order:dispatch",
            "operations:technician:read",
        ],
        "quick_links": [
            {"label": "Dispatch Board", "href": "/admin/operations/dispatch"},
            {"label": "Work Orders", "href": "/admin/operations/work-orders"},
            {"label": "Technicians", "href": "/admin/operations/technicians"},
        ],
        "workflows": [
            {
                "title": "Schedule and Dispatch Work Order",
                "objective": "Assign jobs to the right technician with context.",
                "steps": [
                    "Create/select work order and verify address + contact.",
                    "Set priority, required skills, and estimated duration.",
                    "Assign technician based on capacity and proximity.",
                    "Confirm appointment window and notify customer.",
                    "Track status transitions: scheduled -> in progress -> completed.",
                ],
            },
            {
                "title": "Manage Delays and Reassignments",
                "objective": "Recover schedule quickly when disruptions happen.",
                "steps": [
                    "Detect delay from board/status feed.",
                    "Add delay reason and updated ETA.",
                    "Reassign to alternate technician if needed.",
                    "Notify affected customer and update related ticket.",
                    "Review end-of-day backlog and unresolved jobs.",
                ],
            },
        ],
        "pitfalls": [
            "Dispatching without confirming technician skill/asset availability.",
            "Skipping customer notification after schedule changes.",
            "Leaving in-progress jobs without completion proof.",
        ],
        "troubleshooting": [
            {
                "issue": "Technician unavailable for assignment",
                "fix": "Check roster status, then reassign or reschedule slot.",
            },
            {
                "issue": "Work order status not moving",
                "fix": "Verify required fields and related task dependencies.",
            },
        ],
        "role_playbooks": [
            {
                "role": "Dispatcher",
                "steps": [
                    "Assign jobs using skill, geography, and current workload.",
                    "Confirm ETA/appointment with customer before dispatch lock.",
                    "Track technician check-in and progress milestones.",
                    "Re-plan quickly on delay and communicate revised ETA.",
                ],
            },
            {
                "role": "Field Supervisor",
                "steps": [
                    "Validate job quality and completion evidence.",
                    "Approve complex reassignments or priority overrides.",
                    "Ensure safety and compliance notes are present.",
                    "Close or bounce incomplete jobs with explicit reasons.",
                ],
            },
            {
                "role": "Admin",
                "steps": [
                    "Maintain technician roster accuracy and availability flags.",
                    "Review dispatch utilization and backlog trends weekly.",
                    "Validate work-order state model and automation hooks.",
                    "Audit reassignment frequency for planning improvements.",
                ],
            },
        ],
        "sla_checkpoints": [
            "Work order assigned within defined dispatch SLA.",
            "Customer informed when ETA shifts beyond tolerance window.",
            "In-progress jobs receive status heartbeat updates.",
            "Completion verification posted before final close.",
        ],
        "completion_criteria": [
            "Technician notes and evidence (photos/parts/actions) are complete.",
            "Customer outcome confirmed or follow-up window scheduled.",
            "Consumed inventory/services are recorded.",
            "Linked ticket/project states are updated consistently.",
        ],
    },
    "projects": {
        "audience": ["Project Manager", "Coordinator", "Admin"],
        "permissions": [
            "project:read/create/update",
            "project:task:read/write",
            "vendor access where applicable",
        ],
        "quick_links": [
            {"label": "Projects", "href": "/admin/projects"},
            {"label": "Project Templates", "href": "/admin/projects/templates"},
            {"label": "Vendors", "href": "/admin/vendors"},
        ],
        "workflows": [
            {
                "title": "Create New Project from Template",
                "objective": "Start projects with standardized structure.",
                "steps": [
                    "Create project with customer, location, and target dates.",
                    "Apply template for milestone/task baseline.",
                    "Review dependencies and assign task owners.",
                    "Set project status and communication cadence.",
                    "Publish kickoff update to stakeholders.",
                ],
            },
            {
                "title": "Track Progress and Closeout",
                "objective": "Deliver on time with complete documentation.",
                "steps": [
                    "Review overdue tasks and blockers daily.",
                    "Update milestone completion evidence.",
                    "Escalate risks and adjust plan where needed.",
                    "Complete as-built and acceptance steps.",
                    "Mark project closed and record lessons learned.",
                ],
            },
        ],
        "pitfalls": [
            "Skipping dependency setup, causing sequencing conflicts.",
            "No single owner on high-impact tasks.",
            "Closing project without documentation handover.",
        ],
        "troubleshooting": [
            {
                "issue": "Tasks missing after template apply",
                "fix": "Reopen template config and verify task generation settings.",
            },
            {
                "issue": "Project cannot move to closed",
                "fix": "Resolve open tasks/milestones and required completion fields.",
            },
        ],
        "role_playbooks": [
            {
                "role": "Project Manager",
                "steps": [
                    "Initialize project using approved template and baseline dates.",
                    "Assign accountable owners to all critical-path tasks.",
                    "Run weekly risk review and mitigation updates.",
                    "Control change requests with impact notes and approvals.",
                ],
            },
            {
                "role": "Project Coordinator",
                "steps": [
                    "Update task progress and blocker notes daily.",
                    "Prepare stakeholder updates from milestone status.",
                    "Track vendor dependencies and due dates.",
                    "Collect completion evidence before task closure.",
                ],
            },
            {
                "role": "Admin",
                "steps": [
                    "Maintain project templates and task dependency standards.",
                    "Audit overdue task backlog and owner distribution.",
                    "Validate closeout controls and required fields.",
                    "Tune role permissions for project operations.",
                ],
            },
        ],
        "sla_checkpoints": [
            "Project kickoff communication sent at project start.",
            "Critical blockers escalated within review window.",
            "Milestone status refreshed on agreed reporting cadence.",
            "Closeout checklist completed before project closure.",
        ],
        "completion_criteria": [
            "All mandatory tasks and milestones are complete.",
            "As-built or completion artifacts are attached.",
            "Stakeholder acceptance or closure acknowledgment exists.",
            "Project status, dates, and summary notes are finalized.",
        ],
    },
    "fiber-map": {
        "audience": ["Network Planner", "Field Engineer", "Admin"],
        "permissions": [
            "gis:fiber:view/edit",
            "network:fiber:read/write",
            "network:read",
        ],
        "quick_links": [
            {"label": "Fiber Map", "href": "/admin/network/fiber-map"},
            {"label": "Fiber Change Requests", "href": "/admin/network/fiber-change-requests"},
            {"label": "Fiber Reports", "href": "/admin/network/fiber/reports"},
        ],
        "workflows": [
            {
                "title": "Locate and Inspect Network Assets",
                "objective": "Use map layers to inspect topology and asset state.",
                "steps": [
                    "Open map and enable required layer groups.",
                    "Use search (Ctrl/Cmd+K) for code/name/coordinates.",
                    "Open asset popup and verify metadata.",
                    "Toggle distance overlays when planning path checks.",
                    "Capture findings in change request if correction needed.",
                ],
            },
            {
                "title": "Plan and Submit Network Changes",
                "objective": "Document map updates before operational execution.",
                "steps": [
                    "Use plan/measure tools to validate route approach.",
                    "Edit geometry only when authorized for map edits.",
                    "Submit change request with rationale and impact.",
                    "Link request to ticket/project when applicable.",
                    "After approval, apply changes and re-validate layers.",
                ],
            },
        ],
        "pitfalls": [
            "Editing live map objects without approved change request.",
            "Planning routes without checking backbone/distribution context.",
            "Ignoring QA remediation queue after change.",
        ],
        "troubleshooting": [
            {
                "issue": "Layers not visible",
                "fix": "Reset layer presets and confirm group/layer checkboxes.",
            },
            {
                "issue": "Cannot edit markers",
                "fix": "Enable edit mode and verify edit permissions.",
            },
        ],
        "role_playbooks": [
            {
                "role": "Network Planner",
                "steps": [
                    "Validate planning context with required layer presets.",
                    "Use measure and plan tools before proposing path changes.",
                    "Document assumptions and impact in change request.",
                    "Coordinate approvals before applying map edits.",
                ],
            },
            {
                "role": "Field Engineer",
                "steps": [
                    "Confirm asset identity on map before field intervention.",
                    "Capture correction requests with exact coordinates/details.",
                    "Verify implemented changes against approved request.",
                    "Attach post-work validation evidence.",
                ],
            },
            {
                "role": "Admin",
                "steps": [
                    "Maintain map permissions and edit governance.",
                    "Review QA remediation queue for unresolved anomalies.",
                    "Audit high-impact geometry changes periodically.",
                    "Ensure map data sync and reporting consistency.",
                ],
            },
        ],
        "sla_checkpoints": [
            "Urgent correction requests are triaged within response target.",
            "Planned map edits require approved request before execution.",
            "Post-edit validation is completed within verification window.",
            "QA remediation issues are reviewed on regular cadence.",
        ],
        "completion_criteria": [
            "Change request includes rationale, impact, and references.",
            "Map geometry/attributes reflect approved outcome.",
            "Related ticket/project links are attached.",
            "Validation note confirms final topology state.",
        ],
    },
    "inventory": {
        "audience": ["Inventory Officer", "Operations", "Admin"],
        "permissions": [
            "inventory:read",
            "inventory:write",
            "operations access for assignment flows",
        ],
        "quick_links": [
            {"label": "Inventory", "href": "/admin/inventory"},
            {"label": "Locations", "href": "/admin/inventory/locations"},
            {"label": "Work Orders", "href": "/admin/operations/work-orders"},
        ],
        "workflows": [
            {
                "title": "Receive and Stock Items",
                "objective": "Record stock accurately at receipt.",
                "steps": [
                    "Create or open item record and verify SKU/serial data.",
                    "Post quantity received to correct location.",
                    "Attach supplier/reference details in notes.",
                    "Validate on-hand quantity after transaction.",
                    "Flag discrepancies for reconciliation.",
                ],
            },
            {
                "title": "Issue Inventory to Field Work",
                "objective": "Track asset usage per job and technician.",
                "steps": [
                    "Open work order and identify required parts.",
                    "Reserve/issue items to technician or job.",
                    "Confirm deductions from source stock location.",
                    "Return unused parts or mark consumed quantities.",
                    "Close with final usage note for auditability.",
                ],
            },
        ],
        "pitfalls": [
            "Adjusting stock without reason code or note.",
            "Issuing items without linking to work order/ticket.",
            "Missing serial-level traceability on tracked items.",
        ],
        "troubleshooting": [
            {
                "issue": "Negative stock appears",
                "fix": "Review recent movements and reverse incorrect transactions.",
            },
            {
                "issue": "Item not assignable to order",
                "fix": "Check availability at correct location and item status.",
            },
        ],
        "role_playbooks": [
            {
                "role": "Inventory Officer",
                "steps": [
                    "Record every stock movement with reason and reference.",
                    "Enforce serial/lot tracking on controlled assets.",
                    "Run periodic reconciliation by location.",
                    "Escalate discrepancies with audit-ready notes.",
                ],
            },
            {
                "role": "Operations/Field Coordinator",
                "steps": [
                    "Reserve parts against planned work before dispatch.",
                    "Issue inventory with linked work order/ticket.",
                    "Confirm returns or consumption after job closure.",
                    "Report shortage risk before SLA impact.",
                ],
            },
            {
                "role": "Admin",
                "steps": [
                    "Maintain item master quality and location controls.",
                    "Review exception reports (negative stock, orphan movements).",
                    "Tune permissions around adjustments and write-offs.",
                    "Verify integration sync for inventory-affecting workflows.",
                ],
            },
        ],
        "sla_checkpoints": [
            "Receipts posted within intake processing window.",
            "Field issues posted before technician departure.",
            "Job-related consumption reconciled within closure target.",
            "Discrepancies investigated within inventory control SLA.",
        ],
        "completion_criteria": [
            "Movement is linked to a valid operational reference.",
            "On-hand and reserved counts are consistent.",
            "Serial/lot traceability is intact where required.",
            "Exception handling note is attached when adjustments occur.",
        ],
    },
    "system-admin": {
        "audience": ["System Admin", "IT Operations", "Security Lead"],
        "permissions": [
            "system:settings:read/write",
            "rbac roles/permissions management",
            "system automation and scheduler access",
        ],
        "quick_links": [
            {"label": "System Overview", "href": "/admin/system"},
            {"label": "Users", "href": "/admin/system/users"},
            {"label": "Roles", "href": "/admin/system/roles"},
            {"label": "Automations", "href": "/admin/system/automations"},
        ],
        "workflows": [
            {
                "title": "Provision User Access",
                "objective": "Grant least-privilege access safely.",
                "steps": [
                    "Create user and verify identity details.",
                    "Assign baseline role and only required custom permissions.",
                    "Force password reset/MFA setup where policy requires.",
                    "Validate access by checking visible menu modules.",
                    "Record provisioning action in audit context.",
                ],
            },
            {
                "title": "Operate System Controls",
                "objective": "Maintain platform stability and compliance.",
                "steps": [
                    "Review automations and disable noisy/broken rules.",
                    "Check scheduler health and failed jobs.",
                    "Monitor server health and integration status.",
                    "Review audit logs for critical changes.",
                    "Apply configuration updates through controlled change window.",
                ],
            },
        ],
        "pitfalls": [
            "Over-permissioning temporary users.",
            "Changing automations without rollback notes.",
            "Ignoring warning trends in health/audit dashboards.",
        ],
        "troubleshooting": [
            {
                "issue": "User cannot access required module",
                "fix": "Check role + direct permissions and then re-login user.",
            },
            {
                "issue": "Unexpected automation behavior",
                "fix": "Inspect automation logs, disable rule, and test safely.",
            },
        ],
        "role_playbooks": [
            {
                "role": "System Admin",
                "steps": [
                    "Provision users with least-privilege role baseline.",
                    "Review high-risk permissions before granting exceptions.",
                    "Change settings through controlled change window.",
                    "Validate effects in audit and health dashboards.",
                ],
            },
            {
                "role": "IT Operations",
                "steps": [
                    "Monitor scheduler, integration, and service health daily.",
                    "Investigate job failures and restore normal run state.",
                    "Maintain backup/recovery operational readiness.",
                    "Coordinate incident updates with stakeholders.",
                ],
            },
            {
                "role": "Security Lead",
                "steps": [
                    "Review privileged-role assignments and anomalies.",
                    "Audit authentication policy and MFA compliance.",
                    "Track critical configuration changes in audit logs.",
                    "Approve or reject risky automation/system changes.",
                ],
            },
        ],
        "sla_checkpoints": [
            "Access provisioning/deprovisioning completed within policy window.",
            "Critical system alerts acknowledged within incident SLA.",
            "Failed scheduled jobs triaged within operational threshold.",
            "Security-relevant changes reviewed and logged same day.",
        ],
        "completion_criteria": [
            "User access state matches approved request.",
            "System changes have rollback/verification notes.",
            "Audit trail is complete for privileged operations.",
            "Health, scheduler, and automation status are green or actioned.",
        ],
    },
}


@router.get("", response_class=HTMLResponse)
def user_guide_index(
    request: Request,
    module: str = "getting-started",
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    selected_module_meta = next(
        (item for item in GUIDE_MODULES if item["key"] == module),
        GUIDE_MODULES[0],
    )
    selected_module = {
        **DEFAULT_GUIDE_FIELDS,
        **selected_module_meta,
        **GUIDE_DETAILS.get(selected_module_meta["key"], {}),
    }
    return templates.TemplateResponse(
        "admin/user_guide/index.html",
        {
            "request": request,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "user-guide",
            "active_menu": "user-guide",
            "modules": GUIDE_MODULES,
            "selected_module": selected_module,
        },
    )


@router.get("/full", response_class=HTMLResponse)
def user_guide_full(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    guide_text = ""
    if GUIDE_FILE.exists():
        guide_text = GUIDE_FILE.read_text(encoding="utf-8")
    return templates.TemplateResponse(
        "admin/user_guide/full.html",
        {
            "request": request,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "user-guide",
            "active_menu": "user-guide",
            "guide_text": guide_text,
            "guide_found": bool(guide_text.strip()),
        },
    )
