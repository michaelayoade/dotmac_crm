"""Smoke tests for web page rendering.

These tests verify that all major web pages load without errors (HTTP 200 or redirect).
They test against the running Docker container using httpx.

To run these tests:
1. Start the Docker containers: docker compose up -d
2. Run: poetry run pytest tests/test_smoke_web_pages.py -v
"""

from __future__ import annotations

import os
import pytest
import httpx

# Base URL for the running app in Docker
BASE_URL = os.environ.get("SMOKE_TEST_BASE_URL", "http://localhost:8000")


@pytest.fixture(scope="module")
def client():
    """Create an httpx client for making requests."""
    with httpx.Client(base_url=BASE_URL, timeout=30.0, follow_redirects=False) as client:
        yield client


@pytest.fixture(scope="module", autouse=True)
def check_app_running(client):
    """Check that the app is running before tests."""
    try:
        response = client.get("/health")
        if response.status_code != 200:
            pytest.skip("App is not healthy - run 'docker compose up -d' first")
    except httpx.ConnectError:
        pytest.skip("App is not running - run 'docker compose up -d' first")


# Public pages that don't require authentication
PUBLIC_PAGES = [
    "/",
    "/auth/login",
    "/auth/forgot-password",
    "/portal/auth/login",
]


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_public_page_loads(client, path):
    """Test that public pages return 200 or redirect."""
    response = client.get(path)
    assert response.status_code in (200, 302, 303, 307), (
        f"{path} returned {response.status_code}"
    )


# Admin pages (require auth - will redirect to login)
ADMIN_PAGES = [
    "/admin/dashboard",
    "/admin/support/tickets",  # tickets listing
    "/admin/projects",
    "/admin/crm/inbox",
    "/admin/crm/contacts",
    "/admin/operations/sales-orders",
    "/admin/operations/work-orders",
    "/admin/operations/installations",
    "/admin/operations/technicians",
    "/admin/inventory",
    "/admin/subscribers",
    "/admin/gis",
    "/admin/system",  # system settings
    "/admin/integrations/connectors",
    "/admin/integrations/webhooks",
    "/admin/reports/subscribers",  # subscribers report
    "/admin/resellers",
    "/admin/vendors",
    "/admin/system/legal",  # legal is under /system
]


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_admin_page_redirects_to_login(client, path):
    """Test that admin pages redirect to login when not authenticated."""
    response = client.get(path)
    # Should redirect to login or return 401/403
    assert response.status_code in (302, 303, 307, 401, 403), (
        f"{path} returned {response.status_code}, expected redirect or auth error"
    )


# Customer portal has limited routes - auth pages are public
# Contract pages require a valid service order ID, so we skip those


# API health endpoints
API_ENDPOINTS = [
    "/health",
]


@pytest.mark.parametrize("path", API_ENDPOINTS)
def test_api_health_endpoint(client, path):
    """Test that API health endpoints return 200."""
    response = client.get(path)
    assert response.status_code == 200, f"{path} returned {response.status_code}"


def test_404_page_loads(client):
    """Test that 404 pages render properly."""
    response = client.get("/nonexistent-page-12345")
    assert response.status_code == 404


def test_login_page_content(client):
    """Test that login page has expected content."""
    response = client.get("/auth/login")
    assert response.status_code == 200
    # Should contain a form
    assert b"<form" in response.content or b"form" in response.content.lower()


def test_customer_login_page_content(client):
    """Test that customer login page has expected content."""
    response = client.get("/portal/auth/login")
    assert response.status_code == 200
    assert b"<form" in response.content or b"form" in response.content.lower()
