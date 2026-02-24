---
name: db-report
description: Generate formatted analytical reports from the Omni CRM database with tables, summaries, and optional chart code
arguments:
  - name: report
    description: "What report to generate (e.g. 'ticket SLA compliance', 'CRM pipeline health', 'fiber build-out progress', 'agent performance', 'subscriber churn')"
---

# Database Analytics Report Generator

Generate a polished, formatted analytical report from the DotMac Omni CRM database.
Use the `omni-db` MCP server (`execute_sql` tool) for all queries.

## How to Generate a Report

### Step 1: Understand the Request

Parse the user's report request. Common report types:

| Report Category | Key Tables | Typical Dimensions |
|----------------|------------|-------------------|
| Ticket SLA & Volume | `tickets`, `ticket_sla_events`, `ticket_assignees` | status, priority, agent, team, date |
| CRM Pipeline | `crm_leads`, `crm_pipeline_stages`, `crm_quotes` | stage, agent, value, date |
| Campaign Performance | `crm_campaigns`, `crm_campaign_recipients` | channel, status, delivery/open rates |
| Inbox / Conversations | `crm_conversations`, `crm_messages`, `crm_conversation_assignments` | channel, agent, response time |
| Agent Performance | `crm_agent_presence`, `crm_agent_presence_events`, `ticket_assignees` | agent, status, duration |
| Subscriber Health | `subscribers`, `people` | status, plan, region, churn |
| Project Progress | `projects`, `project_tasks` | type, status, completion %, overdue |
| Fiber Network | `olt_devices`, `fdh_cabinets`, `fiber_segments`, `fiber_strands` | device, utilization, geography |
| Work Orders | `work_orders`, `work_order_assignments` | type, status, technician |

### Step 2: Query the Database

**No org_id filter needed** — this is a single-tenant application.

**Always filter soft deletes:**
```sql
WHERE is_active = true
```

**For time-series reports**, use `DATE_TRUNC('month', date_column)` for grouping.

**For large tables**, use LIMIT and filter on indexed columns. Check approximate
row counts first:
```sql
SELECT reltuples::bigint FROM pg_class WHERE relname = 'tickets';
```

### Step 3: Format the Report

Structure every report with these sections:

```markdown
## [Report Title]
*Generated: [current date] | Period: [date range] | Currency: NGN*

### Summary
- **[Key metric 1]**: [value]
- **[Key metric 2]**: [value]
- **[Key metric 3]**: [value]

### Detail
[Formatted markdown table with results]

### Observations
1. [Insight from the data]
2. [Trend or anomaly noticed]
3. [Actionable recommendation if applicable]
```

### Formatting Rules

| Data Type | Format | Example |
|-----------|--------|---------|
| Currency (NGN) | Comma-separated, 2 decimals | `NGN 1,234,567.89` |
| Dates | DD MMM YYYY | `19 Feb 2026` |
| Percentages | 1 decimal | `85.3%` |
| Durations (hours) | 1 decimal + unit | `4.5 hours` |
| Durations (minutes) | Integer + unit | `23 min` |
| Row counts | Comma-separated | `1,234` |
| NULL / zero | Em dash | `---` |
| Month labels | MMM YYYY | `Jan 2026` |

**Table alignment**: Left-align text, right-align numbers, center statuses.

### Step 4: Optional Chart Code

If the report has a time-series or comparison dimension, offer Chart.js:

```javascript
new Chart(ctx, {
  type: 'bar',
  data: {
    labels: ['Jan 2026', 'Feb 2026'],
    datasets: [{
      label: 'Tickets',
      data: [142, 168],
      backgroundColor: 'rgba(6, 182, 212, 0.5)',  // cyan-500
      borderColor: 'rgb(6, 182, 212)',
      borderWidth: 1
    }]
  },
  options: { responsive: true }
});
```

**Chart color palette** (from design system):
- Primary: `rgb(6, 182, 212)` (cyan-500 / teal)
- Accent: `rgb(249, 115, 22)` (orange-500)
- Success: `rgb(16, 185, 129)` (emerald-500)
- Danger: `rgb(239, 68, 68)` (red-500)
- Info: `rgb(59, 130, 246)` (blue-500)
- Neutral: `rgb(100, 116, 139)` (slate-500)
- Violet: `rgb(139, 92, 246)` (violet-500)

## Pre-built Report Templates

### Ticket SLA Compliance
```sql
SELECT
    DATE_TRUNC('week', t.created_at) AS week,
    COUNT(*) AS total_tickets,
    COUNT(*) FILTER (WHERE t.resolved_at IS NOT NULL) AS resolved,
    COUNT(*) FILTER (WHERE t.resolved_at IS NOT NULL AND t.due_at IS NOT NULL
        AND t.resolved_at <= t.due_at) AS on_time,
    COUNT(*) FILTER (WHERE t.resolved_at IS NOT NULL AND t.due_at IS NOT NULL
        AND t.resolved_at > t.due_at) AS breached,
    ROUND(
        COUNT(*) FILTER (WHERE t.resolved_at <= t.due_at) * 100.0 /
        NULLIF(COUNT(*) FILTER (WHERE t.resolved_at IS NOT NULL AND t.due_at IS NOT NULL), 0),
    1) AS sla_pct
FROM tickets t
WHERE t.is_active = true AND t.created_at >= CURRENT_DATE - INTERVAL '12 weeks'
GROUP BY DATE_TRUNC('week', t.created_at)
ORDER BY week;
```

