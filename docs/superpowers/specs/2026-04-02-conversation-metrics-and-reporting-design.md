# Conversation Metrics, SLA Tracking & Agent Performance Reporting

**Date:** 2026-04-02
**Status:** Draft

## Problem

Conversation measurement gaps prevent SLA tracking and agent performance analysis:
- `first_response_at` is unpopulated — no response time tracking
- Zero CSAT ratings recorded despite survey infrastructure existing
- Zero conversation tags used — no categorization data
- No ticket linkage enforcement
- No automated reporting or SLA breach detection

## Goals

1. Populate `first_response_at` when the first agent-authored outbound message is sent
2. Fix the existing CSAT survey flow and add inline CSAT prompts post-resolution
3. Soft-nudge agents to add tags before resolving conversations
4. Build daily data quality alerts (in-app) for conversations missing fields
5. Implement per-priority SLA targets with breach alerting
6. Build a weekly agent performance report page in admin

---

## Section 1: Data Model Changes

### New columns on `Conversation`

| Column | Type | Default | Index | Set When |
|--------|------|---------|-------|----------|
| `first_response_at` | `DateTime(timezone=True)` | null | Yes | First agent-authored outbound message sent |
| `resolved_at` | `DateTime(timezone=True)` | null | Yes | Status transitions to `resolved` |
| `response_time_seconds` | `Integer` | null | No | Computed: `first_response_at - created_at` |
| `resolution_time_seconds` | `Integer` | null | No | Computed: `resolved_at - created_at` |

### Indexes

- `ix_crm_conversations_first_response_at` on `first_response_at`
- `ix_crm_conversations_resolved_at` on `resolved_at`
- `ix_crm_conversations_status_first_response` composite on `(status, first_response_at)` — powers missing-data alerts and SLA breach queries

### New CRM settings (per-priority SLA targets)

Stored in the existing settings table:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `crm_sla_response_urgent_minutes` | int | 60 | Response SLA for urgent priority |
| `crm_sla_response_high_minutes` | int | 240 | Response SLA for high priority |
| `crm_sla_response_medium_minutes` | int | 480 | Response SLA for medium priority |
| `crm_sla_response_low_minutes` | int | 1440 | Response SLA for low/none priority |
| `crm_sla_resolution_urgent_minutes` | int | 240 | Resolution SLA for urgent priority |
| `crm_sla_resolution_high_minutes` | int | 1440 | Resolution SLA for high priority |
| `crm_sla_resolution_medium_minutes` | int | 2880 | Resolution SLA for medium priority |
| `crm_sla_resolution_low_minutes` | int | 4320 | Resolution SLA for low/none priority |

---

## Section 2: first_response_at Population

### Trigger point

In the outbound message service layer, after an outbound message record is persisted against a conversation.

### Logic

1. When an outbound message is created on a conversation:
   - Check `message.direction == outbound`
   - Check the author is a `CrmAgent` (not system, not AI intake) — verified by checking `author_id` against the `crm_agents` table
   - Check `conversation.first_response_at is null` (only set once)
2. If all conditions met:
   - Set `conversation.first_response_at = message.sent_at or now(UTC)`
   - Compute `conversation.response_time_seconds = int((first_response_at - conversation.created_at).total_seconds())`
   - Committed as part of the same transaction

### Backfill

One-time migration script: for each conversation, find the earliest `Message` with `direction=outbound` where `author_id` matches a `CrmAgent`. Populate `first_response_at` and `response_time_seconds`.

---

## Section 3: resolved_at + Resolution Tracking

### Trigger point

In `update_conversation_status()` (`app/services/crm/inbox/conversation_status.py`) when status transitions to `resolved`.

### Logic

1. When new status is `resolved`:
   - Set `conversation.resolved_at = now(UTC)`
   - Compute `conversation.resolution_time_seconds = int((resolved_at - conversation.created_at).total_seconds())`
2. When a resolved conversation is reopened (status changes away from `resolved`):
   - Clear `conversation.resolved_at = None`
   - Clear `conversation.resolution_time_seconds = None`
   - Ensures the metric reflects the final resolution

### Backfill

Query conversations with `status = resolved` and `resolved_at is null`. Use `updated_at` as best-effort approximation.

---

## Section 4: CSAT — Fix Existing + Add Inline Prompt

### Part A: Fix existing survey flow

Investigate and fix `queue_for_resolved_conversation()`. The infrastructure exists but zero ratings are recorded. Likely issues:
- No active survey configured for conversation resolution triggers
- Survey invitation queued but never sent (outbound channel not wired)
- Response capture endpoint missing or broken

