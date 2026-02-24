"""Subscriber sync tasks for external billing system integration."""

import logging
import time
from typing import Any

from app.celery_app import celery_app
from app.db import SessionLocal
from app.logging import get_logger
from app.metrics import observe_job
from app.services.subscriber import subscriber as subscriber_service

logger = get_logger(__name__)


@celery_app.task(name="app.tasks.subscribers.sync_subscribers_from_splynx")
def sync_subscribers_from_splynx() -> dict[str, Any]:
    """
    Reconciliation sync: pull all customers from Splynx using Basic auth
    from domain settings, and upsert local Subscriber records.

    Runs on a 24h schedule as a safety net. The primary creation path is
    via SplynxCustomerHandler on project_created events.
    """
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("SPLYNX_SYNC_START")

    results: dict[str, Any] = {"created": 0, "updated": 0, "errors": []}

    try:
        from app.services.splynx import fetch_customers, map_customer_to_subscriber_data

        customers_data = fetch_customers(session)
        if not customers_data:
            logger.info("splynx_sync_no_data")
            return results

        # Batch-load existing person emails for matching
        from app.models.person import Person

        person_by_email: dict[str, Any] = {}
        all_emails = [c.get("email", "").lower().strip() for c in customers_data if c.get("email")]
        if all_emails:
            persons = session.query(Person).filter(Person.email.in_(all_emails)).all()
            person_by_email = {p.email.lower(): p for p in persons if p.email}

        for customer in customers_data:
            try:
                external_id = str(customer.get("id"))
                data: dict[str, Any] = map_customer_to_subscriber_data(
                    session,
                    customer,
                    include_remote_details=True,
                )

                # Try to match to a Person by email
                email = (customer.get("email") or "").lower().strip()
                if email and email in person_by_email:
                    person = person_by_email[email]
                    data["person_id"] = person.id
                    data["organization_id"] = person.organization_id

                existing = subscriber_service.get_by_external_id(session, "splynx", external_id)

                subscriber_service.sync_from_external(session, "splynx", external_id, data)

                if existing:
                    results["updated"] += 1
                else:
                    results["created"] += 1

            except Exception as e:
                session.rollback()
                results["errors"].append({"external_id": customer.get("id"), "error": str(e)})
                logger.error("splynx_sync_customer_error id=%s error=%s", customer.get("id"), str(e))

        logger.info(
            "SPLYNX_SYNC_COMPLETE created=%d updated=%d errors=%d",
            results["created"],
            results["updated"],
            len(results["errors"]),
        )

    except Exception as e:
        status = "error"
        logger.error("SPLYNX_SYNC_ERROR error=%s", str(e))
        session.rollback()
        raise
    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("splynx_subscriber_sync", status, duration)

    return results


@celery_app.task(name="app.tasks.subscribers.reconcile_subscriber_identity")
def reconcile_subscriber_identity(
    external_system: str = "splynx",
    clear_duplicate_metadata: bool = True,
) -> dict[str, Any]:
    """
    Reconcile subscriber/contact identity and normalize party status.

    Steps:
    - Link subscribers to people via external IDs (for Splynx: metadata.splynx_id)
    - Optionally clear duplicate ID metadata on non-linked duplicate people
    - Normalize Person.party_status based on active subscriber linkage
    """
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("SUBSCRIBER_IDENTITY_RECONCILE_START external_system=%s", external_system)

    results: dict[str, Any] = {
        "external_system": external_system,
        "link_reconciliation": {},
        "status_reconciliation": {},
    }

    try:
        results["link_reconciliation"] = subscriber_service.reconcile_external_people_links(
            session,
            external_system=external_system,
            clear_duplicate_metadata=clear_duplicate_metadata,
            dry_run=False,
        )
        results["status_reconciliation"] = subscriber_service.reconcile_party_status_from_subscribers(
            session,
            dry_run=False,
        )

        logger.info(
            "SUBSCRIBER_IDENTITY_RECONCILE_COMPLETE external_system=%s linked=%d unmatched=%d upgraded=%d downgraded=%d",
            external_system,
            results["link_reconciliation"].get("linked_subscribers", 0),
            results["link_reconciliation"].get("unmatched_subscribers", 0),
            results["status_reconciliation"].get("upgraded_to_subscriber", 0),
            results["status_reconciliation"].get("downgraded_to_customer", 0),
        )
    except Exception as e:
        status = "error"
        session.rollback()
        logger.error("SUBSCRIBER_IDENTITY_RECONCILE_ERROR external_system=%s error=%s", external_system, str(e))
        raise
    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("subscriber_identity_reconcile", status, duration)

    return results


