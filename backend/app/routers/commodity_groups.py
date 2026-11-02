from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import get_optional_membership, require_org_admin
from app.db.session import get_db
from app.models import CommodityGroup, Organization, OrganizationMember, ProcurementRequest
from app.schemas.requests import CommodityGroupIn, CommodityGroupOut
from app.services.commodity_groups import next_org_group_id, visible_groups

router = APIRouter(prefix="/commodity-groups", tags=["commodity-groups"])


@router.get("", response_model=list[CommodityGroupOut])
def list_commodity_groups(
    membership: OrganizationMember | None = Depends(get_optional_membership),
    db: Session = Depends(get_db),
) -> list[CommodityGroupOut]:
    """The shared groups plus the caller's organization's own groups."""
    org_id = membership.organization_id if membership else None
    return [CommodityGroupOut.model_validate(g) for g in visible_groups(db, org_id)]


def _check_name_free(db: Session, membership: OrganizationMember, body: CommodityGroupIn,
                     exclude_id: int | None = None) -> None:
    """Names must be unambiguous among the groups this organization sees."""
    clash = next(
        (g for g in visible_groups(db, membership.organization_id)
         if g.name.strip().lower() == body.name.strip().lower() and g.id != exclude_id),
        None,
    )
    if clash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A commodity group named “{clash.name}” already exists ({clash.id}).",
        )


def _own_group(db: Session, membership: OrganizationMember, group_id: int) -> CommodityGroup:
    group = db.get(CommodityGroup, group_id)
    if group is None or group.organization_id != membership.organization_id:
        # Shared groups and other tenants' groups can't be changed here.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Commodity group not found among your organization's groups")
    return group


@router.post("", response_model=CommodityGroupOut, status_code=status.HTTP_201_CREATED)
def create_commodity_group(
    body: CommodityGroupIn,
    membership: OrganizationMember = Depends(require_org_admin),
    db: Session = Depends(get_db),
) -> CommodityGroupOut:
    """Add a group only this organization sees (same structure as shared ones)."""
    _check_name_free(db, membership, body)
    group = CommodityGroup(
        id=next_org_group_id(db),
        category=body.category.strip(),
        name=body.name.strip(),
        organization_id=membership.organization_id,
    )
    db.add(group)
    db.commit()
    return CommodityGroupOut.model_validate(group)


@router.put("/{group_id}", response_model=CommodityGroupOut)
def update_commodity_group(
    group_id: int,
    body: CommodityGroupIn,
    membership: OrganizationMember = Depends(require_org_admin),
    db: Session = Depends(get_db),
) -> CommodityGroupOut:
    """Rename one of the organization's groups; its code (and bookings) stay."""
    group = _own_group(db, membership, group_id)
    _check_name_free(db, membership, body, exclude_id=group.id)
    group.category, group.name = body.category.strip(), body.name.strip()
    db.commit()
    return CommodityGroupOut.model_validate(group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_commodity_group(
    group_id: int,
    membership: OrganizationMember = Depends(require_org_admin),
    db: Session = Depends(get_db),
) -> None:
    """Delete one of the organization's groups — only if nothing uses it."""
    group = _own_group(db, membership, group_id)
    requests = db.scalar(
        select(func.count()).select_from(ProcurementRequest)
        .where(ProcurementRequest.commodity_group_id == group.id)
    ) or 0
    org = db.get(Organization, membership.organization_id)
    rules = sum(1 for r in (org.settings or {}).get("commodity_rules", [])
                if r.get("commodity_group_id") == group.id)
    if requests or rules:
        used = " and ".join(
            f"{n} {word}{'s' if n != 1 else ''}"
            for n, word in ((requests, "request"), (rules, "booking rule")) if n
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"“{group.name}” is used by {used}. Rename it instead, or reassign those first.",
        )
    db.delete(group)
    db.commit()
