"""Operations page objects."""

from tests.playwright.pages.admin.operations.service_orders_page import ServiceOrdersPage
from tests.playwright.pages.admin.operations.service_order_form_page import ServiceOrderFormPage
from tests.playwright.pages.admin.operations.service_order_detail_page import ServiceOrderDetailPage
from tests.playwright.pages.admin.operations.work_orders_page import WorkOrdersPage
from tests.playwright.pages.admin.operations.dispatch_page import DispatchPage

__all__ = [
    "ServiceOrdersPage",
    "ServiceOrderFormPage",
    "ServiceOrderDetailPage",
    "WorkOrdersPage",
    "DispatchPage",
]
