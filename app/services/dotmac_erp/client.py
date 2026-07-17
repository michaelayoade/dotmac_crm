"""HTTP client for DotMac ERP (erp.dotmac.io)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Collection
from typing import Any

import httpx
from dotmac_integration import IntegrationHttpClient

logger = logging.getLogger(__name__)


class DotMacERPError(Exception):
    """Base exception for DotMac ERP client errors."""

    def __init__(self, message: str, status_code: int | None = None, response: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class DotMacERPAuthError(DotMacERPError):
    """Authentication error (401/403)."""

    pass


class DotMacERPNotFoundError(DotMacERPError):
    """Resource not found (404)."""

    pass


class DotMacERPRateLimitError(DotMacERPError):
    """Rate limit exceeded (429)."""

    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class DotMacERPTransientError(DotMacERPError):
    """Retryable/transient ERP error (e.g., 5xx, timeouts, network issues)."""


class DotMacERPClient:
    """
    HTTP client for DotMac ERP REST API.

    Features:
    - API key authentication (X-API-Key)
    - Automatic retry with exponential backoff
    - Idempotency key support for safe retries
    - Rate limit handling
    """

    DEFAULT_TIMEOUT = 30
    DEFAULT_RETRIES = 3
    DEFAULT_RETRY_DELAY = 1.0

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ):
        """
        Initialize the DotMac ERP client.

        Args:
            base_url: Base URL for ERP API (e.g., "https://erp.dotmac.io")
            token: API key for authentication (X-API-Key)
            timeout: Request timeout in seconds
            retries: Number of retry attempts
            retry_delay: Initial delay between retries (exponential backoff)
        """
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.retries = retries
        self.retry_delay = retry_delay
        self._client: httpx.Client | None = None
        self._transport: IntegrationHttpClient | None = None

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout,
                headers={
                    "X-API-Key": self.token,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "DotMac-CRM/1.0",
                },
            )
        return self._client

    def close(self):
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _handle_response(
        self,
        response: httpx.Response,
        *,
        expected_status_codes: Collection[int] | None = None,
    ) -> dict | list | None:
        """Handle API response and raise appropriate errors."""
        try:
            data = response.json() if response.content else None
        except Exception:
            data = None

        # Auth failures keep their dedicated error type even when the caller
        # narrows expected_status_codes — task-level handlers classify on it.
        if response.status_code == 401 or response.status_code == 403:
            raise DotMacERPAuthError(
                f"Authentication failed: {response.status_code}",
                status_code=response.status_code,
                response=data,
            )

        if expected_status_codes and response.status_code not in expected_status_codes:
            raise DotMacERPError(
                f"API unexpected status ({response.status_code}), expected {sorted(expected_status_codes)}",
                status_code=response.status_code,
                response=data if isinstance(data, dict) else None,
            )

        if response.status_code == 204:
            return None

        if response.status_code == 404:
            raise DotMacERPNotFoundError(
                "Resource not found",
                status_code=404,
                response=data,
            )

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise DotMacERPRateLimitError(
                "Rate limit exceeded",
                retry_after=int(retry_after) if retry_after else None,
            )

        if response.status_code >= 400:
            if isinstance(data, dict):
                error_msg = data.get("detail") or data.get("message") or data.get("error") or str(data)
            else:
                error_msg = str(data)
            logger.warning("ERP API error: status=%s body=%s", response.status_code, data)
            # 5xx are transient (proxy/gateway/unavailable/timeout or a passing
            # ERP blip) — raise the retryable type so _request retries instead of
            # failing the whole sync on a momentary hiccup. Idempotent natural-key
            # upserts on the ERP side make the retry safe.
            if response.status_code in (500, 502, 503, 504):
                raise DotMacERPTransientError(
                    f"ERP transient error ({response.status_code}): {error_msg}",
                    status_code=response.status_code,
                    response=data if isinstance(data, dict) else None,
                )
            raise DotMacERPError(
                f"API error ({response.status_code}): {error_msg}",
                status_code=response.status_code,
                response=data,
            )

        return data

    def _get_transport(self) -> IntegrationHttpClient:
        """The shared retry/transport engine, configured with this edge's policy.

        The retry semantics (Retry-After honouring, 5xx transient retry,
        connect/timeout retry, no retry on auth/4xx, and the give-up wrapping)
        live in ``IntegrationHttpClient``; this only declares which exception
        types mean what. ``_get_client`` and ``_handle_response`` remain the
        transport + response-mapping seams.
        """
        if self._transport is None:
            self._transport = IntegrationHttpClient(
                client_factory=self._get_client,
                response_handler=self._handle_response,
                backoff=lambda attempt: self.retry_delay * (2**attempt),
                max_attempts=self.retries + 1,
                rate_limit_exc=DotMacERPRateLimitError,
                retryable_excs=(DotMacERPTransientError,),
                non_retryable_excs=(DotMacERPError,),
                transport_exhausted_factory=lambda exc, retries: DotMacERPError(
                    f"Connection error after {retries} retries: {exc}"
                ),
                loop_exhausted_factory=lambda exc, retries: DotMacERPError(
                    f"Request failed after {retries} retries: {exc}"
                ),
                unexpected_error_factory=lambda exc: DotMacERPError(f"Unexpected error: {exc}"),
            )
        return self._transport

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_data: dict | list | None = None,
        idempotency_key: str | None = None,
        expected_status_codes: Collection[int] | None = None,
    ) -> dict | list | None:
        """Make an HTTP request with retry logic (delegated to the shared client)."""
        return self._get_transport().request(
            method,
            path,
            params=params,
            json_data=json_data,
            idempotency_key=idempotency_key,
            handler_kwargs={"expected_status_codes": expected_status_codes},
        )

    # ============ Public API Methods ============

    def test_connection(self) -> bool:
        """Test if the connection and authentication work."""
        try:
            # Use the CRM bulk sync endpoint with an empty payload to validate auth.
            self._request(
                "POST",
                "/api/v1/sync/crm/bulk",
                json_data={"projects": [], "tickets": [], "work_orders": []},
            )
            return True
        except DotMacERPAuthError:
            return False
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False

    def bulk_sync(
        self,
        projects: list[dict] | None = None,
        tickets: list[dict] | None = None,
        work_orders: list[dict] | None = None,
    ) -> dict:
        """
        Bulk sync projects, tickets, and work orders to ERP.

        Args:
            projects: List of project payloads
            tickets: List of ticket payloads
            work_orders: List of work order payloads

        Returns:
            Sync result with counts and errors
        """
        payload = {
            "projects": projects or [],
            "tickets": tickets or [],
            "work_orders": work_orders or [],
        }

        # Generate idempotency key for safe retries
        idempotency_key = f"sync-{uuid.uuid4()}"

        result = self._request(
            "POST",
            "/api/v1/sync/crm/bulk",
            json_data=payload,
            idempotency_key=idempotency_key,
        )
        return result if isinstance(result, dict) else {}

    def sync_project(self, project: dict) -> dict:
        """Sync a single project."""
        return self.bulk_sync(projects=[project])

    def sync_ticket(self, ticket: dict) -> dict:
        """Sync a single ticket."""
        return self.bulk_sync(tickets=[ticket])

    def sync_work_order(self, work_order: dict) -> dict:
        """Sync a single work order."""
        return self.bulk_sync(work_orders=[work_order])

    def get_expense_totals(
        self,
        project_omni_ids: list[str] | None = None,
        ticket_omni_ids: list[str] | None = None,
        work_order_omni_ids: list[str] | None = None,
    ) -> dict[str, dict]:
        """
        Get expense totals for synced entities.

        Returns:
            Dict mapping omni_id to expense totals
            e.g., {"uuid": {"draft": 0, "submitted": 1000, "approved": 500, "paid": 500}}
        """
        payload: dict[str, list[str]] = {}
        if project_omni_ids:
            payload["project_crm_ids"] = project_omni_ids
        if ticket_omni_ids:
            payload["ticket_crm_ids"] = ticket_omni_ids
        if work_order_omni_ids:
            payload["work_order_crm_ids"] = work_order_omni_ids

        if not payload:
            return {}

        # ERP expense totals are read-only but exposed as POST with a JSON body
        # containing CRM IDs grouped by entity type.
        result = self._request(
            "POST",
            "/sync/crm/expense-totals",
            json_data=payload,
            expected_status_codes={200},
        )
        if not isinstance(result, dict):
            return {}
        totals = result.get("totals")
        return totals if isinstance(totals, dict) else {}

    # ============ Material Request API Methods ============

    def push_material_request(self, payload: dict, idempotency_key: str | None = None) -> dict:
        """Push an approved material request to ERP.

        Args:
            payload: Material request data (see MaterialRequestSync._map_material_request)
            idempotency_key: Idempotency key for safe retries

        Returns:
            ERP response with request_id/request_number
        """
        result = self._request(
            "POST",
            "/sync/crm/material-requests",
            json_data=payload,
            idempotency_key=idempotency_key or f"mr-{uuid.uuid4()}",
            expected_status_codes={200, 201},
        )
        return result if isinstance(result, dict) else {}

    def get_material_request_status(self, omni_id: str) -> dict | None:
        """Check material request fulfillment status from ERP.

        Args:
            omni_id: CRM material request UUID

        Returns:
            Status dict or None if not found
        """
        try:
            result = self._request(
                "GET",
                f"/sync/crm/material-requests/{omni_id}",
                expected_status_codes={200},
            )
            return result if isinstance(result, dict) else None
        except DotMacERPNotFoundError:
            return None

    # ============ Expense Claim API Methods ============

    def push_expense_claim(self, payload: dict, idempotency_key: str | None = None) -> dict:
        """Push a submitted field expense request to ERP as an expense claim.

        Args:
            payload: Expense request data (see ExpenseRequestSync._map_expense_request)
            idempotency_key: Idempotency key for safe retries

        Returns:
            ERP response with claim_id/claim_number/status
        """
        result = self._request(
            "POST",
            "/sync/crm/expense-claims",
            json_data=payload,
            idempotency_key=idempotency_key or f"exp-{uuid.uuid4()}",
            expected_status_codes={200, 201},
        )
        return result if isinstance(result, dict) else {}

    def get_expense_claim_status(self, omni_id: str) -> dict | None:
        """Check expense claim approval/payment status from ERP.

        Args:
            omni_id: CRM expense request UUID

        Returns:
            Status dict or None if not found
        """
        try:
            result = self._request(
                "GET",
                f"/sync/crm/expense-claims/{omni_id}",
                expected_status_codes={200},
            )
            return result if isinstance(result, dict) else None
        except DotMacERPNotFoundError:
            return None

    def get_expense_categories(self) -> list[dict]:
        """List active ERP expense categories for field expense capture."""
        result = self._request(
            "GET",
            "/sync/crm/expense-categories",
            expected_status_codes={200},
        )
        if isinstance(result, dict):
            items = result.get("items")
            return items if isinstance(items, list) else []
        return result if isinstance(result, list) else []

    def list_available_serials(
        self,
        *,
        item_code: str,
        warehouse_code: str,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """List available ERP serials for one item in one warehouse."""
        params = {
            "item_code": item_code,
            "warehouse_code": warehouse_code,
            "limit": limit,
            "offset": offset,
        }
        result = self._request(
            "GET",
            "/api/v1/sync/crm/inventory/serials/available",
            params=params,
            expected_status_codes={200},
        )
        return result if isinstance(result, dict) else {}

    # ============ Purchase Order API Methods ============

    def create_purchase_order(self, payload: dict, idempotency_key: str | None = None) -> dict:
        """Push an approved vendor quote as a purchase order to ERP.

        Args:
            payload: Purchase order data (see PurchaseOrderSync._map_purchase_order)
            idempotency_key: Idempotency key for safe retries

        Returns:
            ERP response with purchase_order_id
        """
        result = self._request(
            "POST",
            "/api/v1/sync/crm/purchase-orders",
            json_data=payload,
            idempotency_key=idempotency_key or f"po-{uuid.uuid4()}",
            expected_status_codes={200, 201},
        )
        return result if isinstance(result, dict) else {}

    def create_purchase_invoice(self, payload: dict, idempotency_key: str | None = None) -> dict:
        """Push an approved vendor purchase invoice to ERP."""
        result = self._request(
            "POST",
            "/api/v1/sync/crm/purchase-invoices",
            json_data=payload,
            idempotency_key=idempotency_key or f"pinv-{uuid.uuid4()}",
        )
        return result if isinstance(result, dict) else {}

    def upload_purchase_invoice_attachment(
        self,
        purchase_invoice_id: str,
        payload: dict,
        idempotency_key: str | None = None,
    ) -> dict:
        """Upload a supporting attachment for an already-created ERP purchase invoice."""
        result = self._request(
            "POST",
            f"/api/v1/sync/crm/purchase-invoices/{purchase_invoice_id}/attachments",
            json_data=payload,
            idempotency_key=idempotency_key or f"pinv-attach-{uuid.uuid4()}",
        )
        return result if isinstance(result, dict) else {}

    # ============ Customer/Contact API Methods ============

    def get_companies(
        self,
        updated_since: str | None = None,
        include_inactive: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """Fetch B2B companies from ERP for Organization sync.

        Args:
            updated_since: ISO datetime for incremental sync
            include_inactive: Include archived companies
            limit: Pagination limit
            offset: Pagination offset

        Returns:
            List of company dicts
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if updated_since:
            params["updated_since"] = updated_since
        if include_inactive:
            params["include_inactive"] = "true"

        result = self._request("GET", "/api/v1/sync/crm/contacts/companies", params=params)
        if isinstance(result, dict):
            return result.get("companies", [])
        return result if isinstance(result, list) else []

    def get_contacts(
        self,
        updated_since: str | None = None,
        company_id: str | None = None,
        include_inactive: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """Fetch individual contacts from ERP for Person sync.

        Args:
            updated_since: ISO datetime for incremental sync
            company_id: Filter by company
            include_inactive: Include archived contacts
            limit: Pagination limit
            offset: Pagination offset

        Returns:
            List of contact dicts
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if updated_since:
            params["updated_since"] = updated_since
        if company_id:
            params["company_id"] = company_id
        if include_inactive:
            params["include_inactive"] = "true"

        result = self._request("GET", "/api/v1/sync/crm/contacts/people", params=params)
        if isinstance(result, dict):
            return result.get("contacts", [])
        return result if isinstance(result, list) else []

    # ============ Department API Methods ============

    def get_departments(
        self,
        include_inactive: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """Fetch departments from ERP for ServiceTeam sync.

        Args:
            include_inactive: Include archived departments
            limit: Pagination limit
            offset: Pagination offset

        Returns:
            List of department dicts with members
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if include_inactive:
            params["include_inactive"] = "true"

        def _parse_departments(payload: object) -> list[dict]:
            if isinstance(payload, dict):
                value = payload.get("departments", [])
                return value if isinstance(value, list) else []
            return payload if isinstance(payload, list) else []

        def _members_have_any_keys(departments: list[dict], keys: set[str]) -> bool:
            # Look at a small sample; we only use this to decide whether to try the alternate path.
            for dept in departments[:25]:
                members = dept.get("members") if isinstance(dept, dict) else None
                if not isinstance(members, list) or not members:
                    continue
                for m in members[:25]:
                    if isinstance(m, dict) and keys.intersection(m.keys()):
                        return True
            return False

        # Some deployments expose this endpoint without the /api/v1 prefix, and in some environments
        # these paths can be routed to different upstreams. Prefer /api/v1, but fall back to /sync if:
        # - /api/v1 returns 404, or
        # - /api/v1 responds but doesn't include expected member enrichment fields.
        result = None
        try:
            result = self._request("GET", "/api/v1/sync/crm/workforce/departments", params=params)
        except DotMacERPNotFoundError:
            result = self._request("GET", "/sync/crm/workforce/departments", params=params)

        departments = _parse_departments(result)
        if departments and not _members_have_any_keys(
            departments, {"designation_name", "designation_id", "designation"}
        ):
            try:
                alt = self._request("GET", "/sync/crm/workforce/departments", params=params)
                alt_departments = _parse_departments(alt)
                if alt_departments:
                    return alt_departments
            except DotMacERPNotFoundError:
                pass

        return departments

    # ============ Inventory API Methods ============

    def get_inventory_items(
        self,
        limit: int = 500,
        offset: int = 0,
        search: str | None = None,
        category_code: str | None = None,
        warehouse_id: str | None = None,
        include_zero_stock: bool = True,
        only_below_reorder: bool = False,
    ) -> list[dict]:
        """
        Fetch inventory items with stock levels from ERP.

        Args:
            limit: Maximum number of items to fetch
            offset: Pagination offset
            search: Search by item code, name, or barcode
            category_code: Filter by category
            warehouse_id: Filter by warehouse
            include_zero_stock: Include items with zero available (default: True for full sync)
            only_below_reorder: Only items below reorder point

        Returns:
            List of inventory item dicts with keys:
            - item_code (SKU), item_name, description, item_group (category)
            - stock_uom (unit), on_hand, reserved, available
            - list_price, currency, is_below_reorder
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if search:
            params["search"] = search
        if category_code:
            params["category_code"] = category_code
        if warehouse_id:
            params["warehouse_id"] = warehouse_id
        if include_zero_stock:
            params["include_zero_stock"] = "true"
        if only_below_reorder:
            params["only_below_reorder"] = "true"

        result = self._request("GET", "/api/v1/sync/crm/inventory", params=params)
        # Handle paginated response format
        if isinstance(result, dict):
            return result.get("items", [])
        return result if isinstance(result, list) else []

    def get_inventory_item_detail(self, item_id: str) -> dict | None:
        """
        Fetch detailed inventory item with warehouse breakdown from ERP.

        Args:
            item_id: Item ID or code

        Returns:
            Item dict with warehouse breakdown, or None if not found
        """
        try:
            result = self._request("GET", f"/api/v1/sync/crm/inventory/{item_id}")
            return result if isinstance(result, dict) else None
        except DotMacERPNotFoundError:
            return None

    def get_inventory_warehouses(self) -> list[dict]:
        """
        Fetch inventory warehouses (locations) from ERP.

        Returns:
            List of warehouse dicts with keys:
            - warehouse_id, warehouse_name, is_active
        """
        result = self._request("GET", "/api/v1/sync/crm/inventory/meta/warehouses")
        if isinstance(result, dict):
            return result.get("warehouses", [])
        return result if isinstance(result, list) else []

    def get_inventory_categories(self) -> list[dict]:
        """
        Fetch inventory item categories from ERP.

        Returns:
            List of category dicts with keys:
            - category_code, category_name
        """
        result = self._request("GET", "/api/v1/sync/crm/inventory/meta/categories")
        if isinstance(result, dict):
            return result.get("categories", [])
        return result if isinstance(result, list) else []

    # ============ Workforce/Shift API Methods ============

    def get_employee_shifts(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        employee_ids: list[str] | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """
        Fetch employee shifts from ERP.

        Args:
            from_date: Start date filter (ISO format YYYY-MM-DD)
            to_date: End date filter (ISO format YYYY-MM-DD)
            employee_ids: Filter by specific employee IDs
            limit: Maximum number of shifts to fetch
            offset: Pagination offset

        Returns:
            List of shift dicts with keys:
            - shift_id, employee_id, employee_email
            - start_at, end_at (ISO datetime)
            - shift_type (regular, overtime, on_call)
            - timezone
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if from_date:
            params["from_date"] = from_date
        if to_date:
            params["to_date"] = to_date
        if employee_ids:
            params["employee_ids"] = ",".join(employee_ids)

        result = self._request("GET", "/api/v1/sync/crm/workforce/shifts", params=params)
        if isinstance(result, dict):
            return result.get("shifts", [])
        return result if isinstance(result, list) else []

    def get_employee_time_off(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        employee_ids: list[str] | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """
        Fetch employee time-off/leave from ERP.

        Args:
            from_date: Start date filter (ISO format YYYY-MM-DD)
            to_date: End date filter (ISO format YYYY-MM-DD)
            employee_ids: Filter by specific employee IDs
            limit: Maximum number of records to fetch
            offset: Pagination offset

        Returns:
            List of time-off dicts with keys:
            - time_off_id, employee_id, employee_email
            - start_at, end_at (ISO datetime)
            - leave_type (annual, sick, training, etc.)
            - reason
            - status (approved, pending, etc.)
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if from_date:
            params["from_date"] = from_date
        if to_date:
            params["to_date"] = to_date
        if employee_ids:
            params["employee_ids"] = ",".join(employee_ids)

        result = self._request("GET", "/api/v1/sync/crm/workforce/time-off", params=params)
        if isinstance(result, dict):
            return result.get("time_off", [])
        return result if isinstance(result, list) else []

    def get_employees(
        self,
        include_inactive: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """
        Fetch employees from ERP for technician matching.

        Args:
            include_inactive: Include inactive employees
            limit: Maximum number of employees to fetch
            offset: Pagination offset

        Returns:
            List of employee dicts with keys:
            - employee_id, email, full_name
            - department, designation
            - is_active
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if include_inactive:
            params["include_inactive"] = "true"

        result = self._request("GET", "/api/v1/sync/crm/workforce/employees", params=params)
        if isinstance(result, dict):
            return result.get("employees", [])
        return result if isinstance(result, list) else []

    # ============ NCC Regulatory API Methods ============

    def get_ncc_financials(
        self,
        *,
        year: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        as_of_date: str | None = None,
    ) -> dict:
        """Fetch the NCC year-end return's Section F financials from ERP.

        Composed by ERP from its income-statement / balance-sheet /
        expense-summary services. Pass ``year`` for the annual return, or an
        explicit ``start_date``/``end_date``/``as_of_date`` range.

        Returns:
            dict with keys: period, summary, detail, note
        """
        params: dict[str, Any] = {}
        if year is not None:
            params["year"] = year
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if as_of_date:
            params["as_of_date"] = as_of_date

        result = self._request("GET", "/api/v1/sync/crm/ncc/financials", params=params or None)
        return result if isinstance(result, dict) else {}

    def get_ncc_staff_headcount(self) -> dict:
        """Fetch the NCC year-end return's Section G staff head-count from ERP.

        Returns:
            dict with keys: total_active, by_category
            (category -> nationality -> gender -> count)
        """
        result = self._request("GET", "/api/v1/sync/crm/ncc/staff-headcount")
        return result if isinstance(result, dict) else {}
