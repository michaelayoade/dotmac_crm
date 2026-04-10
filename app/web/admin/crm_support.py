"""Shared support helpers for CRM admin routes."""

import base64
import mimetypes
from importlib import import_module
from urllib.parse import urljoin, urlparse

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.csrf import get_csrf_token
from app.logging import get_logger
from app.models.crm.sales import Lead, PipelineStage, Quote
from app.models.person import Person
from app.services import crm as crm_service
from app.services.common import coerce_uuid

logger = get_logger(__name__)


class _TaxRate:
    def __init__(self, id: str, name: str, rate: float):
        self.id = id
        self.name = name
        self.rate = rate


_DEFAULT_TAX_RATES = [
    _TaxRate("vat-0", "No Tax (0%)", 0.0),
    _TaxRate("vat-5", "VAT 5%", 0.05),
    _TaxRate("vat-7.5", "VAT 7.5%", 0.075),
    _TaxRate("vat-10", "VAT 10%", 0.10),
    _TaxRate("vat-15", "VAT 15%", 0.15),
]
_TAX_RATES_BY_ID = {rate.id: rate for rate in _DEFAULT_TAX_RATES}


class _StubTaxRates:
    @staticmethod
    def list(*args, **kwargs):
        return _DEFAULT_TAX_RATES

    @staticmethod
    def get(db, tax_rate_id: str):
        return _TAX_RATES_BY_ID.get(tax_rate_id)


class _StubBillingService:
    tax_rates = _StubTaxRates()


billing_service = _StubBillingService()


def _ensure_pydyf_compat() -> None:
    try:
        pydyf = import_module("pydyf")
    except Exception:
        return
    try:
        init_args = pydyf.PDF.__init__.__code__.co_argcount
    except Exception:
        return
    if init_args == 1:
        original_init = pydyf.PDF.__init__

        def _compat_init(self, *args, **kwargs):
            original_init(self)
            version = args[0] if len(args) > 0 else kwargs.get("version")
            identifier = args[1] if len(args) > 1 else kwargs.get("identifier")
            if version is not None:
                self.version = version if isinstance(version, bytes | bytearray) else str(version).encode()
            if identifier is not None:
                self.identifier = identifier
            if not hasattr(self, "version"):
                self.version = b"1.7"
            if not hasattr(self, "identifier"):
                self.identifier = None
            return None

        pydyf.PDF.__init__ = _compat_init
    if not hasattr(pydyf.Stream, "transform"):

        def _compat_transform(self, a=1, b=0, c=0, d=1, e=0, f=0):
            return self.set_matrix(a, b, c, d, e, f)

        pydyf.Stream.transform = _compat_transform
    if not hasattr(pydyf.Stream, "text_matrix"):

        def _compat_text_matrix(self, a=1, b=0, c=0, d=1, e=0, f=0):
            return self.set_matrix(a, b, c, d, e, f)

        pydyf.Stream.text_matrix = _compat_text_matrix


def _crm_base_context(request: Request, db: Session, active_page: str) -> dict:
    from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats

    return {
        "request": request,
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "active_page": active_page,
        "active_menu": "crm",
        "csrf_token": get_csrf_token(request),
    }


