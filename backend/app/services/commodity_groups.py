import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import CommodityGroup

# Organization groups get codes from here up, clear of the shared taxonomy's
# fixed codes, so a code always means the same group.
ORG_GROUP_ID_START = 100_000


def visible_groups_filter(organization_id: uuid.UUID | None):
    """Shared groups plus the organization's own (none of another tenant's)."""
    if organization_id is None:
        return CommodityGroup.organization_id.is_(None)
    return or_(
        CommodityGroup.organization_id.is_(None),
        CommodityGroup.organization_id == organization_id,
    )


def visible_groups(db: Session, organization_id: uuid.UUID | None) -> list[CommodityGroup]:
    return list(
        db.scalars(
            select(CommodityGroup)
            .where(visible_groups_filter(organization_id))
            .order_by(CommodityGroup.id)
        )
    )


def get_visible_group(
    db: Session, group_id: int, organization_id: uuid.UUID | None
) -> CommodityGroup | None:
    return db.scalar(
        select(CommodityGroup).where(
            CommodityGroup.id == group_id, visible_groups_filter(organization_id)
        )
    )


def next_org_group_id(db: Session) -> int:
    highest = db.scalar(select(func.max(CommodityGroup.id))) or 0
    return max(highest, ORG_GROUP_ID_START - 1) + 1
