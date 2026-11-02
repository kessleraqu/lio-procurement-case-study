from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_optional_membership
from app.db.session import get_db
from app.models import OrganizationMember
from app.schemas.extraction import ExtractionRequest, ExtractionResponse
from app.services.extraction import extract_vendor_data

router = APIRouter(prefix="/extraction", tags=["extraction"])


@router.post("", response_model=ExtractionResponse, response_model_by_alias=True)
def extract(
    payload: ExtractionRequest,
    membership: OrganizationMember | None = Depends(get_optional_membership),
    db: Session = Depends(get_db),
) -> ExtractionResponse:
    """Extract structured vendor data from PDF text via OpenAI.

    Returns ``{success, data?, error?, missingFields?}`` — the same envelope the
    original server action produced (errors are part of the payload, not HTTP
    errors, so the UI can show partial-extraction warnings).
    """
    # The organization's own commodity groups and booking rules apply.
    return extract_vendor_data(
        payload.text, db, membership.organization_id if membership else None
    )
