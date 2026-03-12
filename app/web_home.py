from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")

router = APIRouter()


@router.get("/", tags=["web"], response_class=HTMLResponse)
def home(request: Request):
    branding = getattr(request.state, "branding", {})
    title = branding.get("company_name") or "Dotmac"
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "title": title},
    )
