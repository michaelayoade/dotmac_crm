from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
import uuid

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.gis import GeoLocation, GeoLocationType
from app.models.network import OLTDevice, FdhCabinet
from app.services.response import ListResponseMixin


@dataclass
class SyncResult(ListResponseMixin):
    created: int = 0
    updated: int = 0
    skipped: int = 0


class GeoSync(ListResponseMixin):
    @staticmethod
    def sync_sources(
        db: Session,
        background_tasks: BackgroundTasks,
        sync_olts: bool,
        sync_fdhs: bool,
        deactivate_missing: bool,
        background: bool,
    ) -> Mapping[str, object]:
        if background:
            return GeoSync.queue_sync(
                background_tasks, sync_olts, sync_fdhs, deactivate_missing
            )
        return GeoSync.run_sync(db, sync_olts, sync_fdhs, deactivate_missing)

    @staticmethod
    def run_sync(
        db: Session,
        sync_olts: bool,
        sync_fdhs: bool,
        deactivate_missing: bool,
    ) -> dict[str, dict[str, int]]:
        results: dict[str, dict[str, int]] = {}
        if sync_olts:
            result = GeoSync.sync_olt_devices(db, deactivate_missing=deactivate_missing)
            results["olt_devices"] = {
                "created": result.created,
                "updated": result.updated,
                "skipped": result.skipped,
            }
        if sync_fdhs:
            result = GeoSync.sync_fdh_cabinets(db, deactivate_missing=deactivate_missing)
            results["fdh_cabinets"] = {
                "created": result.created,
                "updated": result.updated,
                "skipped": result.skipped,
            }
        return results

    @staticmethod
    def queue_sync(
        background_tasks: BackgroundTasks,
        sync_olts: bool,
        sync_fdhs: bool,
        deactivate_missing: bool,
    ) -> dict[str, str]:
        def _run_sync() -> None:
            session = SessionLocal()
            try:
                GeoSync.run_sync(
                    session,
                    sync_olts=sync_olts,
                    sync_fdhs=sync_fdhs,
                    deactivate_missing=deactivate_missing,
                )
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        background_tasks.add_task(_run_sync)
        return {"status": "queued"}

    @staticmethod
    def sync_olt_devices(db: Session, deactivate_missing: bool = False) -> SyncResult:
        result = SyncResult()
        olts = db.query(OLTDevice).all()
        seen_ids: set[uuid.UUID] = set()
        for olt in olts:
            if olt.latitude is None or olt.longitude is None:
                result.skipped += 1
                continue
            seen_ids.add(olt.id)
            existing = (
                db.query(GeoLocation)
                .filter(GeoLocation.olt_device_id == olt.id)
                .first()
            )
            if existing:
                existing.name = olt.name
                existing.location_type = GeoLocationType.network_device
                existing.latitude = olt.latitude
                existing.longitude = olt.longitude
                existing.is_active = olt.is_active
                result.updated += 1
            else:
                db.add(
                    GeoLocation(
                        name=olt.name,
                        location_type=GeoLocationType.network_device,
                        latitude=olt.latitude,
                        longitude=olt.longitude,
                        olt_device_id=olt.id,
                        is_active=olt.is_active,
                    )
                )
                result.created += 1
        if deactivate_missing:
            missing_query = db.query(GeoLocation).filter(
                GeoLocation.olt_device_id.isnot(None)
            )
            if seen_ids:
                missing_query = missing_query.filter(
                    GeoLocation.olt_device_id.notin_(seen_ids)
                )
            missing_query.update({"is_active": False}, synchronize_session=False)
        db.commit()
        return result

    @staticmethod
    def sync_fdh_cabinets(db: Session, deactivate_missing: bool = False) -> SyncResult:
        result = SyncResult()
        fdhs = db.query(FdhCabinet).all()
        seen_ids: set[uuid.UUID] = set()
        for fdh in fdhs:
            if fdh.latitude is None or fdh.longitude is None:
                result.skipped += 1
                continue
            seen_ids.add(fdh.id)
            existing = (
                db.query(GeoLocation)
                .filter(GeoLocation.fdh_cabinet_id == fdh.id)
                .first()
            )
            if existing:
                existing.name = fdh.name
                existing.location_type = GeoLocationType.fdh
                existing.latitude = fdh.latitude
                existing.longitude = fdh.longitude
                existing.is_active = fdh.is_active
                result.updated += 1
            else:
                db.add(
                    GeoLocation(
                        name=fdh.name,
                        location_type=GeoLocationType.fdh,
                        latitude=fdh.latitude,
                        longitude=fdh.longitude,
                        fdh_cabinet_id=fdh.id,
                        is_active=fdh.is_active,
                    )
                )
                result.created += 1
        if deactivate_missing:
            missing_query = db.query(GeoLocation).filter(
                GeoLocation.fdh_cabinet_id.isnot(None)
            )
            if seen_ids:
                missing_query = missing_query.filter(
                    GeoLocation.fdh_cabinet_id.notin_(seen_ids)
                )
            missing_query.update({"is_active": False}, synchronize_session=False)
        db.commit()
        return result


geo_sync = GeoSync()
