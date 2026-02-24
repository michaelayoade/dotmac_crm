---
name: db-schema
description: Query the Omni CRM database with full schema context, business rules, and cross-domain relationship awareness
arguments:
  - name: question
    description: "What you want to know (e.g. 'top 10 subscribers by ticket count', 'show CRM pipeline summary', 'which fiber closures have duplicate codes')"
---

# Database Schema & Business Context

Use this when the user asks about data, database structure, querying,
reporting, analytics, or needs to understand entity relationships.

## Instructions

You have access to the DotMac Omni CRM PostgreSQL/PostGIS database via the
`omni-db` MCP server. Use the `execute_sql` tool for SELECT queries only. The
connection should use a **read-only** user.

The MCP connection DSN must be provided via the `DOTMAC_OMNI_DB_DSN` environment
variable (see `.mcp.json`). Do not hardcode DB passwords in repo-tracked files.

### No Multi-Tenancy

Unlike the ERP database, Omni is a **single-tenant** application. There is no
`organization_id` scoping or Row-Level Security. Queries do not need org filters.

### Company Context

DotMac Technologies Ltd is a Nigerian ISP operating an omni-channel field
service and CRM platform. Currency: **NGN** (Nigerian Naira).

**Core business activities:**
- **Subscribers**: Internet service customers synced from Splynx (billing system)
- **Tickets**: Customer support tickets with SLA tracking, technician assignment, multi-team routing
- **Projects**: Field service projects (fiber installations, cable reruns) with task dependencies
- **CRM Inbox**: Omni-channel messaging (WhatsApp, email, SMS, webchat, Facebook, Instagram)
- **CRM Sales**: Lead pipeline, quotes, campaigns
- **Workforce**: Work orders, dispatch, technician scheduling
- **Network**: Fiber plant (OLTs, closures, cabinets, splitters, fiber segments with PostGIS)
- **Notifications**: Email, SMS, push, WhatsApp, webhook

### Schema Layout

All tables are in the `public` schema (single schema, no schema partitioning).

**Tables by Domain:**

| Domain | Key Tables | Table Prefix |
|--------|-----------|--------------|
| Tickets | `tickets`, `ticket_comments`, `ticket_sla_events`, `ticket_assignees` | `ticket*` |
| Projects | `projects`, `project_tasks`, `project_templates`, `project_task_dependencies` | `project*` |
| CRM Inbox | `crm_conversations`, `crm_messages`, `crm_message_attachments`, `crm_conversation_assignments` | `crm_*` |
| CRM Sales | `crm_leads`, `crm_pipelines`, `crm_pipeline_stages`, `crm_quotes`, `crm_quote_line_items` | `crm_*` |
| CRM Campaigns | `crm_campaigns`, `crm_campaign_steps`, `crm_campaign_recipients` | `crm_campaign*` |
| CRM Teams | `crm_teams`, `crm_agents`, `crm_agent_teams`, `crm_team_channels`, `crm_routing_rules` | `crm_*` |
| CRM Presence | `crm_agent_presence`, `crm_agent_presence_events`, `crm_agent_location_pings` | `crm_agent_*` |
| People/Auth | `people`, `person_channels`, `roles`, `permissions`, `user_roles` | `person*`, `people` |
| Subscribers | `subscribers` | `subscriber*` |
| Workforce | `work_orders`, `work_order_assignments`, `work_order_notes` | `work_order*` |
| Network/Fiber | `olt_devices`, `olt_shelves`, `olt_cards`, `olt_card_ports`, `olt_sfp_modules`, `olt_power_units` | `olt_*` |
| Fiber Plant | `fdh_cabinets`, `fiber_strands`, `fiber_splice_closures`, `fiber_segments`, `fiber_access_points`, `fiber_termination_points` | `fdh_*`, `fiber_*` |
| PON | `pon_ports`, `splitters`, `splitter_ports`, `pon_port_splitter_links` | `pon_*`, `splitter*` |
| ONT | `ont_units`, `ont_assignments` | `ont_*` |
| Notifications | `notifications`, `notification_templates`, `notification_deliveries` | `notification*` |
| Settings | `domain_settings` | `domain_setting*` |
| Vendors | `vendor_*` | `vendor_*` |

