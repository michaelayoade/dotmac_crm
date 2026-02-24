---
name: add-celery-task
description: Create a new Celery background task with proper patterns
arguments:
  - name: task_info
    description: "Task name and purpose (e.g. 'sync_subscribers for Splynx reconciliation')"
---

# Add Celery Task

Create a new background task for DotMac Omni CRM.

## Steps

### 1. Determine the module
Parse `$ARGUMENTS` to identify:
- Task name
- Domain module (subscribers, tickets, crm, network, etc.)
- Whether it's periodic (needs beat schedule) or on-demand

### 2. Read reference pattern
Read `app/tasks/subscribers.py` for the established pattern.

### 3. Create the task
Add to `app/tasks/{module}.py`:

```python
import time
from typing import Any

from app.celery_app import celery_app
from app.db import SessionLocal
from app.logging import get_logger
from app.metrics import observe_job

logger = get_logger(__name__)


@celery_app.task(name="app.tasks.{module}.task_name")
def task_name() -> dict[str, Any]:
    """Brief description of what this task does."""
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger.info("TASK_NAME_START")

    results: dict[str, Any] = {"processed": 0, "errors": []}

    try:
        # Import service inside task (avoid circular imports)
        from app.services.{module} import some_service

        items = some_service.list_items_to_process(session)

        # Batch-load related entities to avoid N+1
        # e.g. person_by_id = {p.id: p for p in session.query(Person).filter(...).all()}

        for item in items:
            try:
                some_service.process_item(session, item)
                results["processed"] += 1
            except Exception as e:
                session.rollback()
                results["errors"].append({"id": str(item.id), "error": str(e)})
                logger.error(
                    "task_name_item_error id=%s error=%s",
                    item.id,
                    str(e),
                )

        session.commit()

        logger.info(
            "TASK_NAME_COMPLETE processed=%d errors=%d",
            results["processed"],
            len(results["errors"]),
        )

    except Exception as e:
        status = "error"
        logger.error("TASK_NAME_ERROR error=%s", str(e))
        session.rollback()
        raise

    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("task_name", status, duration)

    return results
```

### 4. Key rules
- **Services do the work** — task only orchestrates
- **Create `SessionLocal()` at start**, close in `finally`
- **Import inside task** — avoids circular imports at module load
- **Return statistics dict** — for monitoring and visibility
- **Log at start/end** — with counts (`TASK_NAME_START`, `TASK_NAME_COMPLETE`)
- **Catch exceptions per item** — one failure shouldn't stop the batch
- **Rollback per-item** on error, commit at end
- **Batch-load related entities** before the loop to avoid N+1
- **Use `observe_job()`** for metrics tracking

### 5. Register in Celery autodiscovery
Ensure the tasks module is discovered. Check `app/celery_app.py`:
```python
celery_app.autodiscover_tasks(["app.tasks"])
```

### 6. Add beat schedule (if periodic)
Use `_sync_scheduled_task()` in `app/services/scheduler_config.py`:

```python
_sync_scheduled_task(
    db,
    name="Task Display Name",
    task_name="app.tasks.{module}.task_name",
    enabled=_effective_bool(db, SettingDomain.domain, "key", "ENV_VAR", default=True),
    interval_seconds=3600,  # 1 hour
)
```

Or for crontab-based scheduling, add directly to the Celery beat config.

### 7. Test
```bash
# Test the task function directly (not through Celery)
python -c "from app.tasks.{module} import task_name; print(task_name())"

# Or via Celery (requires worker running)
docker compose exec app python -c "
from app.tasks.{module} import task_name
result = task_name.delay()
print(result.get(timeout=30))
"
```

### 8. Verify
```bash
ruff check app/tasks/{module}.py --fix
ruff format app/tasks/{module}.py
docker compose logs celery-worker --tail=20  # Check for import errors
```
