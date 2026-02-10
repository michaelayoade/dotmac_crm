"""Sync service for pulling technician shifts from DotMac ERP."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.dispatch import AvailabilityBlock, Shift, TechnicianProfile
from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.services import settings_spec
from app.services.dotmac_erp.client import DotMacERPClient, DotMacERPError

logger = logging.getLogger(__name__)


@dataclass
class ShiftSyncResult:
    """Result of a shift sync operation."""

    shifts_created: int = 0
    shifts_updated: int = 0
    time_off_created: int = 0
    time_off_updated: int = 0
    technicians_matched: int = 0
    technicians_skipped: int = 0
    matched_technician_ids: set[str] = field(default_factory=set, repr=False)
    skipped_employee_ids: set[str] = field(default_factory=set, repr=False)
    errors: list[dict] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def total_synced(self) -> int:
        return self.shifts_created + self.shifts_updated + self.time_off_created + self.time_off_updated

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


class DotMacERPShiftSync:
    """
    Service for syncing technician shifts and availability from DotMac ERP.

    Pulls:
    - Employee shifts (work schedules)
    - Time-off/leave records (unavailability blocks)
    """

    def __init__(self, db: Session):
        self.db = db
        self._client: DotMacERPClient | None = None
        self._technician_cache: dict[str, TechnicianProfile | None] = {}

    def _get_client(self) -> DotMacERPClient | None:
        """Get configured ERP client, or None if not configured."""
        if self._client is not None:
            return self._client

        # Check if shift sync is enabled
        enabled = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_shift_sync_enabled")
        if not enabled:
            return None

        base_url_value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_base_url")
        token_value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_token")

        base_url = str(base_url_value) if base_url_value else None
        token = str(token_value) if token_value else None

        if not base_url or not token:
            logger.warning("DotMac ERP sync enabled but not configured (missing URL or token)")
            return None

        timeout_value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_timeout_seconds")
        if isinstance(timeout_value, int | str):
            timeout = int(timeout_value)
        else:
            timeout = 30

        self._client = DotMacERPClient(
            base_url=base_url,
            token=token,
            timeout=timeout,
        )
        return self._client

    def close(self):
        """Close the ERP client."""
        if self._client:
            self._client.close()
            self._client = None

    def _get_technician_by_erp_id(self, erp_employee_id: str) -> TechnicianProfile | None:
        """Get technician by ERP employee ID, with caching."""
        if erp_employee_id in self._technician_cache:
            return self._technician_cache[erp_employee_id]

        technician = (
            self.db.query(TechnicianProfile)
            .filter(TechnicianProfile.erp_employee_id == erp_employee_id)
            .filter(TechnicianProfile.is_active.is_(True))
            .first()
        )
        self._technician_cache[erp_employee_id] = technician
        return technician

    def _get_technician_by_email(self, email: str) -> TechnicianProfile | None:
        """Get technician by email (fallback matching)."""
        if email in self._technician_cache:
            return self._technician_cache[email]

        # Join with Person to match by email
        technician = (
            self.db.query(TechnicianProfile)
            .join(Person, TechnicianProfile.person_id == Person.id)
            .filter(Person.email == email.lower())
            .filter(TechnicianProfile.is_active.is_(True))
            .first()
        )
        self._technician_cache[email] = technician
        return technician

    def _resolve_technician(self, erp_data: dict) -> TechnicianProfile | None:
        """
        Resolve ERP employee to local technician profile.

        Tries erp_employee_id first, then falls back to email matching.
        """
        employee_id = erp_data.get("employee_id")
        employee_email = erp_data.get("employee_email", "").lower()

        # Try by ERP ID first
        if employee_id:
            technician = self._get_technician_by_erp_id(employee_id)
            if technician:
                return technician

        # Fall back to email
        if employee_email:
            technician = self._get_technician_by_email(employee_email)
            if technician and employee_id:
                # Link the technician to ERP ID for future syncs
                technician.erp_employee_id = employee_id
                self.db.flush()
            return technician

        return None

    def _parse_datetime(self, value: str | None) -> datetime | None:
        """Parse ISO datetime string from ERP."""
        if not value:
            return None
        try:
            # Handle various ISO formats
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed
        except ValueError:
            logger.warning(f"Failed to parse datetime: {value}")
            return None

    def _date_range_bounds(self, from_date: str, to_date: str) -> tuple[datetime, datetime]:
        start_date = datetime.fromisoformat(from_date).date()
        end_date = datetime.fromisoformat(to_date).date()
        start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=UTC)
        end_dt = (datetime.combine(end_date, datetime.min.time()) + timedelta(days=1)).replace(tzinfo=UTC)
        return start_dt, end_dt

    def _upsert_shift(self, erp_shift: dict, technician: TechnicianProfile) -> str:
        """
        Upsert a shift record from ERP data.

        Returns: "created", "updated", or "skipped"
        """
        erp_id = erp_shift.get("shift_id")
        if not erp_id:
            return "skipped"

        start_at = self._parse_datetime(erp_shift.get("start_at"))
        end_at = self._parse_datetime(erp_shift.get("end_at"))

        if not start_at or not end_at:
            logger.warning(f"Shift {erp_id} missing start_at or end_at")
            return "skipped"

        # Check if shift exists
        existing = self.db.query(Shift).filter(Shift.erp_id == str(erp_id)).first()

        if existing:
            # Update existing shift
            existing.technician_id = technician.id
            existing.start_at = start_at
            existing.end_at = end_at
            existing.shift_type = erp_shift.get("shift_type")
            existing.timezone = erp_shift.get("timezone")
            existing.is_active = True
            return "updated"
        else:
            # Create new shift
            shift = Shift(
                technician_id=technician.id,
                start_at=start_at,
                end_at=end_at,
                shift_type=erp_shift.get("shift_type"),
                timezone=erp_shift.get("timezone"),
                erp_id=str(erp_id),
                is_active=True,
            )
            self.db.add(shift)
            return "created"

    def _upsert_time_off(self, erp_time_off: dict, technician: TechnicianProfile) -> str:
        """
        Upsert an availability block from ERP time-off data.

        Returns: "created", "updated", or "skipped"
        """
        erp_id = erp_time_off.get("time_off_id")
        if not erp_id:
            return "skipped"

        # Only sync approved time-off
        status = erp_time_off.get("status", "").lower()
        if status not in ("approved", "active"):
            return "skipped"

        start_at = self._parse_datetime(erp_time_off.get("start_at"))
        end_at = self._parse_datetime(erp_time_off.get("end_at"))

        if not start_at or not end_at:
            logger.warning(f"Time-off {erp_id} missing start_at or end_at")
            return "skipped"

        # Check if availability block exists
        existing = self.db.query(AvailabilityBlock).filter(AvailabilityBlock.erp_id == str(erp_id)).first()

        reason = erp_time_off.get("reason") or erp_time_off.get("leave_type")

        if existing:
            # Update existing block
            existing.technician_id = technician.id
            existing.start_at = start_at
            existing.end_at = end_at
            existing.reason = reason
            existing.block_type = erp_time_off.get("leave_type")
            existing.is_available = False  # Time-off means unavailable
            existing.is_active = True
            return "updated"
        else:
            # Create new block
            block = AvailabilityBlock(
                technician_id=technician.id,
                start_at=start_at,
                end_at=end_at,
                reason=reason,
                block_type=erp_time_off.get("leave_type"),
                is_available=False,  # Time-off means unavailable
                erp_id=str(erp_id),
                is_active=True,
            )
            self.db.add(block)
            return "created"

    def sync_shifts(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        days_ahead: int = 14,
    ) -> ShiftSyncResult:
        """
        Sync technician shifts from ERP.

        Args:
            from_date: Start date (ISO format), defaults to today
            to_date: End date (ISO format), defaults to days_ahead from today
            days_ahead: If to_date not specified, sync this many days ahead

        Returns:
            ShiftSyncResult with counts and any errors
        """
        start_time = datetime.now(UTC)
        result = ShiftSyncResult()

        client = self._get_client()
        if not client:
            result.errors.append({"type": "config", "error": "ERP sync not configured or disabled"})
            return result

        # Default date range
        today = datetime.now(UTC).date()
        if not from_date:
            from_date = today.isoformat()
        if not to_date:
            to_date = (today + timedelta(days=days_ahead)).isoformat()

        try:
            matched_techs = result.matched_technician_ids
            skipped_employees = result.skipped_employee_ids
            seen_shift_ids: set[str] = set()
            limit = 1000
            offset = 0

            while True:
                shifts = client.get_employee_shifts(
                    from_date=from_date,
                    to_date=to_date,
                    limit=limit,
                    offset=offset,
                )

                if not shifts:
                    break

                logger.info(
                    "Fetched %d shifts from ERP (%s to %s, offset=%d)",
                    len(shifts),
                    from_date,
                    to_date,
                    offset,
                )

                for shift_data in shifts:
                    shift_id = shift_data.get("shift_id")
                    if shift_id:
                        seen_shift_ids.add(str(shift_id))
                    technician = self._resolve_technician(shift_data)
                    if not technician:
                        emp_id = shift_data.get("employee_id") or shift_data.get("employee_email")
                        if emp_id:
                            skipped_employees.add(str(emp_id))
                        continue

                    matched_techs.add(str(technician.id))

                    action = self._upsert_shift(shift_data, technician)
                    if action == "created":
                        result.shifts_created += 1
                    elif action == "updated":
                        result.shifts_updated += 1

                if len(shifts) < limit:
                    break
                offset += limit

            # Deactivate shifts no longer present in ERP for the date range.
            start_dt, end_dt = self._date_range_bounds(from_date, to_date)
            cleanup_query = (
                self.db.query(Shift)
                .filter(Shift.erp_id.isnot(None))
                .filter(Shift.is_active.is_(True))
                .filter(Shift.start_at >= start_dt)
                .filter(Shift.start_at < end_dt)
            )
            if seen_shift_ids:
                cleanup_query = cleanup_query.filter(~Shift.erp_id.in_(seen_shift_ids))
            cleanup_query.update({"is_active": False}, synchronize_session=False)

            result.technicians_matched = len(matched_techs)
            result.technicians_skipped = len(skipped_employees)

            if skipped_employees:
                logger.info(
                    f"Skipped {len(skipped_employees)} employees not found as technicians: "
                    f"{list(skipped_employees)[:5]}..."
                )

            self.db.commit()

        except DotMacERPError as e:
            logger.error(f"Shift sync failed: {e}")
            result.errors.append({"type": "api", "error": str(e)})
            self.db.rollback()

        result.duration_seconds = (datetime.now(UTC) - start_time).total_seconds()
        return result

    def sync_time_off(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        days_ahead: int = 30,
    ) -> ShiftSyncResult:
        """
        Sync technician time-off/leave from ERP.

        Args:
            from_date: Start date (ISO format), defaults to today
            to_date: End date (ISO format), defaults to days_ahead from today
            days_ahead: If to_date not specified, sync this many days ahead

        Returns:
            ShiftSyncResult with counts and any errors
        """
        start_time = datetime.now(UTC)
        result = ShiftSyncResult()

        client = self._get_client()
        if not client:
            result.errors.append({"type": "config", "error": "ERP sync not configured or disabled"})
            return result

        # Default date range (longer for time-off to catch upcoming leave)
        today = datetime.now(UTC).date()
        if not from_date:
            from_date = today.isoformat()
        if not to_date:
            to_date = (today + timedelta(days=days_ahead)).isoformat()

        try:
            matched_techs = result.matched_technician_ids
            skipped_employees = result.skipped_employee_ids
            seen_time_off_ids: set[str] = set()
            limit = 1000
            offset = 0

            while True:
                time_off_records = client.get_employee_time_off(
                    from_date=from_date,
                    to_date=to_date,
                    limit=limit,
                    offset=offset,
                )

                if not time_off_records:
                    break

                logger.info(
                    "Fetched %d time-off records from ERP (%s to %s, offset=%d)",
                    len(time_off_records),
                    from_date,
                    to_date,
                    offset,
                )

                for time_off_data in time_off_records:
                    time_off_id = time_off_data.get("time_off_id")
                    if time_off_id:
                        seen_time_off_ids.add(str(time_off_id))
                    technician = self._resolve_technician(time_off_data)
                    if not technician:
                        emp_id = time_off_data.get("employee_id") or time_off_data.get("employee_email")
                        if emp_id:
                            skipped_employees.add(str(emp_id))
                        continue

                    matched_techs.add(str(technician.id))

                    action = self._upsert_time_off(time_off_data, technician)
                    if action == "created":
                        result.time_off_created += 1
                    elif action == "updated":
                        result.time_off_updated += 1

                if len(time_off_records) < limit:
                    break
                offset += limit

            # Deactivate time-off blocks no longer present in ERP for the date range.
            start_dt, end_dt = self._date_range_bounds(from_date, to_date)
            cleanup_query = (
                self.db.query(AvailabilityBlock)
                .filter(AvailabilityBlock.erp_id.isnot(None))
                .filter(AvailabilityBlock.is_active.is_(True))
                .filter(AvailabilityBlock.start_at >= start_dt)
                .filter(AvailabilityBlock.start_at < end_dt)
            )
            if seen_time_off_ids:
                cleanup_query = cleanup_query.filter(~AvailabilityBlock.erp_id.in_(seen_time_off_ids))
            cleanup_query.update({"is_active": False}, synchronize_session=False)

            result.technicians_matched = len(matched_techs)
            result.technicians_skipped = len(skipped_employees)

            self.db.commit()

        except DotMacERPError as e:
            logger.error(f"Time-off sync failed: {e}")
            result.errors.append({"type": "api", "error": str(e)})
            self.db.rollback()

        result.duration_seconds = (datetime.now(UTC) - start_time).total_seconds()
        return result

    def sync_all(
        self,
        days_ahead: int = 14,
        time_off_days_ahead: int = 30,
    ) -> ShiftSyncResult:
        """
        Sync both shifts and time-off from ERP.

        Args:
            days_ahead: Days ahead to sync shifts
            time_off_days_ahead: Days ahead to sync time-off

        Returns:
            Combined ShiftSyncResult
        """
        start_time = datetime.now(UTC)
        combined_result = ShiftSyncResult()

        # Sync shifts
        shift_result = self.sync_shifts(days_ahead=days_ahead)
        combined_result.shifts_created = shift_result.shifts_created
        combined_result.shifts_updated = shift_result.shifts_updated
        combined_result.errors.extend(shift_result.errors)

        # Sync time-off
        time_off_result = self.sync_time_off(days_ahead=time_off_days_ahead)
        combined_result.time_off_created = time_off_result.time_off_created
        combined_result.time_off_updated = time_off_result.time_off_updated
        combined_result.matched_technician_ids = (
            shift_result.matched_technician_ids | time_off_result.matched_technician_ids
        )
        combined_result.skipped_employee_ids = shift_result.skipped_employee_ids | time_off_result.skipped_employee_ids
        combined_result.technicians_matched = len(combined_result.matched_technician_ids)
        combined_result.technicians_skipped = len(combined_result.skipped_employee_ids)
        combined_result.errors.extend(time_off_result.errors)

        combined_result.duration_seconds = (datetime.now(UTC) - start_time).total_seconds()

        logger.info(
            f"Shift sync complete: {combined_result.shifts_created} shifts created, "
            f"{combined_result.shifts_updated} updated, "
            f"{combined_result.time_off_created} time-off created, "
            f"{combined_result.time_off_updated} updated"
        )

        return combined_result


# Factory function
def dotmac_erp_shift_sync(db: Session) -> DotMacERPShiftSync:
    """Create a DotMac ERP shift sync service instance."""
    return DotMacERPShiftSync(db)
