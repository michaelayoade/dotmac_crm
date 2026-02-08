from fastapi import APIRouter

from app.api.crm.contacts import router as contacts_router
from app.api.crm.conversations import router as conversations_router
from app.api.crm.messages import router as messages_router
from app.api.crm.presence import router as presence_router
from app.api.crm.teams import router as teams_router
from app.api.crm.inbox import router as inbox_router
from app.api.crm.sales import router as sales_router
from app.api.crm.reports import router as reports_router

router = APIRouter(tags=["crm"])
router.include_router(contacts_router)
router.include_router(conversations_router)
router.include_router(messages_router)
router.include_router(presence_router)
router.include_router(teams_router)
router.include_router(inbox_router)
router.include_router(sales_router)
router.include_router(reports_router)

__all__ = ["router"]