def _load_crm_sales_options(db: Session) -> dict:
    contacts = crm_service.contacts.list(
        db=db,
        person_id=None,
        organization_id=None,
        party_status=None,
        is_active=True,
        search=None,
        order_by="display_name",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    from app.services.person import people as person_svc

    people = person_svc.list(
        db=db,
        email=None,
        status=None,
        party_status=None,
        organization_id=None,
        is_active=True,
        order_by="last_name",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    pipelines = crm_service.pipelines.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    stages = crm_service.pipeline_stages.list(
        db=db,
        pipeline_id=None,
        is_active=True,
        order_by="order_index",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    agents = crm_service.agents.list(
        db=db,
        person_id=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    try:
        from app.models.crm.team import CrmAgent
        from app.models.service_team import ServiceTeamMember

        sales_team_id = coerce_uuid("7ba88183-1f51-438c-b81c-02f90cbd5287")
        member_person_ids = {
            person_id
            for (person_id,) in (
                db.query(ServiceTeamMember.person_id)
                .filter(ServiceTeamMember.team_id == sales_team_id)
                .filter(ServiceTeamMember.is_active.is_(True))
                .all()
            )
            if person_id
        }
        if member_person_ids:
            team_agents = (
                db.query(CrmAgent)
                .filter(CrmAgent.person_id.in_(member_person_ids))
                .order_by(CrmAgent.created_at.desc())
                .limit(500)
                .all()
            )
            by_id = {str(agent.id): agent for agent in (agents or [])}
            for agent in team_agents:
                by_id[str(agent.id)] = agent
            agents = list(by_id.values())
    except Exception:
        logger.debug("Failed to include Sales service team members in lead owner agent options.", exc_info=True)
    agent_labels = crm_service.get_agent_labels(db, agents)
    return {
        "contacts": contacts,
        "people": people,
        "pipelines": pipelines,
        "stages": stages,
        "agents": agents,
        "agent_labels": agent_labels,
    }


def _load_pipeline_stages_for_pipeline(db: Session, pipeline_id: str | None) -> list[PipelineStage]:
    if not pipeline_id:
        return []
    try:
        pipeline_uuid = coerce_uuid(pipeline_id)
    except Exception:
        return []
    return (
        db.query(PipelineStage)
        .filter(PipelineStage.pipeline_id == pipeline_uuid)
        .filter(PipelineStage.is_active.is_(True))
        .order_by(PipelineStage.order_index.asc(), PipelineStage.name.asc())
        .all()
    )


def _format_project_summary(
    quote: Quote,
    lead: Lead | None,
    contact: Person | None,
    company_name: str | None,
    support_email: str | None = None,
) -> str:
    contact_name = None
    if contact:
        contact_name = (
            contact.display_name
            or " ".join(part for part in [contact.first_name, contact.last_name] if part).strip()
            or contact.email
            or contact.phone
        )
    quote_label = (
        quote.metadata_.get("quote_name")
        if isinstance(quote.metadata_, dict) and quote.metadata_.get("quote_name")
        else None
    )
    project_type = (
        quote.metadata_.get("project_type")
        if isinstance(quote.metadata_, dict) and quote.metadata_.get("project_type")
        else None
    )
    if not project_type:
        project_type = quote_label or (lead.title if lead and lead.title else None)
    if isinstance(project_type, str):
        project_type = project_type.replace("_", " ").strip()
    if not project_type:
        project_type = f"Project {str(quote.id)[:8].upper()}"

    expiry_label = "Not specified"
    if quote.expires_at:
        expiry_label = quote.expires_at.strftime("%b %d, %Y")

    lines: list[str] = [
        "Subject: Installation Quote",
        "",
        f"Dear {contact_name or 'Customer'},",
        "",
        f"Please find the requested quotation for {project_type} attached for your review.",
        "",
        f"The document contains a breakdown for your order. This quote is valid until {expiry_label}.",
        "",
        f"Should you have any questions or wish to proceed, please contact us on {support_email}."
        if support_email
        else "Should you have any questions or wish to proceed, please do not hesitate to reach out.",
        "",
        "Best regards,",
        "",
        company_name or "Dotmac",
    ]
    return "\n".join(lines)


def _is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme in {"http", "https", "mailto", "tel"}:
        return True
    return parsed.scheme == ""


def _resolve_brand_logo_src(branding: dict, request: Request) -> str | None:
    logo_url = branding.get("logo_url") if isinstance(branding, dict) else None
    if not logo_url or not isinstance(logo_url, str):
        return None
    if logo_url.startswith("data:"):
        return logo_url

    marker = "uploads/branding/"
    idx = logo_url.find(marker)
    if idx >= 0:
        key = logo_url[idx:]
        try:
            from app.services.storage import storage

            data = storage.get(key)
            mime, _ = mimetypes.guess_type(key)
            encoded = base64.b64encode(data).decode("ascii")
            return f"data:{mime or 'image/png'};base64,{encoded}"
        except Exception:
            logger.debug("logo_inline_encoding_failed key=%s", key)

    if logo_url.startswith("/"):
        return urljoin(str(request.base_url), logo_url.lstrip("/"))
    return logo_url


def _build_quote_pdf_bytes(
    request: Request,
    quote: Quote,
    items: list,
    lead: Lead | None,
    contact: Person | None,
    quote_name: str | None,
    branding: dict | None,
) -> bytes:
    from app.web.templates import Jinja2Templates

    templates = Jinja2Templates(directory="templates")
    template = templates.get_template("admin/crm/quote_pdf.html")
    branding_payload = dict(branding or {})
    branding_payload["logo_src"] = _resolve_brand_logo_src(branding_payload, request)
    html = template.render(
        {
            "request": request,
            "quote": quote,
            "items": items,
            "lead": lead,
            "contact": contact,
            "quote_name": quote_name or "",
            "branding": branding_payload,
        }
    )
    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="WeasyPrint is not installed on the server. Install it to generate PDFs.",
        ) from exc
    _ensure_pydyf_compat()
    html_doc = HTML(string=html, base_url=str(request.base_url))
    return html_doc.write_pdf()


def _load_crm_agent_team_options(db: Session) -> dict:
    return crm_service.get_agent_team_options(db)


def _get_current_roles(request: Request) -> list[str]:
    auth = getattr(request.state, "auth", None)
    if isinstance(auth, dict):
        roles = auth.get("roles") or []
        if isinstance(roles, list):
            return [str(role) for role in roles]
    return []


def _get_current_scopes(request: Request) -> list[str]:
    auth = getattr(request.state, "auth", None)
    if isinstance(auth, dict):
        scopes = auth.get("scopes") or []
        if isinstance(scopes, list):
            return [str(scope) for scope in scopes]
    return []


def _is_admin_request(request: Request) -> bool:
    return any(role.strip().lower() == "admin" for role in _get_current_roles(request))


def _is_manager_request(request: Request) -> bool:
    return any(role.strip().lower() == "manager" for role in _get_current_roles(request))


def _can_view_live_location_map(request: Request) -> bool:
    if _is_admin_request(request) or _is_manager_request(request):
        return True
    scopes = {scope.strip().lower() for scope in _get_current_scopes(request)}
    return "crm:location:read" in scopes


def _require_admin_role(request: Request) -> None:
    if _is_admin_request(request):
        return
    raise HTTPException(status_code=403, detail="Only admin users can delete quotes.")


def _can_write_sales(request: Request) -> bool:
    if _is_admin_request(request):
        return True
    scopes = set(_get_current_scopes(request))
    return bool({"crm:lead:write", "crm:lead", "crm"} & scopes)
