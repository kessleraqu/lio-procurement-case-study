"""What the model must return when reading a quote (OpenAI structured outputs).

Kept apart from the API schema (``ExtractedVendorData``): strict mode forbids
defaults, and anything a document may not contain has to be nullable so a
missing value is reported instead of failing the whole extraction. Fields are
generated in order, so the customer and the evidence come before the vendor
they rule out.
"""
from typing import Literal

from pydantic import BaseModel, Field

LineType = Literal["item", "shipping", "fee", "discount", "tax", "alternative", "header"]

# Line types that belong on the request; the rest are read but dropped.
ORDERED_LINE_TYPES = {"item", "shipping", "fee", "discount"}


class QuoteLine(BaseModel):
    line_type: LineType = Field(
        description=(
            "item = a product/service being bought; shipping/fee = a priced row for "
            "delivery or a fee; discount = a separate negative row; tax = a tax row; "
            "alternative = an optional/alternative offer not included in the quote's "
            "total; header = a section heading, subtotal or title row"
        )
    )
    position_description: str = Field(
        description=(
            "Product/service description. For alternatives, write a standalone description: "
            "replace every reference to another item with that item's actual product or "
            "feature, then retain the alternative's explicit changes. Never write 'as "
            "previous item', 'item 1.1', 'as described above' or 'as described in the "
            "original item'. Name inherited features directly, including lettering or "
            "other components when mentioned. Preserve which specific component changes."
        )
    )
    amount: float | None = Field(description="The Qty column value")
    unit: str | None
    unit_price: float | None = Field(description="Unit price as printed, before any discount")
    price_adjustment_percent: float | None = Field(
        description=(
            "Percent discount/surcharge shown on the row, signed as printed: -20 for a "
            "20% discount, 5 for a 5% surcharge; null if none"
        )
    )
    total_price: float | None = Field(
        description="The line total as printed (after any line discount)"
    )


class QuoteTotals(BaseModel):
    items_subtotal: float | None = Field(description="Net total of the items, before shipping/tax")
    shipping: float | None = Field(description="Shipping/freight charged in the totals block")
    fees: float | None = Field(
        description="Other fees or surcharges in the totals block (e.g. recycling fee), combined"
    )
    tax: float | None = Field(
        description="The sum of ALL tax rows (e.g. tax on items plus tax on shipping)"
    )
    grand_total: float | None = Field(
        description="The final amount payable, including shipping, fees and tax"
    )


class Quote(BaseModel):
    title: str | None
    customer_name: str | None = Field(
        description="Company the quote is addressed to ('Prepared for', 'Bill to', recipient)"
    )
    vendor_evidence: str = Field(
        description=(
            "Copy the text that names the company that ISSUED the quote: the sender line, "
            "'Prepared by' block, signature or footer with its bank details / EIN"
        )
    )
    vendor_name: str | None = Field(
        description="Legal name of the issuing company, taken from the evidence; never the customer"
    )
    vat_id: str | None = Field(
        description="The VENDOR's tax ID (EIN/TIN/Tax ID) as printed; null if the document has none"
    )
    department: str | None = Field(
        description=(
            "A team/department of the customer named on the quote (e.g. 'Attn: Purchasing'); "
            "null if none — never a company or a person's name"
        )
    )
    order_lines: list[QuoteLine]
    totals: QuoteTotals

    @property
    def ordered_lines(self) -> list[QuoteLine]:
        """The lines that are actually being ordered (no alternatives/headers/tax)."""
        return [line for line in self.order_lines if line.line_type in ORDERED_LINE_TYPES]
