from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from time import monotonic

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from starlette.requests import Request

from app.config import settings
from app.metrics import (
    observe_db_session_closed,
    observe_db_session_created,
    observe_db_transaction_duration,
    set_db_oldest_transaction_age,
    set_db_pool_state,
    set_db_runtime_sessions,
)

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_engine = None
_request_path_var: ContextVar[str | None] = ContextVar("db_request_path", default=None)
_request_id_var: ContextVar[str | None] = ContextVar("db_request_id", default=None)


def bind_request_db_context(*, path: str | None, request_id: str | None) -> tuple[Token, Token]:
    return (
        _request_path_var.set(path),
        _request_id_var.set(request_id),
    )


def reset_request_db_context(tokens: tuple[Token, Token]) -> None:
    path_token, request_id_token = tokens
    _request_path_var.reset(path_token)
    _request_id_var.reset(request_id_token)


class ObservedSession(Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.info["_observed_session_scope"] = "request" if _request_path_var.get() else "background"
        self.info["_observed_session_open"] = True
        observe_db_session_created(scope=self.info["_observed_session_scope"])

    def close(self) -> None:
        if self.info.get("_observed_session_open"):
            observe_db_session_closed(scope=self.info.get("_observed_session_scope", "background"))
            self.info["_observed_session_open"] = False
        super().close()


def _pool_snapshot(engine) -> None:
    try:
        pool = engine.pool
        checked_out = pool.checkedout() if hasattr(pool, "checkedout") else None
        size = pool.size() if hasattr(pool, "size") else None
        overflow = pool.overflow() if hasattr(pool, "overflow") else None
        set_db_pool_state(checked_out=checked_out, size=size, overflow=overflow)
    except Exception:
        return


def _apply_connection_guardrails(dbapi_connection) -> None:
    statement_timeout_ms = max(int(settings.db_statement_timeout_ms or 0), 0)
    idle_timeout_ms = max(int(settings.db_idle_in_transaction_session_timeout_ms or 0), 0)
    if statement_timeout_ms <= 0 and idle_timeout_ms <= 0:
        return
    cursor = dbapi_connection.cursor()
    try:
        if statement_timeout_ms > 0:
            cursor.execute(f"SET SESSION statement_timeout = {statement_timeout_ms}")
        if idle_timeout_ms > 0:
            cursor.execute(f"SET SESSION idle_in_transaction_session_timeout = {idle_timeout_ms}")
    finally:
        cursor.close()


@event.listens_for(ObservedSession, "after_begin")
def _after_transaction_begin(session: ObservedSession, transaction, connection) -> None:
    if transaction.parent is not None:
        return
    session.info["_transaction_started_at"] = monotonic()
    session.info["_transaction_path"] = _request_path_var.get() or "background"


@event.listens_for(ObservedSession, "after_transaction_end")
def _after_transaction_end(session: ObservedSession, transaction) -> None:
    if transaction.parent is not None:
        return
    started_at = session.info.pop("_transaction_started_at", None)
    path = session.info.pop("_transaction_path", None) or "background"
    if started_at is None:
        return
    observe_db_transaction_duration(
        scope=session.info.get("_observed_session_scope", "background"),
        path=path,
        duration_seconds=monotonic() - started_at,
    )


def get_engine():
    global _engine
    if _engine is None:
        database_url = settings.database_url
        engine_kwargs = {"pool_pre_ping": True, "pool_recycle": settings.db_pool_recycle}
        if make_url(database_url).drivername.startswith("sqlite"):
            _engine = create_engine(database_url, **engine_kwargs)
        else:
            _engine = create_engine(
                database_url,
                **engine_kwargs,
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                pool_timeout=settings.db_pool_timeout,
            )
        _pool_snapshot(_engine)

        @event.listens_for(_engine, "connect")
        def _on_connect(dbapi_connection, connection_record) -> None:
            _apply_connection_guardrails(dbapi_connection)
            _pool_snapshot(_engine)

        @event.listens_for(_engine, "checkout")
        def _on_checkout(dbapi_connection, connection_record, connection_proxy) -> None:
            _pool_snapshot(_engine)

        @event.listens_for(_engine, "checkin")
        def _on_checkin(dbapi_connection, connection_record) -> None:
            _pool_snapshot(_engine)

        if settings.db_statement_timeout_ms > 0 or settings.db_idle_in_transaction_session_timeout_ms > 0:
            logger.info(
                "db_connection_guardrails_enabled statement_timeout_ms=%s idle_in_transaction_timeout_ms=%s",
                settings.db_statement_timeout_ms,
                settings.db_idle_in_transaction_session_timeout_ms,
            )

    return _engine


def collect_db_runtime_snapshot() -> None:
    engine = get_engine()
    if make_url(settings.database_url).drivername.startswith("sqlite"):
        return
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    select
                        count(*) as total,
                        count(*) filter (where state = 'active') as active,
                        count(*) filter (where state = 'idle') as idle,
                        count(*) filter (where state = 'idle in transaction') as idle_in_transaction,
                        coalesce(max(extract(epoch from (now() - xact_start))), 0) as oldest_xact_age_seconds
                    from pg_stat_activity
                    where datname = current_database()
                    """
                )
            ).one()
        set_db_runtime_sessions(
            active=int(row.active or 0),
            idle=int(row.idle or 0),
            idle_in_transaction=int(row.idle_in_transaction or 0),
            total=int(row.total or 0),
        )
        set_db_oldest_transaction_age(duration_seconds=float(row.oldest_xact_age_seconds or 0.0))
    except Exception:
        return


SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, class_=ObservedSession)


def _shared_request_db_enabled(request: Request | None) -> bool:
    if request is None or not settings.request_shared_db_session_enabled:
        return False
    prefixes = tuple(settings.request_shared_db_session_path_prefixes or ())
    path = request.url.path
    if not prefixes:
        return True
    return any(path.startswith(prefix) for prefix in prefixes)


def end_read_only_transaction(db: Session | None) -> None:
    if db is None:
        return
    try:
        if db.in_transaction():
            db.rollback()
    except SQLAlchemyError:
        return
    except Exception:
        return


def get_request_db_session(request: Request | None = None):
    """Centralized database session dependency for FastAPI.

    Yields a database session and ensures it is closed after the request.
    Use this as a dependency in FastAPI route handlers.

    Example:
        @app.get("/items")
        def get_items(db: Session = Depends(get_db)):
            return db.query(Item).all()
    """
    if request is not None and _shared_request_db_enabled(request):
        shared_db = getattr(request.state, "middleware_db", None)
        if shared_db is not None:
            yield shared_db
            return

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db(request: Request):
    yield from get_request_db_session(request)
