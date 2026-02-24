from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services import web_vendor_routes as web_vendor_routes_service

router = APIRouter(prefix="/vendor", tags=["web-vendor"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/", response_class=HTMLResponse)
def vendor_home(request: Request, db: Session = Depends(get_db)):
    return web_vendor_routes_service.vendor_home(request, db)


@router.get("/dashboard", response_class=HTMLResponse)
def vendor_dashboard(request: Request, db: Session = Depends(get_db)):
    return web_vendor_routes_service.vendor_dashboard(request, db)


@router.get("/projects/available", response_class=HTMLResponse)
def vendor_projects_available(request: Request, db: Session = Depends(get_db)):
    return web_vendor_routes_service.vendor_projects_available(request, db)


@router.get("/projects/mine", response_class=HTMLResponse)
def vendor_projects_mine(request: Request, db: Session = Depends(get_db)):
    return web_vendor_routes_service.vendor_projects_mine(request, db)


@router.get("/fiber-map", response_class=HTMLResponse)
def vendor_fiber_map(request: Request, db: Session = Depends(get_db)):
    return web_vendor_routes_service.vendor_fiber_map(request, db)


@router.post("/fiber-map/update-position")
async def vendor_fiber_map_update_position(request: Request, db: Session = Depends(get_db)):
    return await web_vendor_routes_service.vendor_fiber_map_update_position(request, db)


@router.post("/fiber-map/update-olt-role")
async def vendor_fiber_map_update_olt_role(request: Request, db: Session = Depends(get_db)):
    return await web_vendor_routes_service.vendor_fiber_map_update_olt_role(request, db)


@router.post("/fiber-map/save-plan")
async def vendor_fiber_map_save_plan(request: Request, db: Session = Depends(get_db)):
    return await web_vendor_routes_service.vendor_fiber_map_save_plan(request, db)


@router.get("/fiber-map/nearest-cabinet")
async def vendor_fiber_map_nearest_cabinet(request: Request, lat: float, lng: float, db: Session = Depends(get_db)):
    return await web_vendor_routes_service.vendor_fiber_map_nearest_cabinet(request, lat, lng, db)


@router.get("/fiber-map/plan-options")
async def vendor_fiber_map_plan_options(request: Request, lat: float, lng: float, db: Session = Depends(get_db)):
    return await web_vendor_routes_service.vendor_fiber_map_plan_options(request, lat, lng, db)


@router.get("/fiber-map/route")
async def vendor_fiber_map_route(
    request: Request, lat: float, lng: float, cabinet_id: str, db: Session = Depends(get_db)
):
    return await web_vendor_routes_service.vendor_fiber_map_route(request, lat, lng, cabinet_id, db)


@router.get("/fiber-map/asset-details")
async def vendor_fiber_map_asset_details(
    request: Request,
    asset_type: str,
    asset_id: str,
    db: Session = Depends(get_db),
):
    return await web_vendor_routes_service.vendor_fiber_map_asset_details(request, asset_type, asset_id, db)


@router.post("/fiber-map/merge")
async def vendor_fiber_map_merge(request: Request, db: Session = Depends(get_db)):
    return await web_vendor_routes_service.vendor_fiber_map_merge(request, db)


@router.get("/fiber-map/closure-duplicates.pdf")
def vendor_fiber_map_closure_duplicates_pdf(request: Request, db: Session = Depends(get_db)):
    return web_vendor_routes_service.vendor_fiber_map_closure_duplicates_pdf(request, db)


@router.get("/fiber-map/segments/geometry")
def vendor_fiber_map_segment_geometry(request: Request, segment_id: str, db: Session = Depends(get_db)):
    return web_vendor_routes_service.vendor_fiber_map_segment_geometry(request, segment_id, db)


@router.get("/quotes/builder", response_class=HTMLResponse)
def quote_builder(request: Request, project_id: str, db: Session = Depends(get_db)):
    return web_vendor_routes_service.quote_builder(request, project_id, db)


@router.get("/as-built/submit", response_class=HTMLResponse)
def as_built_submit(request: Request, project_id: str, db: Session = Depends(get_db)):
    return web_vendor_routes_service.as_built_submit(request, project_id, db)
