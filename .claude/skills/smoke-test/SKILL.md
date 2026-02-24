---
name: smoke-test
description: Run a smoke test across the stack using Playwright, DB, and Docker
arguments:
  - name: scope
    description: "What to test (e.g. 'full', 'admin pages', 'crm inbox', 'api endpoints')"
---

# Smoke Test

Verify the application is working end-to-end using Playwright (browser),
Database, and Docker MCP servers.

## Smoke Test Workflow

### Phase 1: Stack Health (Docker MCP)

Use the `docker` MCP server to verify all containers are running:

1. `dotmac_omni_app` — FastAPI application (port 8000)
2. `dotmac_omni_db` — PostgreSQL/PostGIS (port 5432)
3. `dotmac_omni_redis` — Redis (port 6379)
4. `dotmac_omni_celery_worker` — Celery worker
5. `dotmac_omni_celery_beat` — Celery beat scheduler

Check each container's status and health check result.
If any are unhealthy, fetch logs and report the issue immediately.

### Phase 2: Database Health (DB MCP)

Quick queries to verify DB is populated and functional:

```sql
-- Core tables have data
SELECT 'people' AS tbl, COUNT(*) FROM people
UNION ALL SELECT 'subscribers', COUNT(*) FROM subscribers
UNION ALL SELECT 'tickets', COUNT(*) FROM tickets
UNION ALL SELECT 'crm_conversations', COUNT(*) FROM crm_conversations
UNION ALL SELECT 'projects', COUNT(*) FROM projects;

-- Recent activity (app is being used)
SELECT MAX(created_at) AS last_ticket FROM tickets;
SELECT MAX(created_at) AS last_message FROM crm_messages;
```

### Phase 3: Application Health (Playwright MCP)

Use the Playwright MCP browser tools:

**1. Health endpoint:**
- Navigate to `http://localhost:8000/health`
- Verify 200 response

**2. Login flow:**
- Navigate to `http://localhost:8000/auth/login`
- Take accessibility snapshot
- Fill login form with environment-provided test credentials (never hardcode credentials in prompts or committed docs)
- Submit
- Verify redirect to `/admin/`

**3. Key admin pages (navigate + snapshot each):**
- `/admin/` — Dashboard
- `/admin/tickets` — Tickets list
- `/admin/crm/inbox` — CRM Inbox
- `/admin/crm/leads` — Sales pipeline
- `/admin/subscribers` — Subscribers
- `/admin/projects` — Projects
- `/admin/network/fiber/map` — Fiber map

For each page:
- Verify it loads (no error page)
- Check for JavaScript console errors
- Take screenshot if failures found

**4. HTMX partials (if testing inbox):**
- Navigate to inbox
- Click a conversation
- Verify message thread loads

### Phase 4: API Health

Via Playwright or direct fetch:
- `GET /health` → 200
- `POST /api/v1/auth/login` with credentials → access_token returned
- `GET /api/v1/tickets?limit=1` with Bearer token → 200 with data

### Report Format

```markdown
## Smoke Test Report
*Run: [timestamp]*

### Stack Health
| Service | Container | Status | Health |
|---------|-----------|--------|--------|
| App | dotmac_omni_app | running | healthy |
| DB | dotmac_omni_db | running | healthy |
| Redis | dotmac_omni_redis | running | healthy |
| Worker | dotmac_omni_celery_worker | running | - |
| Beat | dotmac_omni_celery_beat | running | - |

### Database
| Table | Row Count | Last Activity |
|-------|-----------|---------------|
| people | 1,234 | 19 Feb 2026 |
| tickets | 5,678 | 19 Feb 2026 |

### Pages Tested
| Page | URL | Status | Notes |
|------|-----|--------|-------|
| Login | /auth/login | PASS | |
| Dashboard | /admin/ | PASS | |
| Tickets | /admin/tickets | PASS | |

### Issues Found
- [List any failures, console errors, or unexpected behavior]

### Result: PASS / FAIL
```
