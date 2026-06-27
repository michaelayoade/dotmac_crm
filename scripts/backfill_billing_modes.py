from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
from sqlalchemy import text

from app.db import SessionLocal
from app.services import selfcare
from app.services.billing_risk_cache import _display_billing_type


def _extract_billing_fields(payload: dict[str, Any]) -> dict[str, str]:
    billing = payload.get("billing") if isinstance(payload.get("billing"), dict) else {}
    billing_mode = str(
        payload.get("billing_mode")
        or payload.get("billingMode")
        or billing.get("billing_mode")
        or billing.get("billingMode")
        or ""
    ).strip()
    subscription_billing_mode = str(
        payload.get("subscription_billing_mode")
        or payload.get("subscriptionBillingMode")
        or billing.get("subscription_billing_mode")
        or billing.get("subscriptionBillingMode")
        or ""
    ).strip()
    raw_billing_type = str(
        payload.get("billing_type")
        or payload.get("billingType")
        or billing.get("billing_type")
        or billing.get("billingType")
        or ""
    ).strip()
    billing_type = _display_billing_type(billing_mode, subscription_billing_mode, raw_billing_type)
    return {
        "billing_mode": billing_mode,
        "subscription_billing_mode": subscription_billing_mode,
        "billing_type": billing_type,
        "raw_billing_type": raw_billing_type,
    }


def _fetch_one(config: dict[str, Any], external_id: str) -> tuple[str, dict[str, str] | None, str | None]:
    url = selfcare._crm_url(config, f"/subscribers/{external_id}")
    try:
        response = requests.get(
            url,
            headers=selfcare._api_headers(config),
            timeout=int(config.get("timeout_seconds") or 30),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return external_id, None, str(exc)
    data = selfcare._unwrap_data(payload)
    if not isinstance(data, dict):
        return external_id, None, "non_object_payload"
    return external_id, _extract_billing_fields(data), None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--only-unknown", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        config = selfcare._get_api_config(db)
        where = ""
        if args.only_unknown:
            where = """
            where coalesce(source_metadata::jsonb->>'billing_mode', '') = ''
              and coalesce(source_metadata::jsonb->>'subscription_billing_mode', '') = ''
              and coalesce(source_metadata::jsonb->>'billing_type', '') in ('', 'unknown')
            """
        limit_sql = "limit :limit" if args.limit else ""
        rows = db.execute(
            text(
                f"""
                select external_id
                from subscriber_billing_risk_snapshots
                {where}
                order by name asc
                {limit_sql}
                """
            ),
            {"limit": args.limit},
        ).all()
        external_ids = [str(row.external_id) for row in rows if str(row.external_id or "").strip()]
    finally:
        db.close()

    updated = 0
    failed = 0
    fetched = 0
    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as executor:
        futures = {executor.submit(_fetch_one, config, external_id): external_id for external_id in external_ids}
        db = SessionLocal()
        try:
            for future in as_completed(futures):
                external_id, fields, error = future.result()
                fetched += 1
                if error or fields is None:
                    failed += 1
                else:
                    metadata_patch = {
                        "billing_mode": fields["billing_mode"],
                        "subscription_billing_mode": fields["subscription_billing_mode"],
                        "billing_type": fields["billing_type"],
                        "raw_billing_type": fields["raw_billing_type"],
                    }
                    db.execute(
                        text(
                            """
                            update subscriber_billing_risk_snapshots
                            set source_metadata = (
                                coalesce(source_metadata::jsonb, '{}'::jsonb) || cast(:metadata_patch as jsonb)
                            )::json,
                                updated_at = now()
                            where external_id = :external_id
                            """
                        ),
                        {
                            "external_id": external_id,
                            "metadata_patch": json.dumps(metadata_patch),
                        },
                    )
                    updated += 1
                if fetched % 100 == 0:
                    db.commit()
                    print(f"progress fetched={fetched} updated={updated} failed={failed}", flush=True)
            db.commit()
        finally:
            db.close()

    print(f"complete total={len(external_ids)} fetched={fetched} updated={updated} failed={failed}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
