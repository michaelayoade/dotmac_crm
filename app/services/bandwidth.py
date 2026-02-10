from __future__ import annotations

import builtins
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bandwidth import BandwidthSample
from app.models.subscriber import Subscriber, SubscriberStatus
from app.schemas.bandwidth import BandwidthSampleCreate, BandwidthSampleUpdate
from app.services.common import apply_ordering, apply_pagination, coerce_uuid
from app.services.metrics_store import get_metrics_store
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


class BandwidthSamples(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: BandwidthSampleCreate):
        sample = BandwidthSample(**payload.model_dump())
        db.add(sample)
        db.commit()
        db.refresh(sample)
        return sample

    @staticmethod
    def get(db: Session, sample_id: str):
        sample = db.get(BandwidthSample, coerce_uuid(sample_id))
        if not sample:
            raise HTTPException(status_code=404, detail="Bandwidth sample not found")
        return sample

    @staticmethod
    def list(
        db: Session,
        subscription_id: str | None,
        device_id: str | None,
        interface_id: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(BandwidthSample)
        if subscription_id:
            query = query.filter(BandwidthSample.subscription_id == coerce_uuid(subscription_id))
        if device_id:
            query = query.filter(BandwidthSample.device_id == coerce_uuid(device_id))
        if interface_id:
            query = query.filter(BandwidthSample.interface_id == coerce_uuid(interface_id))
        if start_at:
            query = query.filter(BandwidthSample.sample_at >= start_at)
        if end_at:
            query = query.filter(BandwidthSample.sample_at <= end_at)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": BandwidthSample.created_at, "sample_at": BandwidthSample.sample_at},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, sample_id: str, payload: BandwidthSampleUpdate):
        sample = db.get(BandwidthSample, coerce_uuid(sample_id))
        if not sample:
            raise HTTPException(status_code=404, detail="Bandwidth sample not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(sample, key, value)
        db.commit()
        db.refresh(sample)
        return sample

    @staticmethod
    def delete(db: Session, sample_id: str):
        sample = db.get(BandwidthSample, coerce_uuid(sample_id))
        if not sample:
            raise HTTPException(status_code=404, detail="Bandwidth sample not found")
        db.delete(sample)
        db.commit()

    @staticmethod
    def check_subscription_access(db: Session, subscription_id: UUID, current_user: dict) -> None:
        roles = set(current_user.get("roles") or [])
        if "admin" in roles or "support" in roles:
            return
        person_id = current_user.get("person_id")
        if not person_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        subscriber = (
            db.query(Subscriber)
            .filter(Subscriber.id == subscription_id)
            .filter(Subscriber.person_id == coerce_uuid(person_id))
            .first()
        )
        if not subscriber:
            raise HTTPException(status_code=403, detail="Forbidden")

    @staticmethod
    def get_user_active_subscription(db: Session, current_user: dict) -> Subscriber:
        person_id = current_user.get("person_id")
        if not person_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        subscriber = (
            db.query(Subscriber)
            .filter(Subscriber.person_id == coerce_uuid(person_id))
            .filter(Subscriber.status == SubscriberStatus.active)
            .first()
        )
        if not subscriber:
            raise HTTPException(status_code=404, detail="Active subscription not found")
        return subscriber

    @staticmethod
    async def get_bandwidth_series(
        db: Session,
        subscription_id: UUID,
        start_at: datetime | None,
        end_at: datetime | None,
        interval: str,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        end_at = end_at or now
        if start_at is None:
            start_at = end_at - timedelta(hours=24)
        query = (
            db.query(BandwidthSample)
            .filter(BandwidthSample.subscription_id == subscription_id)
            .filter(BandwidthSample.sample_at >= start_at)
            .filter(BandwidthSample.sample_at <= end_at)
            .order_by(BandwidthSample.sample_at.asc())
        )
        samples = query.all()
        data = [
            {
                "timestamp": sample.sample_at,
                "rx_bps": float(sample.rx_bps),
                "tx_bps": float(sample.tx_bps),
            }
            for sample in samples
        ]
        return {"data": data, "total": len(samples), "source": "postgres"}

    @staticmethod
    async def get_bandwidth_stats(
        db: Session, subscription_id: UUID, period: str
    ) -> dict[str, Any]:
        period_seconds = {
            "1h": 3600,
            "24h": 86400,
            "7d": 7 * 86400,
            "30d": 30 * 86400,
        }.get(period, 86400)
        now = datetime.now(UTC)
        start_at = now - timedelta(seconds=period_seconds)
        query = (
            db.query(BandwidthSample)
            .filter(BandwidthSample.subscription_id == subscription_id)
            .filter(BandwidthSample.sample_at >= start_at)
            .filter(BandwidthSample.sample_at <= now)
        )
        current_sample = (
            query.order_by(BandwidthSample.sample_at.desc()).first()
        )
        peak_rx = query.with_entities(func.max(BandwidthSample.rx_bps)).scalar() or 0
        peak_tx = query.with_entities(func.max(BandwidthSample.tx_bps)).scalar() or 0
        total_rx = query.with_entities(func.sum(BandwidthSample.rx_bps)).scalar() or 0
        total_tx = query.with_entities(func.sum(BandwidthSample.tx_bps)).scalar() or 0
        sample_count = query.count()
        return {
            "current_rx_bps": float(current_sample.rx_bps) if current_sample else 0.0,
            "current_tx_bps": float(current_sample.tx_bps) if current_sample else 0.0,
            "peak_rx_bps": float(peak_rx),
            "peak_tx_bps": float(peak_tx),
            "total_rx_bytes": float(total_rx),
            "total_tx_bytes": float(total_tx),
            "sample_count": sample_count,
        }

    @staticmethod
    async def get_top_users(db: Session, limit: int, duration: str) -> builtins.list[dict[str, Any]]:
        metrics_store = get_metrics_store()
        return await metrics_store.get_top_users(limit, duration)

    @staticmethod
    def series(
        db: Session,
        subscription_id: str | None,
        device_id: str | None,
        interface_id: str | None,
        start_at: datetime,
        end_at: datetime,
        interval: str = "minute",
        agg: str = "avg",
    ) -> builtins.list[dict]:
        """Get time series data for bandwidth samples."""
        # Build time bucket expression based on interval
        if interval == "hour":
            bucket = func.date_trunc("hour", BandwidthSample.sample_at)
        elif interval == "day":
            bucket = func.date_trunc("day", BandwidthSample.sample_at)
        else:
            bucket = func.date_trunc("minute", BandwidthSample.sample_at)

        # Build aggregation based on agg type
        rx_agg: Any
        tx_agg: Any
        if agg == "sum":
            rx_agg = func.sum(BandwidthSample.rx_bps)
            tx_agg = func.sum(BandwidthSample.tx_bps)
        elif agg == "max":
            rx_agg = func.max(BandwidthSample.rx_bps)
            tx_agg = func.max(BandwidthSample.tx_bps)
        else:  # avg
            rx_agg = func.avg(BandwidthSample.rx_bps)
            tx_agg = func.avg(BandwidthSample.tx_bps)

        query = (
            db.query(
                bucket.label("bucket"),
                rx_agg.label("rx_bps"),
                tx_agg.label("tx_bps"),
            )
            .filter(BandwidthSample.sample_at >= start_at)
            .filter(BandwidthSample.sample_at <= end_at)
        )

        if subscription_id:
            query = query.filter(BandwidthSample.subscription_id == coerce_uuid(subscription_id))
        if device_id:
            query = query.filter(BandwidthSample.device_id == coerce_uuid(device_id))
        if interface_id:
            query = query.filter(BandwidthSample.interface_id == coerce_uuid(interface_id))

        query = query.group_by(bucket).order_by(bucket)

        results: list[dict] = []
        for row in query.all():
            results.append({
                "timestamp": row.bucket.isoformat() if row.bucket else None,
                "rx_bps": int(row.rx_bps or 0),
                "tx_bps": int(row.tx_bps or 0),
            })
        return results


bandwidth_samples = BandwidthSamples()
