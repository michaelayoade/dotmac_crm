"""Subscriber sync tasks for external billing system integration."""
import time
from typing import Any

from app.celery_app import celery_app
from app.db import SessionLocal
from app.logging import get_logger
from app.metrics import observe_job
from app.services.subscriber import subscriber as subscriber_service


@celery_app.task(name="app.tasks.subscribers.sync_subscribers_from_splynx")
def sync_subscribers_from_splynx(config: dict[str, Any] | None = None):
    """
    Sync subscribers from Splynx billing system.

    Args:
        config: Connection config with api_url, api_key, etc.
    """
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("SPLYNX_SYNC_START")

    results: dict[str, Any] = {"created": 0, "updated": 0, "errors": []}

    try:
        if not config:
            logger.warning("splynx_sync_no_config")
            return results

        # Fetch subscribers from Splynx API
        subscribers_data = _fetch_splynx_customers(config, logger)

        for customer in subscribers_data:
            try:
                external_id = str(customer.get("id"))
                data = {
                    "subscriber_number": customer.get("login"),
                    "status": _map_splynx_status(customer.get("status")),
                    "service_name": customer.get("tariff_name"),
                    "service_plan": customer.get("tariff_name"),
                    "balance": str(customer.get("balance", 0)),
                    "currency": customer.get("currency", "USD"),
                    "service_address_line1": customer.get("street"),
                    "service_city": customer.get("city"),
                    "service_region": customer.get("state"),
                    "service_postal_code": customer.get("zip"),
                }

                existing = subscriber_service.get_by_external_id(
                    session, "splynx", external_id
                )

                subscriber_service.sync_from_external(
                    session, "splynx", external_id, data
                )

                if existing:
                    results["updated"] += 1
                else:
                    results["created"] += 1

            except Exception as e:
                results["errors"].append({
                    "external_id": customer.get("id"),
                    "error": str(e)
                })
                logger.error("splynx_sync_customer_error id=%s error=%s",
                           customer.get("id"), str(e))

        logger.info("SPLYNX_SYNC_COMPLETE created=%d updated=%d errors=%d",
                   results["created"], results["updated"], len(results["errors"]))

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

                existing = subscriber_service.get_by_external_id(
                    session, "ucrm", external_id
                )

                subscriber_service.sync_from_external(
                    session, "ucrm", external_id, data
                )

                if existing:
                    results["updated"] += 1
                else:
                    results["created"] += 1

            except Exception as e:
                results["errors"].append({
                    "external_id": client.get("id"),
                    "error": str(e)
                })
                logger.error("ucrm_sync_client_error id=%s error=%s",
                           client.get("id"), str(e))

        logger.info("UCRM_SYNC_COMPLETE created=%d updated=%d errors=%d",
                   results["created"], results["updated"], len(results["errors"]))

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
    logger.info("GENERIC_SYNC_START system=%s count=%d",
               external_system, len(subscribers_data))

    results: dict[str, Any] = {"created": 0, "updated": 0, "errors": []}

    try:
        for sub_data in subscribers_data:
            try:
                external_id = sub_data.get("external_id") or sub_data.get("id")
                if not external_id:
                    results["errors"].append({
                        "error": "Missing external_id"
                    })
                    continue

                external_id = str(external_id)

                existing = subscriber_service.get_by_external_id(
                    session, external_system, external_id
                )

                subscriber_service.sync_from_external(
                    session, external_system, external_id, sub_data
                )

                if existing:
                    results["updated"] += 1
                else:
                    results["created"] += 1

            except Exception as e:
                results["errors"].append({
                    "external_id": sub_data.get("external_id", "unknown"),
                    "error": str(e)
                })
                logger.error("generic_sync_error system=%s error=%s",
                           external_system, str(e))

        logger.info("GENERIC_SYNC_COMPLETE system=%s created=%d updated=%d errors=%d",
                   external_system, results["created"], results["updated"],
                   len(results["errors"]))

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


def _fetch_splynx_customers(config: dict[str, Any], logger) -> list[dict]:
    """Fetch customers from Splynx API."""
    import requests

    api_url = config.get("api_url", "").rstrip("/")
    api_key = config.get("api_key")
    api_secret = config.get("api_secret")

    if not all([api_url, api_key]):
        logger.warning("splynx_incomplete_config")
        return []

    headers = {
        "Content-Type": "application/json",
    }

    # Splynx uses API key/secret authentication
    auth_url = f"{api_url}/admin/auth/tokens"
    try:
        auth_response = requests.post(
            auth_url,
            json={"auth_type": "api_key", "key": api_key, "secret": api_secret},
            headers=headers,
            timeout=30,
        )
        auth_response.raise_for_status()
        token = auth_response.json().get("access_token")

        headers["Authorization"] = f"Splynx-EA (access_token={token})"

        # Fetch customers
        customers_url = f"{api_url}/admin/customers/customer"
        response = requests.get(customers_url, headers=headers, timeout=60)
        response.raise_for_status()
        return response.json()

    except requests.RequestException as e:
        logger.error("splynx_api_error error=%s", str(e))
        return []


def _fetch_ucrm_clients(config: dict[str, Any], logger) -> list[dict]:
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
    """Map Splynx status to our status enum value."""
    status_map = {
        "active": "active",
        "blocked": "suspended",
        "inactive": "terminated",
        "new": "pending",
        1: "active",
        2: "suspended",
        0: "terminated",
    }
    return status_map.get(status, "active")
