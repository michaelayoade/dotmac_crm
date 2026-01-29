from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services import person as person_service

templates = Jinja2Templates(directory="templates")

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/", tags=["web"], response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    people = person_service.people.list(
        db=db,
        email=None,
        status=None,
        party_status=None,
        organization_id=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=25,
        offset=0,
    )
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "title": "Dotmac SM", "people": people},
    )
