"""Service for contract signatures (click-to-sign workflow)."""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.contracts import ContractSignature
from app.models.legal import LegalDocument, LegalDocumentType
from app.schemas.contracts import ContractSignatureCreate
from app.services.common import coerce_uuid


class ContractSignatures:
    """Service for managing contract signatures."""

    @staticmethod
    def create(db: Session, payload: ContractSignatureCreate) -> ContractSignature:
        """Create a new contract signature record.

        Args:
            db: Database session
            payload: Contract signature data

        Returns:
            Created ContractSignature

        Raises:
            HTTPException: If account not found
        """
        # Account validation removed - SubscriberAccount no longer exists

        data = payload.model_dump()
        if not data.get("signed_at"):
            data["signed_at"] = datetime.now(UTC)

        signature = ContractSignature(**data)
        db.add(signature)
        db.commit()
        db.refresh(signature)
        return signature

    @staticmethod
    def get(db: Session, signature_id: str) -> ContractSignature:
        """Get a contract signature by ID.

        Args:
            db: Database session
            signature_id: Signature ID

        Returns:
            ContractSignature

        Raises:
            HTTPException: If signature not found
        """
        signature = db.get(ContractSignature, coerce_uuid(signature_id))
        if not signature:
            raise HTTPException(status_code=404, detail="Contract signature not found")
        return signature

    @staticmethod
    @staticmethod
    def list_for_account(db: Session, account_id: str, limit: int = 100, offset: int = 0) -> list[ContractSignature]:
        """List all contract signatures for an account.

        Args:
            db: Database session
            account_id: Account ID
            limit: Max results to return
            offset: Number of results to skip

        Returns:
            List of ContractSignature objects
        """
        return (
            db.query(ContractSignature)
            .filter(ContractSignature.account_id == coerce_uuid(account_id))
            .filter(ContractSignature.is_active.is_(True))
            .order_by(ContractSignature.signed_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_contract_template(
        db: Session, document_type: LegalDocumentType = LegalDocumentType.terms_of_service
    ) -> LegalDocument | None:
        """Get the current published contract template.

        Args:
            db: Database session
            document_type: Type of legal document to retrieve

        Returns:
            LegalDocument or None if not found
        """
        return (
            db.query(LegalDocument)
            .filter(LegalDocument.document_type == document_type)
            .filter(LegalDocument.is_current.is_(True))
            .filter(LegalDocument.is_published.is_(True))
            .first()
        )


contract_signatures = ContractSignatures()
