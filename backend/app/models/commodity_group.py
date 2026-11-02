import uuid

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin


class CommodityGroup(Base, CreatedAtMixin):
    """Commodity groups: the shared taxonomy, plus groups one organization added.

    ``organization_id`` is NULL for the shared groups every tenant sees; set,
    the group belongs to that organization only (e.g. Acme's "Office Supplies").
    """

    __tablename__ = "commodity_groups"

    # IDs are fixed (1-50) reference values, not auto-generated.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
