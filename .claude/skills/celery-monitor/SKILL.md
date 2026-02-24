---
name: celery-monitor
description: Inspect Celery task queues, worker health, and failed tasks using Redis MCP
arguments:
  - name: query
    description: "What to check (e.g. 'queue depth', 'failed tasks', 'worker status', 'stuck tasks')"
---

# Celery Task Queue Monitor

Inspect the Celery task queue system via the Redis MCP server.

## How It Works

Celery uses Redis as both **broker** (task queue) and **result backend**.
The `redis` MCP server provides direct access to these Redis data structures.

## Key Redis Keys

| Key Pattern | Type | Purpose |
|-------------|------|---------|
| `celery` | list | Default task queue (pending tasks) |
| `celery-task-meta-*` | string | Task results/state by task ID |
| `_kombu.binding.*` | set | Queue bindings and routing |
| `unacked_mutex` | string | Worker acknowledgment lock |

## Inspection Steps

### 1. Queue Depth (Pending Tasks)
Use the Redis MCP `list` tools to check queue length:
- Check length of `celery` key (default queue)
- A growing queue means workers can't keep up

### 2. Active Workers
Check for worker heartbeat keys:
- Pattern: `celery-worker-heartbeat-*`
- Presence = worker is alive

### 3. Failed Tasks
Search for task results with failure state:
- Key pattern: `celery-task-meta-*`
- Value is JSON with `status`, `result`, `traceback` fields
- `status: "FAILURE"` indicates a failed task

### 4. Stuck Tasks
Look for tasks that have been running too long:
- Check task meta keys with `status: "STARTED"` and old timestamps
- Compare `date_done` or started timestamp against current time

### 5. Result Backend Cleanup
Check how many result keys exist:
- Large numbers of `celery-task-meta-*` keys = results not being cleaned up
- Default expiry is 24h but verify

## Common Queries

**"Is Celery healthy?"**
1. Check queue depth (should be low or zero in steady state)
2. Check for worker heartbeats
3. Check for recent failures

**"Why is task X not running?"**
1. Check if it's in the queue (pending)
2. Check if workers are alive
3. Check if it failed (look at result backend)
4. Check Docker container health: `docker` MCP → `fetch_container_logs` for `dotmac_omni_celery_worker`

**"What tasks are scheduled?"**
1. Check the beat schedule in Redis or via:
   ```bash
   docker compose exec celery-worker python -c "
   from app.celery_app import celery_app
   print(celery_app.conf.beat_schedule)
   "
   ```

## Combining with Other MCPs

- **Docker MCP**: Fetch celery-worker container logs for error details
- **DB MCP**: Query `scheduled_tasks` table for beat schedule config
- **DB MCP**: Cross-reference task results with business data (e.g., sync results vs subscriber counts)