Fix the full chain: resolve -> queue invitation -> send via conversation channel -> capture response -> update `SurveyResponse`.

### Part B: Inline CSAT prompt

After resolution, send a quick-rating message in the same channel:

1. When conversation resolves and CSAT is enabled for that integration target (via existing `get_enabled_map()`):
   - Queue an outbound message with a channel-appropriate CSAT prompt
   - **Email:** HTML with clickable 1-5 star links pointing to a response endpoint
   - **WhatsApp/chat:** Text message with rating options (interactive buttons where supported, or "Reply 1-5")
2. Response capture:
   - New endpoint: `POST /api/csat/{token}/respond` — accepts rating + optional comment
   - For in-channel replies: detect numeric response (1-5) on a conversation with pending CSAT, auto-capture
3. Store as `SurveyResponse` linked to the conversation's person and invitation

The inline prompt is sent as a system-authored message (not agent-authored) so it does not affect `first_response_at` on any future conversations.

---

## Section 5: Tag Enforcement on Resolution (Soft Nudge)

### Frontend-only enforcement

On the resolve button in the conversation detail view:

1. Check if the conversation has any tags (already loaded in context)
2. If no tags: show a confirmation modal — "This conversation has no tags. Resolve anyway?"
3. Agent can confirm to proceed or cancel to add tags first

### Implementation

- Alpine.js logic on the resolve button
- No backend enforcement — the API still accepts tagless resolution
- Non-blocking for urgent situations

---

## Section 6: Daily Data Quality Alert

### Celery task: `check_conversation_data_quality_task`

Runs daily (scheduled via `scheduler_config.py`).

### Checks

1. Conversations resolved in the last 24 hours with `first_response_at = null` (agent never responded before resolution)
2. Open/pending conversations older than their priority's SLA threshold with `first_response_at = null` (response overdue)
3. Conversations resolved in the last 24 hours with zero tags
4. Conversations resolved in the last 24 hours with no linked CSAT invitation

### Output

In-app notification to team leads via `NotificationService`. One summary notification per check run with counts and a link to a filtered admin view.

### Filtered admin view

Extend the existing conversation list endpoint to accept a `missing` filter parameter (e.g., `missing=first_response,tags,csat`) that returns conversations missing those fields.

---

## Section 7: SLA Breach Alerts

### Celery task: `check_sla_breaches_task`

Runs every 15 minutes (scheduled via `scheduler_config.py`).

### Logic

1. Load per-priority SLA targets from CRM settings
2. Query open/pending conversations where:
   - `first_response_at is null` AND `now() - created_at > response SLA for priority` -> **response breach**
   - `first_response_at is not null` AND `resolved_at is null` AND `now() - created_at > resolution SLA for priority` -> **resolution breach**
3. Deduplication: track `sla_response_breach_alerted_at` and `sla_resolution_breach_alerted_at` in `conversation.metadata_`. Only alert once per breach type per conversation.

### Output

In-app notification to the assigned agent + their team lead. Includes conversation subject, priority, time past SLA, and a direct link.

Stale conversation escalation is covered by the resolution SLA breach — no separate mechanism needed.

---

## Section 8: Weekly Agent Performance Report Page

### Route

`GET /admin/reports/agent-performance`

### Metrics per agent

| Metric | Source | Trend |
|--------|--------|-------|
| Conversations handled (resolved) | Count where `resolved_at` in week range and agent was assigned | vs previous week |
| Median first response time | `response_time_seconds` on resolved conversations | vs previous week |
| Median resolution time | `resolution_time_seconds` on resolved conversations | vs previous week |
| Open backlog | Currently assigned, status = open/pending | vs previous week snapshot |
| CSAT average | `SurveyResponse.rating` linked to agent's conversations | vs previous week |
| SLA breach count | Conversations where response/resolution exceeded target | vs previous week |

### Trend arrows

Green for improvement (lower time = green, higher CSAT = green). Red for regression.

### Threshold flagging

Agents below team median on any metric get a warning indicator. Default threshold: below team median.

### Implementation

- **Service:** New function in `app/services/crm/reports.py` computing all metrics for a date range
- **Route:** `app/web/admin/reports.py` — thin wrapper calling the service
- **Template:** Standard admin page — `stats_card` macros for team-wide summary at top, `data_table` for per-agent breakdown below. Date range picker defaulting to current week.

---

## Out of Scope

- Per-team or per-channel SLA targets (future enhancement)
- Email digest delivery of reports
- Hard-block tag enforcement
- Auto-reassignment on escalation
- Ticket linkage enforcement from conversations
