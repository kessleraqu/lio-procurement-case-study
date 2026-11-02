import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    article_number: str
    supplier_id: uuid.UUID
    supplier_name: str | None = None
    description: str
    unit_price: Decimal
    currency: str
    unit: str
    quantity: Decimal
    created_at: datetime


class ArticlePage(BaseModel):
    """A page of articles plus the totals the UI needs to render pagination."""

    items: list[ArticleOut]
    total: int
    limit: int
    offset: int


# --- Negotiated-price suggestions for request lines (camelCase in, like the
# request forms; snake_case out, like the other read models) -----------------

class SuggestionLineIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    description: str
    unit_price: float | None = Field(default=None, alias="unitPrice")
    amount: float | None = None
    unit: str | None = None


class SuggestionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    lines: list[SuggestionLineIn] = Field(max_length=50)
    # The request's vendor, to find prices negotiated with that very supplier.
    vendor_name: str | None = Field(default=None, alias="vendorName")
    vendor_tax_id: str | None = Field(default=None, alias="vendorTaxId")


class ArticleSuggestion(BaseModel):
    article_id: uuid.UUID
    article_number: str
    description: str
    supplier_name: str
    unit_price: float
    unit: str
    quantity: float  # catalog quantity the price was negotiated for
    # The model's view: "same" product or "comparable" (same kind, other
    # brand/spec). Used to drop substitutes when the exact product exists; the
    # requester is always asked to review, since the model can be wrong.
    match: Literal["same", "comparable"]
    # The article is negotiated with the request's own vendor (the main case:
    # the quote should use this price), not with another supplier.
    same_vendor: bool = False
    # Priced in the same unit as the line, so the two prices can be compared.
    same_unit: bool = False

class SuggestionResponse(BaseModel):
    # One list per input line, in order; empty = nothing in the catalog.
    suggestions: list[list[ArticleSuggestion]]
