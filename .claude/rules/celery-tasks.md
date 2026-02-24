# Celery Task Rules

## Task Pattern

```python
@celery_app.task(name="app.tasks.module.task_name")
def task_name() -> dict[str, Any]:
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger.info("TASK_NAME_START")
    results = {"processed": 0, "errors": []}

    try:
        from app.services.module import service  # Import inside task
        # ... business logic via service ...
        session.commit()
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        observe_job("task_name", status, time.monotonic() - start)

    return results
```

## Key Rules

1. **Services do the work** — tasks only orchestrate
2. **`SessionLocal()` at start**, `session.close()` in `finally`
3. **Import inside task** — avoids circular imports at module load
4. **Return statistics dict** — for monitoring (`processed`, `errors`)
5. **Log at start/end** — `TASK_NAME_START`, `TASK_NAME_COMPLETE` with counts
6. **Catch per-item exceptions** — one failure shouldn't stop the batch
7. **Rollback per-item** on error, commit at end
8. **Batch-load** related entities before loops (N+1 prevention)
9. **Use `observe_job()`** for metrics

## Beat Schedule

Register periodic tasks via `_sync_scheduled_task()` in `app/services/scheduler_config.py`.

## Notification Tasks

```python
from app.services.notification import NotificationService
notification_service = NotificationService()
notification_service.create(db, recipient_id, ...)
```

Always check for duplicate notifications before sending.