---

### Key Enums (verify before using!)

**CRITICAL**: Always verify enum values by querying the database. Never assume values.

```sql
SELECT t.typname AS enum_name, e.enumlabel AS value, e.enumsortorder AS ord
FROM pg_type t
JOIN pg_enum e ON e.enumtypid = t.oid
WHERE t.typname = 'ticketstatus'
ORDER BY e.enumsortorder;
```

**Known enums:**

| Enum | Values |
|------|--------|
| `ticketstatus` | new, open, pending, waiting_on_customer, lastmile_rerun, site_under_construction, on_hold, resolved, closed, canceled |
| `ticketpriority` | lower, low, medium, normal, high, urgent |
| `ticketchannel` | web, email, phone, chat, api |
| `projectstatus` | open, planned, active, on_hold, completed, canceled |
| `projecttype` | cable_rerun, fiber_optics_relocation, air_fiber_relocation, fiber_optics_installation, air_fiber_installation, cross_connect |
| `taskstatus` | backlog, todo, in_progress, blocked, done, canceled |
| `conversationstatus` | open, pending, snoozed, resolved |
| `channeltype` | email, whatsapp, facebook_messenger, instagram_dm, note, chat_widget |
| `messagedirection` | inbound, outbound, internal |
| `messagestatus` | received, queued, sent, failed |
| `leadstatus` | new, contacted, qualified, proposal, negotiation, won, lost |
| `quotestatus` | draft, sent, accepted, rejected, expired |
| `campaignstatus` | draft, scheduled, sending, sent, completed, cancelled |
| `subscriberstatus` | active, suspended, terminated, pending |
| `workorderstatus` | draft, scheduled, dispatched, in_progress, completed, canceled |
| `agentpresencestatus` | online, away, on_break, offline |
| `notificationstatus` | queued, sending, delivered, failed, canceled |
| `fiberstrandstatus` | available, in_use, reserved, damaged, retired |

---

### Primary Key Convention

All tables use UUID primary keys. The PK column is named `id` for all tables.

---

### PostGIS Geometry Columns

Several fiber plant tables have spatial data (SRID 4326 = WGS84):

| Table | Column | Geometry Type |
|-------|--------|--------------|
| `fdh_cabinets` | `geom` | POINT |
| `fiber_splice_closures` | `geom` | POINT |
| `fiber_access_points` | `geom` | POINT |
| `fiber_segments` | `route_geom` | LINESTRING |

**Spatial query examples:**
```sql
-- Find closures within 500m of a point (lat 9.05, lng 7.49)
SELECT name, ST_Distance(
    geom::geography,
    ST_SetSRID(ST_MakePoint(7.49, 9.05), 4326)::geography
) AS distance_m
FROM fiber_splice_closures
WHERE ST_DWithin(
    geom::geography,
    ST_SetSRID(ST_MakePoint(7.49, 9.05), 4326)::geography,
    500
)
ORDER BY distance_m;

-- Total fiber length by segment type
SELECT segment_type, COUNT(*), SUM(length_m) AS total_meters
FROM fiber_segments
WHERE is_active = true
GROUP BY segment_type;
```

---

### Cross-Domain Relationship Map

