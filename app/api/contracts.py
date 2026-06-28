"""Contracts (click-to-sign) JSON API — thin wrappers over app.services.contracts.

The ContractSignatures service was fully built but had no web or API surface;
this exposes the signing flow (template + record + read) for a customer portal
or external signing client.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.contracts import ContractSignatureCreate, ContractSignatureRead
from app.services.contracts import contract_signatures

router = APIRouter(prefix="/contracts", tags=["contracts"])


@router.get("/template")
def get_contract_template(document_type: str = "terms_of_service", db: Session = Depends(get_db)):
    """The current published contract template for the given legal-document type."""
    from app.models.legal import LegalDocumentType

    try:
        doc_type = LegalDocumentType(document_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown document_type '{document_type}'") from exc
    template = contract_signatures.get_contract_template(db, doc_type)
    if template is None:
        raise HTTPException(status_code=404, detail="No published contract template")
    return template


@router.post("/signatures", response_model=ContractSignatureRead, status_code=status.HTTP_201_CREATED)
def create_signature(payload: ContractSignatureCreate, db: Session = Depends(get_db)):
    return contract_signatures.create(db, payload)


@router.get("/signatures/{signature_id}", response_model=ContractSignatureRead)
def get_signature(signature_id: str, db: Session = Depends(get_db)):
    return contract_signatures.get(db, signature_id)


@router.get("/signatures", response_model=list[ContractSignatureRead])
def list_signatures(
    account_id: str = Query(...),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return contract_signatures.list_for_account(db, account_id, limit, offset)