@celery_app.task(name="app.tasks.subscribers.sync_subscribers_from_ucrm")
def sync_subscribers_from_ucrm(config: dict[str, Any] | None = None):
    """
    Sync subscribers from UCRM/UNMS billing system.

    Args:
        config: Connection config with api_url, api_key, etc.
    """
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("UCRM_SYNC_START")

    results: dict[str, Any] = {"created": 0, "updated": 0, "errors": []}

    try:
        if not config:
            logger.warning("ucrm_sync_no_config")
            return results

        # Fetch clients from UCRM API
        clients_data = _fetch_ucrm_clients(config, logger)

        for client in clients_data:
            try:
                external_id = str(client.get("id"))
                data = {
                    "subscriber_number": client.get("userIdent"),
                    "account_number": client.get("customId"),
                    "status": "active" if client.get("isActive") else "suspended",
                    "service_name": client.get("servicePlanName"),
                    "balance": str(client.get("accountBalance", 0)),
                    "currency": client.get("currencyCode", "USD"),
                    "service_address_line1": client.get("street1"),
                    "service_address_line2": client.get("street2"),
                    "service_city": client.get("city"),
                    "service_region": client.get("state"),
                    "service_postal_code": client.get("zipCode"),
                    "service_country_code": client.get("countryId"),
                }

                existing = subscriber_service.get_by_external_id(session, "ucrm", external_id)

                subscriber_service.sync_from_external(session, "ucrm", external_id, data)

                if existing:
                    results["updated"] += 1
                else:
                    results["created"] += 1

            except Exception as e:
                results["errors"].append({"external_id": client.get("id"), "error": str(e)})
                logger.error("ucrm_sync_client_error id=%s error=%s", client.get("id"), str(e))

        logger.info(
            "UCRM_SYNC_COMPLETE created=%d updated=%d errors=%d",
            results["created"],
            results["updated"],
            len(results["errors"]),
        )

    except Exception as e:
        status = "error"
        logger.error("UCRM_SYNC_ERROR error=%s", str(e))
        session.rollback()
        raise
    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("ucrm_subscriber_sync", status, duration)

    return results


@celery_app.task(name="app.tasks.subscribers.sync_subscribers_generic")
def sync_subscribers_generic(
    external_system: str,
    subscribers_data: list[dict[str, Any]],
):
    """
    Generic subscriber sync task for any external system.

    Args:
        external_system: Name of the external system
        subscribers_data: List of subscriber records in normalized format
    """
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("GENERIC_SYNC_START system=%s count=%d", external_system, len(subscribers_data))

    results: dict[str, Any] = {"created": 0, "updated": 0, "errors": []}

    try:
        for sub_data in subscribers_data:
            try:
                external_id = sub_data.get("external_id") or sub_data.get("id")
                if not external_id:
                    results["errors"].append({"error": "Missing external_id"})
                    continue

                external_id = str(external_id)

                existing = subscriber_service.get_by_external_id(session, external_system, external_id)

                subscriber_service.sync_from_external(session, external_system, external_id, sub_data)

                if existing:
                    results["updated"] += 1
                else:
                    results["created"] += 1

            except Exception as e:
                results["errors"].append({"external_id": sub_data.get("external_id", "unknown"), "error": str(e)})
                logger.error("generic_sync_error system=%s error=%s", external_system, str(e))

        logger.info(
            "GENERIC_SYNC_COMPLETE system=%s created=%d updated=%d errors=%d",
            external_system,
            results["created"],
            results["updated"],
            len(results["errors"]),
        )

    except Exception as e:
        status = "error"
        logger.error("GENERIC_SYNC_ERROR system=%s error=%s", external_system, str(e))
        session.rollback()
        raise
    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("generic_subscriber_sync", status, duration)

    return results


def _fetch_ucrm_clients(config: dict[str, Any], logger: logging.Logger) -> list[dict[str, Any]]:
    """Fetch clients from UCRM/UNMS API."""
    import requests

    api_url = config.get("api_url", "").rstrip("/")
    api_key = config.get("api_key")

    if not all([api_url, api_key]):
        logger.warning("ucrm_incomplete_config")
        return []

    headers = {
        "Content-Type": "application/json",
        "X-Auth-App-Key": api_key,
    }

    try:
        clients_url = f"{api_url}/api/v1.0/clients"
        response = requests.get(clients_url, headers=headers, timeout=60)
        response.raise_for_status()
        return response.json()

    except requests.RequestException as e:
        logger.error("ucrm_api_error error=%s", str(e))
        return []


def _map_splynx_status(status: str | int | None) -> str:
    """Backward-compatible status mapper retained for tests/imports."""
    from app.services.splynx import _map_splynx_status as _service_status_mapper

    return _service_status_mapper(status)