```
    people (Person)
    ├── subscribers (person_id)
    ├── crm_conversations (person_id)
    ├── crm_leads (person_id)
    ├── crm_quotes (person_id)
    ├── crm_agents (person_id)
    ├── tickets (created_by_person_id, assigned_to_person_id, customer_person_id)
    ├── projects (created_by_person_id, owner_person_id, manager_person_id)
    └── work_orders (assigned_to_person_id)

    subscribers
    ├── tickets (subscriber_id)
    ├── projects (subscriber_id)
    └── work_orders (subscriber_id)

    tickets
    ├── ticket_comments (ticket_id)
    ├── ticket_sla_events (ticket_id)
    ├── ticket_assignees (ticket_id, person_id)
    ├── crm_conversations (ticket_id)
    ├── project_tasks (ticket_id)
    └── work_orders (ticket_id)

    projects
    ├── project_tasks (project_id)
    ├── project_comments (project_id)
    └── work_orders (project_id)

    crm_conversations
    ├── crm_messages (conversation_id)
    ├── crm_conversation_assignments (conversation_id)
    └── crm_conversation_tags (conversation_id)

    crm_leads
    └── crm_quotes (lead_id)
        └── crm_quote_line_items (quote_id)

    crm_campaigns
    ├── crm_campaign_steps (campaign_id)
    └── crm_campaign_recipients (campaign_id, person_id)

    Fiber network chain:
    olt_devices → olt_shelves → olt_cards → olt_card_ports
                                                ↓
    pon_ports ←→ splitter_ports ← splitters ← fdh_cabinets
         ↓
    ont_assignments → ont_units
         ↓
    fiber_strands → fiber_segments (PostGIS routes)
         ↓
    fiber_splice_closures → fiber_splice_trays → fiber_splices
```

---

### Entity Lifecycle Flows

**Ticket:**
```
new → open → pending / waiting_on_customer / on_hold → resolved → closed
                                                    ↗
                                              canceled
```

**Project:**
```
open → planned → active → on_hold → completed
                                   ↗
                            canceled
```

**Project Task:**
```
backlog → todo → in_progress → blocked → done
                                       ↗
                                canceled
```

**Lead (Sales):**
```
new → contacted → qualified → proposal → negotiation → won
                                                      ↗
                                                   lost
```

**Quote:**
```
draft → sent → accepted / rejected / expired
```

**Campaign:**
```
draft → scheduled → sending → sent → completed
                                   ↗
                            cancelled
```

**Conversation:**
```
open → pending → snoozed → resolved
       ↕ (can reopen)
```

**Subscriber:**
```
pending → active → suspended → terminated
```

**Work Order:**
```
draft → scheduled → dispatched → in_progress → completed
                                              ↗
                                       canceled
```

---

### Business Query Cookbook

**Copy and adapt these queries. They use correct joins, indexes, and soft-delete filters.**

#### Tickets

```sql
-- Ticket volume by status
SELECT status, COUNT(*) AS ticket_count
FROM tickets WHERE is_active = true
GROUP BY status ORDER BY ticket_count DESC;

-- Average resolution time by priority (last 30 days)
SELECT priority,
    COUNT(*) AS resolved_count,
    ROUND(AVG(EXTRACT(EPOCH FROM (resolved_at - created_at)) / 3600), 1) AS avg_hours
FROM tickets
WHERE is_active = true AND resolved_at IS NOT NULL
    AND resolved_at >= CURRENT_DATE - 30
GROUP BY priority ORDER BY avg_hours;

-- Top 10 subscribers by ticket count
SELECT s.subscriber_number, p.first_name, p.last_name,
    COUNT(t.id) AS ticket_count,
    COUNT(*) FILTER (WHERE t.status IN ('new', 'open', 'pending')) AS open_tickets
FROM tickets t
JOIN subscribers s ON s.id = t.subscriber_id
JOIN people p ON p.id = s.person_id
WHERE t.is_active = true
GROUP BY s.id, s.subscriber_number, p.first_name, p.last_name
ORDER BY ticket_count DESC LIMIT 10;

-- SLA compliance (tickets resolved before due_at)
SELECT
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE resolved_at <= due_at) AS on_time,
    COUNT(*) FILTER (WHERE resolved_at > due_at) AS breached,
    ROUND(
        COUNT(*) FILTER (WHERE resolved_at <= due_at) * 100.0 /
        NULLIF(COUNT(*), 0), 1
    ) AS compliance_pct
FROM tickets
WHERE is_active = true AND resolved_at IS NOT NULL AND due_at IS NOT NULL
    AND resolved_at >= CURRENT_DATE - 30;
```

