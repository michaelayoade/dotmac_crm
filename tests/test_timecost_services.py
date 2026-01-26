"""Tests for timecost service."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.timecost import BillingRate, CostRate, ExpenseLine, WorkLog
from app.schemas.timecost import (
    BillingRateCreate,
    BillingRateUpdate,
    CostRateCreate,
    CostRateUpdate,
    ExpenseLineCreate,
    ExpenseLineUpdate,
    WorkLogCreate,
    WorkLogUpdate,
)
from app.services import timecost as timecost_service
from app.services.common import apply_ordering, apply_pagination, validate_enum


# ============================================================================
# Helper Function Tests
# ============================================================================


class TestApplyOrdering:
    """Tests for _apply_ordering helper."""

    def test_orders_ascending(self, db_session):
        """Test orders ascending."""
        query = db_session.query(WorkLog)
        allowed = {"created_at": WorkLog.created_at}
        result = apply_ordering(query, "created_at", "asc", allowed)
        assert result is not None

    def test_orders_descending(self, db_session):
        """Test orders descending."""
        query = db_session.query(WorkLog)
        allowed = {"created_at": WorkLog.created_at}
        result = apply_ordering(query, "created_at", "desc", allowed)
        assert result is not None

    def test_raises_for_invalid_column(self, db_session):
        """Test raises HTTPException for invalid column."""
        query = db_session.query(WorkLog)
        allowed = {"created_at": WorkLog.created_at}
        with pytest.raises(HTTPException) as exc_info:
            apply_ordering(query, "invalid", "asc", allowed)
        assert exc_info.value.status_code == 400
        assert "Invalid order_by" in exc_info.value.detail


class TestApplyPagination:
    """Tests for _apply_pagination helper."""

    def test_applies_limit_and_offset(self, db_session):
        """Test applies limit and offset."""
        query = db_session.query(WorkLog)
        result = apply_pagination(query, limit=10, offset=5)
        assert result is not None


class TestEnsurePerson:
    """Tests for _ensure_person helper."""

    def test_passes_for_valid_person(self, db_session, person):
        """Test passes silently for valid person."""
        timecost_service._ensure_person(db_session, str(person.id))
        # No exception raised

    def test_raises_for_invalid_person(self, db_session):
        """Test raises HTTPException for invalid person."""
        with pytest.raises(HTTPException) as exc_info:
            timecost_service._ensure_person(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404
        assert "Person not found" in exc_info.value.detail


class TestEnsureWorkOrder:
    """Tests for _ensure_work_order helper."""

    def test_passes_for_valid_work_order(self, db_session, work_order):
        """Test passes silently for valid work order."""
        timecost_service._ensure_work_order(db_session, str(work_order.id))
        # No exception raised

    def test_raises_for_invalid_work_order(self, db_session):
        """Test raises HTTPException for invalid work order."""
        with pytest.raises(HTTPException) as exc_info:
            timecost_service._ensure_work_order(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404
        assert "Work order not found" in exc_info.value.detail


class TestEnsureProject:
    """Tests for _ensure_project helper."""

    def test_passes_for_valid_project(self, db_session, project):
        """Test passes silently for valid project."""
        timecost_service._ensure_project(db_session, str(project.id))
        # No exception raised

    def test_raises_for_invalid_project(self, db_session):
        """Test raises HTTPException for invalid project."""
        with pytest.raises(HTTPException) as exc_info:
            timecost_service._ensure_project(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404
        assert "Project not found" in exc_info.value.detail


class TestComputeMinutes:
    """Tests for _compute_minutes helper."""

    def test_computes_minutes_from_times(self):
        """Test computes minutes from start and end times."""
        start = datetime.now(timezone.utc)
        end = start + timedelta(hours=2, minutes=30)
        result = timecost_service._compute_minutes(start, end)
        assert result == 150

    def test_returns_zero_for_none_start(self):
        """Test returns 0 when start is None."""
        end = datetime.now(timezone.utc)
        result = timecost_service._compute_minutes(None, end)
        assert result == 0

    def test_returns_zero_for_none_end(self):
        """Test returns 0 when end is None."""
        start = datetime.now(timezone.utc)
        result = timecost_service._compute_minutes(start, None)
        assert result == 0

    def test_returns_zero_for_negative_delta(self):
        """Test returns 0 when end is before start."""
        end = datetime.now(timezone.utc)
        start = end + timedelta(hours=1)
        result = timecost_service._compute_minutes(start, end)
        assert result == 0


# ============================================================================
# WorkLogs Tests
# ============================================================================


class TestWorkLogsCreate:
    """Tests for WorkLogs.create."""

    def test_creates_work_log(self, db_session, work_order, person):
        """Test creates work log with required fields."""
        start = datetime.now(timezone.utc)
        payload = WorkLogCreate(
            work_order_id=work_order.id,
            person_id=person.id,
            start_at=start,
        )
        result = timecost_service.work_logs.create(db_session, payload)
        assert result.id is not None
        assert result.work_order_id == work_order.id
        assert result.person_id == person.id

    def test_computes_minutes_when_end_provided(self, db_session, work_order, person):
        """Test auto-computes minutes when end_at is provided."""
        start = datetime.now(timezone.utc)
        end = start + timedelta(hours=2)
        payload = WorkLogCreate(
            work_order_id=work_order.id,
            person_id=person.id,
            start_at=start,
            end_at=end,
            minutes=0,
        )
        result = timecost_service.work_logs.create(db_session, payload)
        assert result.minutes == 120

    def test_uses_provided_minutes(self, db_session, work_order, person):
        """Test uses provided minutes instead of computing."""
        start = datetime.now(timezone.utc)
        end = start + timedelta(hours=2)
        payload = WorkLogCreate(
            work_order_id=work_order.id,
            person_id=person.id,
            start_at=start,
            end_at=end,
            minutes=60,
        )
        result = timecost_service.work_logs.create(db_session, payload)
        assert result.minutes == 60

    def test_raises_for_invalid_work_order(self, db_session, person):
        """Test raises HTTPException for invalid work_order_id."""
        payload = WorkLogCreate(
            work_order_id=uuid.uuid4(),
            person_id=person.id,
            start_at=datetime.now(timezone.utc),
        )
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.work_logs.create(db_session, payload)
        assert exc_info.value.status_code == 404
        assert "Work order not found" in exc_info.value.detail

    def test_raises_for_invalid_person(self, db_session, work_order):
        """Test raises HTTPException for invalid person_id."""
        payload = WorkLogCreate(
            work_order_id=work_order.id,
            person_id=uuid.uuid4(),
            start_at=datetime.now(timezone.utc),
        )
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.work_logs.create(db_session, payload)
        assert exc_info.value.status_code == 404
        assert "Person not found" in exc_info.value.detail


class TestWorkLogsGet:
    """Tests for WorkLogs.get."""

    def test_gets_work_log(self, db_session, work_order, person):
        """Test gets work log by id."""
        log = WorkLog(
            work_order_id=work_order.id,
            person_id=person.id,
            start_at=datetime.now(timezone.utc),
        )
        db_session.add(log)
        db_session.commit()

        result = timecost_service.work_logs.get(db_session, str(log.id))
        assert result.id == log.id

    def test_raises_for_not_found(self, db_session):
        """Test raises HTTPException for not found."""
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.work_logs.get(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404
        assert "Work log not found" in exc_info.value.detail


class TestWorkLogsList:
    """Tests for WorkLogs.list."""

    def test_lists_active_by_default(self, db_session, work_order, person):
        """Test lists only active work logs by default."""
        active = WorkLog(
            work_order_id=work_order.id,
            person_id=person.id,
            start_at=datetime.now(timezone.utc),
            is_active=True,
        )
        inactive = WorkLog(
            work_order_id=work_order.id,
            person_id=person.id,
            start_at=datetime.now(timezone.utc),
            is_active=False,
        )
        db_session.add_all([active, inactive])
        db_session.commit()

        result = timecost_service.work_logs.list(
            db=db_session,
            work_order_id=None,
            person_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert all(log.is_active for log in result)

    def test_filters_by_work_order_id(self, db_session, work_order, person):
        """Test filters by work_order_id."""
        log = WorkLog(
            work_order_id=work_order.id,
            person_id=person.id,
            start_at=datetime.now(timezone.utc),
        )
        db_session.add(log)
        db_session.commit()

        result = timecost_service.work_logs.list(
            db=db_session,
            work_order_id=str(work_order.id),
            person_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
        assert all(l.work_order_id == work_order.id for l in result)

    def test_filters_by_person_id(self, db_session, work_order, person):
        """Test filters by person_id."""
        log = WorkLog(
            work_order_id=work_order.id,
            person_id=person.id,
            start_at=datetime.now(timezone.utc),
        )
        db_session.add(log)
        db_session.commit()

        result = timecost_service.work_logs.list(
            db=db_session,
            work_order_id=None,
            person_id=str(person.id),
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
        assert all(l.person_id == person.id for l in result)

    def test_lists_inactive_when_specified(self, db_session, work_order, person):
        """Test lists inactive work logs when specified."""
        inactive = WorkLog(
            work_order_id=work_order.id,
            person_id=person.id,
            start_at=datetime.now(timezone.utc),
            is_active=False,
        )
        db_session.add(inactive)
        db_session.commit()

        result = timecost_service.work_logs.list(
            db=db_session,
            work_order_id=None,
            person_id=None,
            is_active=False,
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
        assert all(not l.is_active for l in result)


class TestWorkLogsUpdate:
    """Tests for WorkLogs.update."""

    def test_updates_work_log(self, db_session, work_order, person):
        """Test updates work log."""
        log = WorkLog(
            work_order_id=work_order.id,
            person_id=person.id,
            start_at=datetime.now(timezone.utc),
        )
        db_session.add(log)
        db_session.commit()

        payload = WorkLogUpdate(notes="Updated notes")
        result = timecost_service.work_logs.update(db_session, str(log.id), payload)
        assert result.notes == "Updated notes"

    def test_recomputes_minutes_on_time_change(self, db_session, work_order, person):
        """Test recomputes minutes when times change."""
        start = datetime.now(timezone.utc)
        log = WorkLog(
            work_order_id=work_order.id,
            person_id=person.id,
            start_at=start,
            minutes=0,
        )
        db_session.add(log)
        db_session.commit()

        new_start = start  # Ensure we pass the same timezone-aware datetime
        new_end = start + timedelta(hours=3)
        payload = WorkLogUpdate(start_at=new_start, end_at=new_end)
        result = timecost_service.work_logs.update(db_session, str(log.id), payload)
        assert result.minutes == 180

    def test_raises_for_not_found(self, db_session):
        """Test raises HTTPException for not found."""
        payload = WorkLogUpdate(notes="Test")
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.work_logs.update(db_session, str(uuid.uuid4()), payload)
        assert exc_info.value.status_code == 404


class TestWorkLogsDelete:
    """Tests for WorkLogs.delete (soft delete)."""

    def test_soft_deletes_work_log(self, db_session, work_order, person):
        """Test soft deletes work log."""
        log = WorkLog(
            work_order_id=work_order.id,
            person_id=person.id,
            start_at=datetime.now(timezone.utc),
            is_active=True,
        )
        db_session.add(log)
        db_session.commit()
        log_id = log.id

        timecost_service.work_logs.delete(db_session, str(log_id))

        log = db_session.get(WorkLog, log_id)
        assert log is not None
        assert log.is_active is False

    def test_raises_for_not_found(self, db_session):
        """Test raises HTTPException for not found."""
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.work_logs.delete(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404


# ============================================================================
# ExpenseLines Tests
# ============================================================================


class TestExpenseLinesCreate:
    """Tests for ExpenseLines.create."""

    def test_creates_expense_line_for_work_order(self, db_session, work_order, monkeypatch):
        """Test creates expense line for work order."""
        # Mock settings_spec to avoid NameError
        monkeypatch.setattr(
            "app.services.settings_spec.resolve_value",
            lambda db, domain, key: None,
            raising=False,
        )
        payload = ExpenseLineCreate(
            work_order_id=work_order.id,
            amount=Decimal("100.00"),
        )
        result = timecost_service.expense_lines.create(db_session, payload)
        assert result.id is not None
        assert result.work_order_id == work_order.id
        assert result.amount == Decimal("100.00")

    def test_creates_expense_line_for_project(self, db_session, project, monkeypatch):
        """Test creates expense line for project."""
        monkeypatch.setattr(
            "app.services.settings_spec.resolve_value",
            lambda db, domain, key: None,
            raising=False,
        )
        payload = ExpenseLineCreate(
            project_id=project.id,
            amount=Decimal("50.00"),
        )
        result = timecost_service.expense_lines.create(db_session, payload)
        assert result.project_id == project.id

    def test_raises_for_invalid_work_order(self, db_session, monkeypatch):
        """Test raises HTTPException for invalid work_order_id."""
        monkeypatch.setattr(
            "app.services.settings_spec.resolve_value",
            lambda db, domain, key: None,
            raising=False,
        )
        payload = ExpenseLineCreate(
            work_order_id=uuid.uuid4(),
            amount=Decimal("100.00"),
        )
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.expense_lines.create(db_session, payload)
        assert exc_info.value.status_code == 404
        assert "Work order not found" in exc_info.value.detail

    def test_raises_for_invalid_project(self, db_session, monkeypatch):
        """Test raises HTTPException for invalid project_id."""
        monkeypatch.setattr(
            "app.services.settings_spec.resolve_value",
            lambda db, domain, key: None,
            raising=False,
        )
        payload = ExpenseLineCreate(
            project_id=uuid.uuid4(),
            amount=Decimal("100.00"),
        )
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.expense_lines.create(db_session, payload)
        assert exc_info.value.status_code == 404
        assert "Project not found" in exc_info.value.detail

    def test_uses_default_currency_from_settings(self, db_session, work_order, monkeypatch):
        """Test uses default_currency from settings."""
        monkeypatch.setattr(
            "app.services.settings_spec.resolve_value",
            lambda db, domain, key: "EUR" if key == "default_currency" else None,
            raising=False,
        )
        payload = ExpenseLineCreate(
            work_order_id=work_order.id,
            amount=Decimal("100.00"),
        )
        result = timecost_service.expense_lines.create(db_session, payload)
        assert result.currency == "EUR"


class TestExpenseLinesGet:
    """Tests for ExpenseLines.get."""

    def test_gets_expense_line(self, db_session, work_order):
        """Test gets expense line by id."""
        line = ExpenseLine(
            work_order_id=work_order.id,
            amount=Decimal("100.00"),
        )
        db_session.add(line)
        db_session.commit()

        result = timecost_service.expense_lines.get(db_session, str(line.id))
        assert result.id == line.id

    def test_raises_for_not_found(self, db_session):
        """Test raises HTTPException for not found."""
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.expense_lines.get(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404
        assert "Expense line not found" in exc_info.value.detail


class TestExpenseLinesList:
    """Tests for ExpenseLines.list."""

    def test_lists_active_by_default(self, db_session, work_order):
        """Test lists only active expense lines by default."""
        active = ExpenseLine(
            work_order_id=work_order.id,
            amount=Decimal("100.00"),
            is_active=True,
        )
        inactive = ExpenseLine(
            work_order_id=work_order.id,
            amount=Decimal("50.00"),
            is_active=False,
        )
        db_session.add_all([active, inactive])
        db_session.commit()

        result = timecost_service.expense_lines.list(
            db=db_session,
            work_order_id=None,
            project_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert all(line.is_active for line in result)

    def test_filters_by_work_order_id(self, db_session, work_order):
        """Test filters by work_order_id."""
        line = ExpenseLine(
            work_order_id=work_order.id,
            amount=Decimal("100.00"),
        )
        db_session.add(line)
        db_session.commit()

        result = timecost_service.expense_lines.list(
            db=db_session,
            work_order_id=str(work_order.id),
            project_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
        assert all(l.work_order_id == work_order.id for l in result)

    def test_filters_by_project_id(self, db_session, project):
        """Test filters by project_id."""
        line = ExpenseLine(
            project_id=project.id,
            amount=Decimal("100.00"),
        )
        db_session.add(line)
        db_session.commit()

        result = timecost_service.expense_lines.list(
            db=db_session,
            work_order_id=None,
            project_id=str(project.id),
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
        assert all(l.project_id == project.id for l in result)

    def test_lists_inactive_when_specified(self, db_session, work_order):
        """Test lists inactive expense lines when specified."""
        inactive = ExpenseLine(
            work_order_id=work_order.id,
            amount=Decimal("100.00"),
            is_active=False,
        )
        db_session.add(inactive)
        db_session.commit()

        result = timecost_service.expense_lines.list(
            db=db_session,
            work_order_id=None,
            project_id=None,
            is_active=False,
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
        assert all(not l.is_active for l in result)


class TestExpenseLinesUpdate:
    """Tests for ExpenseLines.update."""

    def test_updates_expense_line(self, db_session, work_order):
        """Test updates expense line."""
        line = ExpenseLine(
            work_order_id=work_order.id,
            amount=Decimal("100.00"),
        )
        db_session.add(line)
        db_session.commit()

        payload = ExpenseLineUpdate(amount=Decimal("200.00"))
        result = timecost_service.expense_lines.update(db_session, str(line.id), payload)
        assert result.amount == Decimal("200.00")

    def test_validates_work_order_on_update(self, db_session, work_order):
        """Test validates work_order_id on update."""
        line = ExpenseLine(
            work_order_id=work_order.id,
            amount=Decimal("100.00"),
        )
        db_session.add(line)
        db_session.commit()

        payload = ExpenseLineUpdate(work_order_id=uuid.uuid4())
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.expense_lines.update(db_session, str(line.id), payload)
        assert exc_info.value.status_code == 404
        assert "Work order not found" in exc_info.value.detail

    def test_validates_project_on_update(self, db_session, work_order):
        """Test validates project_id on update."""
        line = ExpenseLine(
            work_order_id=work_order.id,
            amount=Decimal("100.00"),
        )
        db_session.add(line)
        db_session.commit()

        payload = ExpenseLineUpdate(project_id=uuid.uuid4())
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.expense_lines.update(db_session, str(line.id), payload)
        assert exc_info.value.status_code == 404
        assert "Project not found" in exc_info.value.detail

    def test_raises_for_not_found(self, db_session):
        """Test raises HTTPException for not found."""
        payload = ExpenseLineUpdate(amount=Decimal("100.00"))
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.expense_lines.update(db_session, str(uuid.uuid4()), payload)
        assert exc_info.value.status_code == 404


class TestExpenseLinesDelete:
    """Tests for ExpenseLines.delete (soft delete)."""

    def test_soft_deletes_expense_line(self, db_session, work_order):
        """Test soft deletes expense line."""
        line = ExpenseLine(
            work_order_id=work_order.id,
            amount=Decimal("100.00"),
            is_active=True,
        )
        db_session.add(line)
        db_session.commit()
        line_id = line.id

        timecost_service.expense_lines.delete(db_session, str(line_id))

        line = db_session.get(ExpenseLine, line_id)
        assert line is not None
        assert line.is_active is False

    def test_raises_for_not_found(self, db_session):
        """Test raises HTTPException for not found."""
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.expense_lines.delete(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404


# ============================================================================
# CostRates Tests
# ============================================================================


class TestCostRatesCreate:
    """Tests for CostRates.create."""

    def test_creates_cost_rate(self, db_session):
        """Test creates cost rate without person."""
        payload = CostRateCreate(
            hourly_rate=Decimal("75.00"),
        )
        result = timecost_service.cost_rates.create(db_session, payload)
        assert result.id is not None
        assert result.hourly_rate == Decimal("75.00")

    def test_creates_cost_rate_for_person(self, db_session, person):
        """Test creates cost rate for person."""
        payload = CostRateCreate(
            person_id=person.id,
            hourly_rate=Decimal("100.00"),
        )
        result = timecost_service.cost_rates.create(db_session, payload)
        assert result.person_id == person.id

    def test_creates_with_effective_dates(self, db_session, person):
        """Test creates cost rate with effective dates."""
        now = datetime.now(timezone.utc)
        payload = CostRateCreate(
            person_id=person.id,
            hourly_rate=Decimal("100.00"),
            effective_from=now,
            effective_to=now + timedelta(days=365),
        )
        result = timecost_service.cost_rates.create(db_session, payload)
        assert result.effective_from is not None
        assert result.effective_to is not None

    def test_raises_for_invalid_person(self, db_session):
        """Test raises HTTPException for invalid person_id."""
        payload = CostRateCreate(
            person_id=uuid.uuid4(),
            hourly_rate=Decimal("100.00"),
        )
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.cost_rates.create(db_session, payload)
        assert exc_info.value.status_code == 404
        assert "Person not found" in exc_info.value.detail


class TestCostRatesGet:
    """Tests for CostRates.get."""

    def test_gets_cost_rate(self, db_session):
        """Test gets cost rate by id."""
        rate = CostRate(hourly_rate=Decimal("50.00"))
        db_session.add(rate)
        db_session.commit()

        result = timecost_service.cost_rates.get(db_session, str(rate.id))
        assert result.id == rate.id

    def test_raises_for_not_found(self, db_session):
        """Test raises HTTPException for not found."""
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.cost_rates.get(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404
        assert "Cost rate not found" in exc_info.value.detail


class TestCostRatesList:
    """Tests for CostRates.list."""

    def test_lists_active_by_default(self, db_session):
        """Test lists only active cost rates by default."""
        active = CostRate(hourly_rate=Decimal("50.00"), is_active=True)
        inactive = CostRate(hourly_rate=Decimal("75.00"), is_active=False)
        db_session.add_all([active, inactive])
        db_session.commit()

        result = timecost_service.cost_rates.list(
            db=db_session,
            person_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert all(rate.is_active for rate in result)

    def test_filters_by_person_id(self, db_session, person):
        """Test filters by person_id."""
        rate = CostRate(person_id=person.id, hourly_rate=Decimal("100.00"))
        db_session.add(rate)
        db_session.commit()

        result = timecost_service.cost_rates.list(
            db=db_session,
            person_id=str(person.id),
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
        assert all(r.person_id == person.id for r in result)

    def test_lists_inactive_when_specified(self, db_session):
        """Test lists inactive cost rates when specified."""
        inactive = CostRate(hourly_rate=Decimal("50.00"), is_active=False)
        db_session.add(inactive)
        db_session.commit()

        result = timecost_service.cost_rates.list(
            db=db_session,
            person_id=None,
            is_active=False,
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
        assert all(not r.is_active for r in result)


class TestCostRatesUpdate:
    """Tests for CostRates.update."""

    def test_updates_cost_rate(self, db_session):
        """Test updates cost rate."""
        rate = CostRate(hourly_rate=Decimal("50.00"))
        db_session.add(rate)
        db_session.commit()

        payload = CostRateUpdate(hourly_rate=Decimal("75.00"))
        result = timecost_service.cost_rates.update(db_session, str(rate.id), payload)
        assert result.hourly_rate == Decimal("75.00")

    def test_validates_person_on_update(self, db_session):
        """Test validates person_id on update."""
        rate = CostRate(hourly_rate=Decimal("50.00"))
        db_session.add(rate)
        db_session.commit()

        payload = CostRateUpdate(person_id=uuid.uuid4())
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.cost_rates.update(db_session, str(rate.id), payload)
        assert exc_info.value.status_code == 404
        assert "Person not found" in exc_info.value.detail

    def test_raises_for_not_found(self, db_session):
        """Test raises HTTPException for not found."""
        payload = CostRateUpdate(hourly_rate=Decimal("50.00"))
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.cost_rates.update(db_session, str(uuid.uuid4()), payload)
        assert exc_info.value.status_code == 404


class TestCostRatesDelete:
    """Tests for CostRates.delete (soft delete)."""

    def test_soft_deletes_cost_rate(self, db_session):
        """Test soft deletes cost rate."""
        rate = CostRate(hourly_rate=Decimal("50.00"), is_active=True)
        db_session.add(rate)
        db_session.commit()
        rate_id = rate.id

        timecost_service.cost_rates.delete(db_session, str(rate_id))

        rate = db_session.get(CostRate, rate_id)
        assert rate is not None
        assert rate.is_active is False

    def test_raises_for_not_found(self, db_session):
        """Test raises HTTPException for not found."""
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.cost_rates.delete(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404


# ============================================================================
# BillingRates Tests
# ============================================================================


class TestBillingRatesCreate:
    """Tests for BillingRates.create."""

    def test_creates_billing_rate(self, db_session, monkeypatch):
        """Test creates billing rate."""
        monkeypatch.setattr(
            "app.services.settings_spec.resolve_value",
            lambda db, domain, key: None,
            raising=False,
        )
        payload = BillingRateCreate(
            name="Standard Rate",
            hourly_rate=Decimal("150.00"),
        )
        result = timecost_service.billing_rates.create(db_session, payload)
        assert result.id is not None
        assert result.name == "Standard Rate"
        assert result.hourly_rate == Decimal("150.00")

    def test_uses_default_currency_from_settings(self, db_session, monkeypatch):
        """Test uses default_currency from settings."""
        monkeypatch.setattr(
            "app.services.settings_spec.resolve_value",
            lambda db, domain, key: "GBP" if key == "default_currency" else None,
            raising=False,
        )
        payload = BillingRateCreate(
            name="UK Rate",
            hourly_rate=Decimal("100.00"),
        )
        result = timecost_service.billing_rates.create(db_session, payload)
        assert result.currency == "GBP"


class TestBillingRatesGet:
    """Tests for BillingRates.get."""

    def test_gets_billing_rate(self, db_session):
        """Test gets billing rate by id."""
        rate = BillingRate(name="Test Rate", hourly_rate=Decimal("100.00"))
        db_session.add(rate)
        db_session.commit()

        result = timecost_service.billing_rates.get(db_session, str(rate.id))
        assert result.id == rate.id

    def test_raises_for_not_found(self, db_session):
        """Test raises HTTPException for not found."""
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.billing_rates.get(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404
        assert "Billing rate not found" in exc_info.value.detail


class TestBillingRatesList:
    """Tests for BillingRates.list."""

    def test_lists_active_by_default(self, db_session):
        """Test lists only active billing rates by default."""
        active = BillingRate(name="Active", hourly_rate=Decimal("100.00"), is_active=True)
        inactive = BillingRate(name="Inactive", hourly_rate=Decimal("50.00"), is_active=False)
        db_session.add_all([active, inactive])
        db_session.commit()

        result = timecost_service.billing_rates.list(
            db=db_session,
            is_active=None,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert all(rate.is_active for rate in result)

    def test_lists_inactive_when_specified(self, db_session):
        """Test lists inactive billing rates when specified."""
        inactive = BillingRate(name="Inactive", hourly_rate=Decimal("50.00"), is_active=False)
        db_session.add(inactive)
        db_session.commit()

        result = timecost_service.billing_rates.list(
            db=db_session,
            is_active=False,
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
        assert all(not r.is_active for r in result)


class TestBillingRatesUpdate:
    """Tests for BillingRates.update."""

    def test_updates_billing_rate(self, db_session):
        """Test updates billing rate."""
        rate = BillingRate(name="Original", hourly_rate=Decimal("100.00"))
        db_session.add(rate)
        db_session.commit()

        payload = BillingRateUpdate(name="Updated", hourly_rate=Decimal("150.00"))
        result = timecost_service.billing_rates.update(db_session, str(rate.id), payload)
        assert result.name == "Updated"
        assert result.hourly_rate == Decimal("150.00")

    def test_raises_for_not_found(self, db_session):
        """Test raises HTTPException for not found."""
        payload = BillingRateUpdate(name="Test")
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.billing_rates.update(db_session, str(uuid.uuid4()), payload)
        assert exc_info.value.status_code == 404


class TestBillingRatesDelete:
    """Tests for BillingRates.delete (soft delete)."""

    def test_soft_deletes_billing_rate(self, db_session):
        """Test soft deletes billing rate."""
        rate = BillingRate(name="Test", hourly_rate=Decimal("100.00"), is_active=True)
        db_session.add(rate)
        db_session.commit()
        rate_id = rate.id

        timecost_service.billing_rates.delete(db_session, str(rate_id))

        rate = db_session.get(BillingRate, rate_id)
        assert rate is not None
        assert rate.is_active is False

    def test_raises_for_not_found(self, db_session):
        """Test raises HTTPException for not found."""
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.billing_rates.delete(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404


# ============================================================================
# Summary Functions Tests
# ============================================================================


class TestResolveRate:
    """Tests for _resolve_rate helper."""

    def test_returns_rate_for_person(self, db_session, person):
        """Test returns rate for person."""
        now = datetime.now(timezone.utc)
        rate = CostRate(
            person_id=person.id,
            hourly_rate=Decimal("60.00"),
            effective_from=now - timedelta(days=1),
            is_active=True,
        )
        db_session.add(rate)
        db_session.commit()

        result = timecost_service._resolve_rate(db_session, str(person.id), now)
        assert result == Decimal("60.00")

    def test_returns_zero_when_no_rate(self, db_session, person):
        """Test returns 0 when no rate found."""
        now = datetime.now(timezone.utc)
        result = timecost_service._resolve_rate(db_session, str(person.id), now)
        assert result == Decimal("0.00")

    def test_respects_effective_dates(self, db_session, person):
        """Test respects effective dates."""
        now = datetime.now(timezone.utc)
        # Create rate that's expired
        expired = CostRate(
            person_id=person.id,
            hourly_rate=Decimal("50.00"),
            effective_from=now - timedelta(days=30),
            effective_to=now - timedelta(days=1),
            is_active=True,
        )
        # Create current rate
        current = CostRate(
            person_id=person.id,
            hourly_rate=Decimal("75.00"),
            effective_from=now - timedelta(days=1),
            is_active=True,
        )
        db_session.add_all([expired, current])
        db_session.commit()

        result = timecost_service._resolve_rate(db_session, str(person.id), now)
        assert result == Decimal("75.00")


class TestWorkOrderCostSummary:
    """Tests for work_order_cost_summary function."""

    def test_calculates_labor_cost(self, db_session, work_order, person):
        """Test calculates labor cost from work logs."""
        rate = CostRate(
            person_id=person.id,
            hourly_rate=Decimal("60.00"),
            is_active=True,
        )
        db_session.add(rate)
        db_session.commit()

        start = datetime.now(timezone.utc)
        log = WorkLog(
            work_order_id=work_order.id,
            person_id=person.id,
            start_at=start,
            end_at=start + timedelta(hours=2),
            minutes=120,
        )
        db_session.add(log)
        db_session.commit()

        summary = timecost_service.work_order_cost_summary(db_session, str(work_order.id))
        assert summary["labor_cost"] == Decimal("120.00")

    def test_uses_log_hourly_rate_if_set(self, db_session, work_order, person):
        """Test uses work log's hourly_rate if set."""
        start = datetime.now(timezone.utc)
        log = WorkLog(
            work_order_id=work_order.id,
            person_id=person.id,
            start_at=start,
            minutes=60,
            hourly_rate=Decimal("100.00"),
        )
        db_session.add(log)
        db_session.commit()

        summary = timecost_service.work_order_cost_summary(db_session, str(work_order.id))
        assert summary["labor_cost"] == Decimal("100.00")

    def test_calculates_expense_total(self, db_session, work_order):
        """Test calculates expense total."""
        expense1 = ExpenseLine(
            work_order_id=work_order.id,
            amount=Decimal("50.00"),
            is_active=True,
        )
        expense2 = ExpenseLine(
            work_order_id=work_order.id,
            amount=Decimal("75.00"),
            is_active=True,
        )
        db_session.add_all([expense1, expense2])
        db_session.commit()

        summary = timecost_service.work_order_cost_summary(db_session, str(work_order.id))
        assert summary["expense_total"] == Decimal("125.00")

    def test_ignores_inactive_logs_and_expenses(self, db_session, work_order, person):
        """Test ignores inactive work logs and expenses."""
        start = datetime.now(timezone.utc)
        active_log = WorkLog(
            work_order_id=work_order.id,
            person_id=person.id,
            start_at=start,
            minutes=60,
            hourly_rate=Decimal("50.00"),
            is_active=True,
        )
        inactive_log = WorkLog(
            work_order_id=work_order.id,
            person_id=person.id,
            start_at=start,
            minutes=60,
            hourly_rate=Decimal("50.00"),
            is_active=False,
        )
        active_expense = ExpenseLine(
            work_order_id=work_order.id,
            amount=Decimal("100.00"),
            is_active=True,
        )
        inactive_expense = ExpenseLine(
            work_order_id=work_order.id,
            amount=Decimal("100.00"),
            is_active=False,
        )
        db_session.add_all([active_log, inactive_log, active_expense, inactive_expense])
        db_session.commit()

        summary = timecost_service.work_order_cost_summary(db_session, str(work_order.id))
        assert summary["labor_cost"] == Decimal("50.00")
        assert summary["expense_total"] == Decimal("100.00")

    def test_raises_for_invalid_work_order(self, db_session):
        """Test raises HTTPException for invalid work_order_id."""
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.work_order_cost_summary(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404


class TestProjectCostSummary:
    """Tests for project_cost_summary function."""

    def test_calculates_labor_from_work_orders(self, db_session, project, work_order, person):
        """Test calculates labor from all project work orders."""
        rate = CostRate(
            person_id=person.id,
            hourly_rate=Decimal("50.00"),
            is_active=True,
        )
        db_session.add(rate)
        db_session.commit()

        start = datetime.now(timezone.utc)
        log = WorkLog(
            work_order_id=work_order.id,
            person_id=person.id,
            start_at=start,
            minutes=60,
        )
        db_session.add(log)
        db_session.commit()

        summary = timecost_service.project_cost_summary(db_session, str(project.id))
        assert summary["labor_cost"] == Decimal("50.00")

    def test_calculates_project_expenses(self, db_session, project):
        """Test calculates expenses linked to project."""
        expense = ExpenseLine(
            project_id=project.id,
            amount=Decimal("200.00"),
            is_active=True,
        )
        db_session.add(expense)
        db_session.commit()

        summary = timecost_service.project_cost_summary(db_session, str(project.id))
        assert summary["expense_total"] == Decimal("200.00")

    def test_calculates_total_cost(self, db_session, project, work_order, person):
        """Test calculates total cost."""
        start = datetime.now(timezone.utc)
        log = WorkLog(
            work_order_id=work_order.id,
            person_id=person.id,
            start_at=start,
            minutes=60,
            hourly_rate=Decimal("100.00"),
        )
        expense = ExpenseLine(
            project_id=project.id,
            amount=Decimal("50.00"),
            is_active=True,
        )
        db_session.add_all([log, expense])
        db_session.commit()

        summary = timecost_service.project_cost_summary(db_session, str(project.id))
        assert summary["labor_cost"] == Decimal("100.00")
        assert summary["expense_total"] == Decimal("50.00")
        assert summary["total_cost"] == Decimal("150.00")

    def test_raises_for_invalid_project(self, db_session):
        """Test raises HTTPException for invalid project_id."""
        with pytest.raises(HTTPException) as exc_info:
            timecost_service.project_cost_summary(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404
