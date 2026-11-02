import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

MemberRole = Literal["admin", "buyer", "requester"]

# Request fields an organization may optionally mark as required. (The other
# request fields are always required by the app.)
ConfigurableRequiredField = Literal["vat_id", "department"]


class CommodityRule(BaseModel):
    """An organization's booking rule: purchases of X are always booked under group Y."""

    model_config = ConfigDict(extra="forbid")

    # What the rule covers, in plain words (e.g. "printer toner").
    applies_to: str = Field(min_length=2, max_length=100)
    commodity_group_id: int
    # Only applies up to this request total (e.g. "small" subscriptions); None = always.
    max_total: float | None = Field(default=None, gt=0)


class OrganizationSettings(BaseModel):
    """Per-organization configuration. Stored as JSONB, validated here.

    This is the reference pattern for tenant config: keep the storage flexible,
    but validate the shape at the edge so it can't drift into a free-for-all.
    """

    model_config = ConfigDict(extra="forbid")

    required_fields: list[ConfigurableRequiredField] = Field(default_factory=list)
    # Booking rules that override the general commodity classification.
    commodity_rules: list[CommodityRule] = Field(default_factory=list)
    # Show admins an "AI suggested X, users chose Y" pattern once it happened
    # this many times — a hint that a booking rule may be missing.
    correction_alert_threshold: int = Field(default=3, ge=1, le=100)
    # Requests whose full total (incl. tax and shipping) is below this amount
    # need no buyer approval. None = every request needs approval.
    auto_approve_below: float | None = Field(default=None, gt=0)


class GroupRef(BaseModel):
    id: int
    category: str
    name: str


class CorrectionPattern(BaseModel):
    """Requesters repeatedly replaced the AI's commodity group with another one."""

    suggested: GroupRef
    chosen: GroupRef
    count: int
    examples: list[str]  # request titles, most recent first


class OrganizationCreate(BaseModel):
    name: str
    slug: str


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime
    updated_at: datetime


class MyOrganizationOut(OrganizationOut):
    role: MemberRole


class MemberProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str | None = None
    avatar_url: str | None = None


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID
    role: MemberRole
    created_at: datetime
    # Named to match the Supabase join key the frontend already renders.
    profiles: MemberProfileOut | None = None


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    email: str
    role: str
    invited_by: uuid.UUID
    expires_at: datetime
    accepted_at: datetime | None
    created_at: datetime


class MembersResponse(BaseModel):
    members: list[MemberOut]
    invites: list[InviteOut]


class InviteCreate(BaseModel):
    email: EmailStr
    role: MemberRole = "requester"


class InviteAccept(BaseModel):
    token: str