#### CRM / Sales Pipeline

```sql
-- Pipeline summary (leads by stage)
SELECT ps.name AS stage, ps.order_index,
    COUNT(l.id) AS lead_count,
    SUM(l.estimated_value) AS total_value
FROM crm_leads l
JOIN crm_pipeline_stages ps ON ps.id = l.stage_id
WHERE l.is_active = true
GROUP BY ps.name, ps.order_index
ORDER BY ps.order_index;

-- Conversion rate (won / total leads, last 90 days)
SELECT
    COUNT(*) AS total_leads,
    COUNT(*) FILTER (WHERE status = 'won') AS won,
    COUNT(*) FILTER (WHERE status = 'lost') AS lost,
    ROUND(
        COUNT(*) FILTER (WHERE status = 'won') * 100.0 /
        NULLIF(COUNT(*), 0), 1
    ) AS conversion_pct
FROM crm_leads
WHERE is_active = true AND created_at >= CURRENT_DATE - 90;

-- Campaign performance
SELECT c.name, c.channel, c.status,
    c.total_recipients, c.sent_count, c.delivered_count,
    c.opened_count, c.clicked_count,
    ROUND(c.delivered_count * 100.0 / NULLIF(c.sent_count, 0), 1) AS delivery_pct,
    ROUND(c.opened_count * 100.0 / NULLIF(c.delivered_count, 0), 1) AS open_pct
FROM crm_campaigns c
WHERE c.status IN ('sent', 'completed')
ORDER BY c.completed_at DESC LIMIT 10;
```

#### CRM Inbox / Conversations

```sql
-- Conversation volume by channel
SELECT m.channel_type, COUNT(DISTINCT m.conversation_id) AS conversations,
    COUNT(m.id) AS messages
FROM crm_messages m
WHERE m.created_at >= CURRENT_DATE - 30
GROUP BY m.channel_type ORDER BY conversations DESC;

-- Agent workload (assigned conversations)
SELECT a.id, p.first_name, p.last_name,
    COUNT(DISTINCT ca.conversation_id) AS assigned_conversations,
    COUNT(DISTINCT ca.conversation_id) FILTER (
        WHERE c.status = 'open'
    ) AS open_conversations
FROM crm_agents a
JOIN people p ON p.id = a.person_id
LEFT JOIN crm_conversation_assignments ca ON ca.agent_id = a.id
LEFT JOIN crm_conversations c ON c.id = ca.conversation_id
WHERE a.is_active = true
GROUP BY a.id, p.first_name, p.last_name
ORDER BY open_conversations DESC;

-- Average first response time (last 7 days)
SELECT
    DATE(c.created_at) AS day,
    COUNT(*) AS conversations,
    ROUND(AVG(EXTRACT(EPOCH FROM (
        (SELECT MIN(m2.created_at) FROM crm_messages m2
         WHERE m2.conversation_id = c.id AND m2.direction = 'outbound')
        - c.created_at
    )) / 60), 1) AS avg_first_response_minutes
FROM crm_conversations c
WHERE c.created_at >= CURRENT_DATE - 7
GROUP BY DATE(c.created_at)
ORDER BY day;
```

#### Fiber Network