### Ticket Volume by Agent
```sql
SELECT p.first_name || ' ' || p.last_name AS agent,
    COUNT(t.id) AS total,
    COUNT(t.id) FILTER (WHERE t.status IN ('new', 'open', 'pending')) AS open_now,
    COUNT(t.id) FILTER (WHERE t.status = 'resolved') AS resolved,
    ROUND(AVG(EXTRACT(EPOCH FROM (t.resolved_at - t.created_at)) / 3600)
        FILTER (WHERE t.resolved_at IS NOT NULL), 1) AS avg_resolution_hours
FROM tickets t
JOIN people p ON p.id = t.assigned_to_person_id
WHERE t.is_active = true AND t.created_at >= CURRENT_DATE - 30
GROUP BY p.id, p.first_name, p.last_name
ORDER BY total DESC LIMIT 20;
```

### CRM Pipeline Health
```sql
SELECT ps.name AS stage, ps.order_index,
    COUNT(l.id) AS leads,
    SUM(l.estimated_value) AS pipeline_value,
    ROUND(AVG(l.probability), 0) AS avg_probability,
    SUM(l.estimated_value * l.probability / 100.0) AS weighted_value
FROM crm_leads l
JOIN crm_pipeline_stages ps ON ps.id = l.stage_id
WHERE l.is_active = true AND l.status NOT IN ('won', 'lost')
GROUP BY ps.id, ps.name, ps.order_index
ORDER BY ps.order_index;
```

### Lead Conversion Funnel
```sql
SELECT
    DATE_TRUNC('month', created_at) AS month,
    COUNT(*) AS new_leads,
    COUNT(*) FILTER (WHERE status IN ('contacted', 'qualified', 'proposal', 'negotiation', 'won')) AS contacted,
    COUNT(*) FILTER (WHERE status IN ('qualified', 'proposal', 'negotiation', 'won')) AS qualified,
    COUNT(*) FILTER (WHERE status = 'won') AS won,
    COUNT(*) FILTER (WHERE status = 'lost') AS lost,
    ROUND(COUNT(*) FILTER (WHERE status = 'won') * 100.0 / NULLIF(COUNT(*), 0), 1) AS win_rate
FROM crm_leads
WHERE is_active = true AND created_at >= CURRENT_DATE - INTERVAL '6 months'
GROUP BY DATE_TRUNC('month', created_at)
ORDER BY month;
```

### Inbox Channel Distribution
```sql
SELECT m.channel_type,
    COUNT(DISTINCT m.conversation_id) AS conversations,
    COUNT(m.id) AS total_messages,
    COUNT(m.id) FILTER (WHERE m.direction = 'inbound') AS inbound,
    COUNT(m.id) FILTER (WHERE m.direction = 'outbound') AS outbound
FROM crm_messages m
WHERE m.created_at >= CURRENT_DATE - 30
GROUP BY m.channel_type
ORDER BY conversations DESC;
```

### Subscriber Growth & Churn
```sql
SELECT
    DATE_TRUNC('month', s.created_at) AS month,
    COUNT(*) FILTER (WHERE s.status = 'active') AS active_total,
    COUNT(*) FILTER (WHERE s.activated_at >= DATE_TRUNC('month', s.created_at)) AS new_activations,
    COUNT(*) FILTER (WHERE s.terminated_at >= DATE_TRUNC('month', s.created_at)
        AND s.terminated_at < DATE_TRUNC('month', s.created_at) + INTERVAL '1 month') AS churned
FROM subscribers s
WHERE s.created_at >= CURRENT_DATE - INTERVAL '12 months'
GROUP BY DATE_TRUNC('month', s.created_at)
ORDER BY month;
```

### Fiber Build-Out Progress
```sql
SELECT
    segment_type,
    COUNT(*) AS segment_count,
    ROUND(SUM(length_m) / 1000.0, 2) AS total_km,
    COUNT(*) FILTER (WHERE is_active = true) AS active_segments
FROM fiber_segments
GROUP BY segment_type
ORDER BY total_km DESC;
```

### Agent Presence Summary (Today)
```sql
SELECT p.first_name || ' ' || p.last_name AS agent,
    ap.status AS current_status,
    ap.last_seen_at,
    EXTRACT(EPOCH FROM (NOW() - ap.last_seen_at)) / 60 AS minutes_since_seen
FROM crm_agent_presence ap
JOIN crm_agents a ON a.id = ap.agent_id
JOIN people p ON p.id = a.person_id
WHERE a.is_active = true
ORDER BY ap.last_seen_at DESC NULLS LAST;
```

### Project Delivery Tracker
```sql
SELECT p.name, p.project_type, p.status,
    p.start_at, p.due_at,
    CASE WHEN p.due_at < CURRENT_TIMESTAMP AND p.status NOT IN ('completed', 'canceled')
        THEN CURRENT_DATE - p.due_at::date ELSE 0 END AS days_overdue,
    COUNT(pt.id) AS total_tasks,
    COUNT(pt.id) FILTER (WHERE pt.status = 'done') AS done,
    ROUND(COUNT(pt.id) FILTER (WHERE pt.status = 'done') * 100.0 /
        NULLIF(COUNT(pt.id), 0), 1) AS pct_complete
FROM projects p
LEFT JOIN project_tasks pt ON pt.project_id = p.id AND pt.is_active = true
WHERE p.is_active = true AND p.status NOT IN ('completed', 'canceled')
GROUP BY p.id, p.name, p.project_type, p.status, p.start_at, p.due_at
ORDER BY days_overdue DESC, pct_complete ASC;
```

## Safety Rules

- NEVER run INSERT, UPDATE, DELETE, DROP, TRUNCATE, or ALTER
- Always use LIMIT (default 25, max 100) on detail queries
- Don't expose PII (mask emails, phone numbers) in report output
- Always note the currency (NGN) when presenting financial figures
- For tables >100K rows, filter on indexed columns
- Always filter `is_active = true` unless analyzing deleted records
