"""Data quality API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services.auth_dependencies import require_permission, require_user_auth
from app.services.data_quality.reports import (
    DOMAIN_REPORTERS,
    all_domains_health,
    domain_entity_list,
    domain_health_report,
)

router = APIRouter(prefix="/data-quality", tags=["data-quality"])


def _report_to_dict(r) -> dict:
    return {
        "domain": r.domain,
        "label": r.label,
        "entity_count": r.entity_count,
        "avg_quality": r.avg_quality,
        "avg_quality_pct": r.avg_pct(),
        "pct_above_threshold": r.pct_above_threshold,
        "pct_high_quality": r.pct_high_quality,
        "top_missing_fields": [{"field": f, "count": c} for f, c in r.top_missing_fields],
    }


def _entity_to_dict(e) -> dict:
    return {
        "entity_type": e.entity_type,
        "entity_id": e.entity_id,
        "score": e.score,
        "score_pct": e.pct(),
        "field_scores": e.field_scores,
        "missing_fields": e.missing_fields,
    }


@router.get(
    "/health",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def data_quality_health(
    domain: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """Data quality health report for all or a single domain."""
    capped = min(max(int(limit), 10), 500)
    if domain:
        if domain not in DOMAIN_REPORTERS:
            raise HTTPException(status_code=400, detail=f"Unknown domain: {domain}")
        report = domain_health_report(db, domain, limit=capped)
        return {"reports": [_report_to_dict(report)]}
    reports = all_domains_health(db, limit=capped)
    return {"reports": [_report_to_dict(r) for r in reports]}


@router.get(
    "/health/{domain}",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def data_quality_domain_detail(
    domain: str,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """Detailed data quality report for a single domain."""
    if domain not in DOMAIN_REPORTERS:
        raise HTTPException(status_code=400, detail=f"Unknown domain: {domain}")
    report = domain_health_report(db, domain, limit=min(max(int(limit), 10), 500))
    result = _report_to_dict(report)
    result["sample_worst"] = [_entity_to_dict(e) for e in report.sample_worst]
    return result


@router.get(
    "/entities/{domain}",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def data_quality_entity_list(
    domain: str,
    limit: int = 50,
    offset: int = 0,
    sort: str = "worst",
    db: Session = Depends(get_db),
):
    """Paginated entity quality list for drill-down views."""
    if domain not in DOMAIN_REPORTERS:
        raise HTTPException(status_code=400, detail=f"Unknown domain: {domain}")
    if sort not in ("worst", "best"):
        sort = "worst"
    results, total = domain_entity_list(
        db,
        domain,
        limit=min(max(int(limit), 1), 100),
        offset=max(int(offset), 0),
        sort=sort,
    )
    return {
        "domain": domain,
        "items": [_entity_to_dict(e) for e in results],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/score/{entity_type}/{entity_id}",
    dependencies=[Depends(require_user_auth)],
)
def data_quality_score_entity(
    entity_type: str,
    entity_id: str,
    db: Session = Depends(get_db),
):
    """Score a single entity on demand."""
    from app.services.data_quality.scoring import (
        score_campaign_quality,
        score_conversation_quality,
        score_project_quality,
        score_subscriber_quality,
        score_ticket_quality,
        score_vendor_quote_quality,
        score_work_order_quality,
    )

    scorers = {
        "ticket": score_ticket_quality,
        "conversation": score_conversation_quality,
        "project": score_project_quality,
        "work_order": score_work_order_quality,
        "campaign": score_campaign_quality,
        "vendor_quote": score_vendor_quote_quality,
        "subscriber": score_subscriber_quality,
    }
    scorer = scorers.get(entity_type)
    if not scorer:
        raise HTTPException(status_code=400, detail=f"Unknown entity type: {entity_type}")
    result = scorer(db, entity_id)
    return _entity_to_dict(result)