```sql
-- OLT device summary
SELECT d.name, d.hostname, d.vendor, d.model,
    COUNT(DISTINCT s.id) AS shelves,
    COUNT(DISTINCT c.id) AS cards,
    COUNT(DISTINCT cp.id) AS ports
FROM olt_devices d
LEFT JOIN olt_shelves s ON s.olt_id = d.id AND s.is_active = true
LEFT JOIN olt_cards c ON c.shelf_id = s.id AND c.is_active = true
LEFT JOIN olt_card_ports cp ON cp.card_id = c.id AND cp.is_active = true
WHERE d.is_active = true
GROUP BY d.id, d.name, d.hostname, d.vendor, d.model
ORDER BY d.name;

-- Fiber strand utilization
SELECT status, COUNT(*) AS strand_count
FROM fiber_strands WHERE is_active = true
GROUP BY status ORDER BY strand_count DESC;

-- FDH cabinets with splitter counts
SELECT f.code, f.name,
    COUNT(sp.id) AS splitter_count,
    ST_Y(f.geom) AS latitude, ST_X(f.geom) AS longitude
FROM fdh_cabinets f
LEFT JOIN splitters sp ON sp.fdh_id = f.id AND sp.is_active = true
WHERE f.is_active = true
GROUP BY f.id, f.code, f.name, f.geom
ORDER BY f.code;
```

#### Projects

```sql
-- Active projects with task progress
SELECT p.name, p.project_type, p.status,
    COUNT(pt.id) AS total_tasks,
    COUNT(pt.id) FILTER (WHERE pt.status = 'done') AS done_tasks,
    ROUND(
        COUNT(pt.id) FILTER (WHERE pt.status = 'done') * 100.0 /
        NULLIF(COUNT(pt.id), 0), 1
    ) AS progress_pct
FROM projects p
LEFT JOIN project_tasks pt ON pt.project_id = p.id AND pt.is_active = true
WHERE p.is_active = true AND p.status IN ('active', 'planned')
GROUP BY p.id, p.name, p.project_type, p.status
ORDER BY progress_pct;

-- Overdue tasks
SELECT pt.title, p.name AS project, pt.status, pt.due_at,
    CURRENT_DATE - pt.due_at::date AS days_overdue
FROM project_tasks pt
JOIN projects p ON p.id = pt.project_id
WHERE pt.is_active = true
    AND pt.status NOT IN ('done', 'canceled')
    AND pt.due_at < CURRENT_TIMESTAMP
ORDER BY days_overdue DESC LIMIT 25;
```

#### Subscribers

```sql
-- Subscriber status breakdown
SELECT status, COUNT(*) FROM subscribers
WHERE is_active = true GROUP BY status;

-- Recently churned (terminated in last 30 days)
SELECT s.subscriber_number, p.first_name, p.last_name,
    s.service_plan, s.terminated_at
FROM subscribers s
JOIN people p ON p.id = s.person_id
WHERE s.status = 'terminated'
    AND s.terminated_at >= CURRENT_DATE - 30
ORDER BY s.terminated_at DESC;
```

---

### Output Formatting Conventions

When presenting query results:
- **Currency**: `NGN 1,234,567.89` (comma thousands, 2 decimals)
- **Negative amounts**: Parentheses: `NGN (1,234.56)`
- **Dates**: `DD MMM YYYY` (e.g., `19 Feb 2026`)
- **Percentages**: 1 decimal: `85.3%`
- **Row counts**: Comma-separated: `1,234`
- **NULL / zero**: Em dash `---`
- **Durations**: hours+minutes for short, days for long

### Soft Deletes

Most tables use `is_active = true` for active records. **Always filter** by
`is_active = true` unless explicitly analyzing deleted/archived records.

### Safety Rules

- **NEVER** run INSERT, UPDATE, DELETE, DROP, TRUNCATE, or ALTER
- **Always** use LIMIT (default 25, max 100)
- For large result sets, run `SELECT COUNT(*)` first
- Don't expose PII (mask emails, phone numbers) in outputs
- When querying amounts, note the currency (NGN)
- Avoid `SELECT *` on large tables — list columns explicitly
- For PostGIS queries, cast to `::geography` for meter-based distance calculations

### Detailed Column Reference

See [SCHEMA_REF.md](SCHEMA_REF.md) for auto-generated complete column-level
details. Run `python scripts/generate_schema_skill.py` to regenerate after migrations.
