---
name: debug-stack
description: Debug production issues using Docker logs, database queries, and Redis inspection
arguments:
  - name: issue
    description: "What's wrong (e.g. '500 error on /admin/tickets', 'subscriber sync failing', 'emails not sending')"
---

# Debug Stack

Diagnose production issues by combining Docker, Database, and Redis MCP servers.

## Diagnostic Workflow

### Step 1: Identify the Error (Docker MCP)

Use the `docker` MCP server to fetch container logs:

**For app errors (500s, route failures):**
- Container: `dotmac_omni_app`
- Look for: Python tracebacks, FastAPI error responses

**For task failures (Celery):**
- Container: `dotmac_omni_celery_worker`
- Look for: Task exceptions, connection errors

**For scheduler issues:**
- Container: `dotmac_omni_celery_beat`
- Look for: Schedule registration errors, missed beats

**For database issues:**
- Container: `dotmac_omni_db`
- Look for: Connection limits, slow queries, lock timeouts

### Step 2: Trace the Data (DB MCP)

Once you know which entity/operation failed, query the database via `omni-db`:

**Ticket stuck in wrong state:**
```sql
SELECT id, status, assigned_to_person_id, created_at, updated_at
FROM tickets WHERE id = '<ticket_id>';
-- Check related: comments, SLA events, assignees
```

**Subscriber sync mismatch:**
```sql
SELECT external_id, external_system, last_synced_at, sync_error
FROM subscribers WHERE external_id = '<splynx_id>';
```

**Campaign not sending:**
```sql
SELECT c.status, c.total_recipients, c.sent_count, c.sending_started_at,
    cr.status, cr.failed_reason
FROM crm_campaigns c
LEFT JOIN crm_campaign_recipients cr ON cr.campaign_id = c.id AND cr.status = 'failed'
WHERE c.id = '<campaign_id>'
LIMIT 10;
```

**Notification delivery failures:**
```sql
SELECT n.id, n.channel, n.status, n.last_error, n.retry_count,
    nd.status AS delivery_status, nd.response_body
FROM notifications n
LEFT JOIN notification_deliveries nd ON nd.notification_id = n.id
WHERE n.status = 'failed'
ORDER BY n.created_at DESC LIMIT 10;
```

### Step 3: Check Cache/Queue State (Redis MCP)

Use the `redis` MCP server:

**Rate limiting issues:**
- Check rate limit counters (pattern: `rate_limit:*`)
- If a user/IP is being blocked, inspect the counter value and TTL

**Stale cache:**
- Branding cache, settings cache
- Check TTL and value freshness

**Celery task state:**
- Check `celery-task-meta-<task_id>` for task result/error
- Check queue depth for backlogs

### Step 4: Cross-Reference and Diagnose

Combine findings from all three sources:

1. **Docker logs** → identify the error type and timestamp
2. **Database** → verify data state (is the entity in an unexpected state?)
3. **Redis** → check if cache/queue issues contributed

### Common Issue Patterns

| Symptom | Docker Check | DB Check | Redis Check |
|---------|-------------|----------|-------------|
| 500 on page | App logs traceback | Query the entity | Check cache freshness |
| Task not running | Worker logs | `scheduled_tasks` table | Queue depth |
| Emails not sending | Worker logs | `notifications` table | Celery result |
| Slow page load | App logs timing | EXPLAIN query plans | Cache hit/miss |
| Auth failures | App logs 401/403 | `people` + `user_roles` | Session/token state |
| Sync failures | Worker logs | `subscribers.sync_error` | Task results |

### Output Format

Present findings as:
```markdown
## Diagnosis: [Issue Summary]

### Error
[Exact error from logs]

### Root Cause
[What went wrong and why]

### Affected Data
[Which entities/records are impacted]

### Fix
[Step-by-step resolution]

### Prevention
[How to prevent recurrence]
```
