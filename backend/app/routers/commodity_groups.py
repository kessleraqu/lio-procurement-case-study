from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_optional_membership
from app.db.session import get_db
from app.models import OrganizationMember
from app.schemas.requests import CommodityGroupOut
from app.services.commodity_groups import visible_groups

router = APIRouter(prefix="/commodity-groups", tags=["commodity-groups"])


@router.get("", response_model=list[CommodityGroupOut])
def list_commodity_groups(
    membership: OrganizationMember | None = Depends(get_optional_membership),
    db: Session = Depends(get_db),
) -> list[CommodityGroupOut]:
    """The shared groups plus the caller's organization's own groups."""
    org_id = membership.organization_id if membership else None
    return [CommodityGroupOut.model_validate(g) for g in visible_groups(db, org_id)]
