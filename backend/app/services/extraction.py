import logging
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import CommodityGroup
from app.schemas.extraction import ExtractedVendorData, ExtractionResponse, OrderLineData

logger = logging.getLogger(__name__)

EXTRACTION_FAILED_MESSAGE = (
    "We couldn't read this document automatically. Please fill in the request manually."
)

# The generated taxonomy names variants "<base> — <variant>" (e.g. "Backup
# Software — Cloud"). Classifying against the ~380 base groups keeps the prompt
# small (~4k instead of ~27k tokens) without losing any distinct category.
_VARIANT_SEPARATOR = " — "


# What the model must return, enforced by OpenAI structured outputs. Kept apart
# from the API schema (ExtractedVendorData): strict mode forbids defaults, and
# anything the document may not contain has to be nullable so a missing value
# is reported instead of failing the whole extraction. Fields are generated in
# order, so the customer and the evidence come before the vendor they rule out.
LineType = Literal["item", "shipping", "fee", "discount", "tax", "alternative", "header"]

# Line types that belong on the request; the rest are read but dropped.
_ORDERED_LINE_TYPES = {"item", "shipping", "fee", "discount"}


class _QuoteLine(BaseModel):
    line_type: LineType = Field(
        description=(
            "item = a product/service being bought; shipping/fee = a priced row for "
            "delivery or a fee; discount = a separate negative row; tax = a tax row; "
            "alternative = an optional/alternative offer not included in the quote's "
            "total; header = a section heading, subtotal or title row"
        )
    )
    position_description: str
    amount: float | None = Field(description="The Qty column value")
    unit: str | None
    unit_price: float | None = Field(description="Unit price as printed, before any discount")
    total_price: float | None = Field(
        description="The line total as printed (after any line discount)"
    )


class _Totals(BaseModel):
    items_subtotal: float | None = Field(description="Net total of the items, before shipping/tax")
    shipping: float | None = Field(description="Shipping/freight charged in the totals block")
    tax: float | None = Field(description="All sales tax combined")
    grand_total: float | None = Field(
        description="The final amount payable, including shipping, tax and fees"
    )


class _Quote(BaseModel):
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
    order_lines: list[_QuoteLine]
    totals: _Totals


class _Classification(BaseModel):
    commodity_group_name: str | None = Field(description="Name exactly as listed")
    commodity_group_id: int | None


EXTRACTION_SYSTEM_PROMPT = (
    "You extract structured data from vendor quotes (US business documents) for a "
    "procurement system. Read the whole document carefully; accuracy matters more than "
    "completeness — return null rather than guessing."
)

CLASSIFICATION_SYSTEM_PROMPT = (
    "You classify purchases into the commodity group used for accounting. Pick the "
    "single best group from the list you are given."
)


def _build_extraction_prompt(pdf_text: str) -> str:
    return f"""Extract the data from the vendor quote below. The text was extracted from a PDF with its layout preserved: blocks that are side by side on the page are side by side on a line, separated by wide gaps, and "--- Page N of M ---" marks page breaks. Logos are images and do not appear in the text.

WHO IS WHO
- First identify the customer: the company the quote is addressed to ("Prepared for", "Bill to", "Quote recipient", the address block near the top). The customer is the BUYER and is never the vendor.
- The vendor is the company that ISSUED the quote and is selling. Find it in the small sender line above the recipient address, the "Prepared by" / "Vendor" block, the closing signature, the footer (company name next to its bank details, EIN and address), or the sales rep's email domain. When blocks sit side by side on one line, keep each block's lines together — the company name in the same column as "Prepared by" or the tax ID is the vendor.
- If a brand name differs from the legal entity in the footer (e.g. brand "GreenCo" vs "GreenCo Holdings, Inc."), use the legal entity.
- The tax ID is the VENDOR's EIN / Tax ID / TIN. Customer numbers, quote numbers, seller's permits, routing and account numbers are not tax IDs. If the vendor's tax ID is not printed, return null.
- Department: only a team or department of the customer named on the quote (e.g. "Attn: Purchasing / Engineering"). A person's name or a company name is not a department — return null.

LINE ITEMS
- Return every row of the item table with a line_type.
- Alternatives are not part of the order: a row marked "Alternative", "Alt." or "(Alt.)" anywhere in or under it, every row that follows a heading like "Alternative: …" or "Alternative to the preceding item", and rows priced only as a unit price ("U.P.") without a total are line_type "alternative".
- Section headings, title rows without their own quantity and price, and subtotal/total rows are "header".
- amount is the number in the Qty column. Do not confuse it with item numbers, position numbers (1.1, 2) or tax codes.
- unit_price is the price per unit as printed; total_price is the line total as printed. If a discount column applies (e.g. -20%), total_price is the discounted total.
- Shipping, tax and fees that appear only in the totals block go into totals, not into order lines.
- For the unit, write out abbreviations in plain English ("ea." -> "pieces", "lic." -> "licenses"). Numbers are US-formatted ("1,438.00" = 1438.00); return plain numbers without currency symbols.

TOTALS
- grand_total is the final amount payable on the LAST page (often "Grand total", "Total (gross)", "Total (USD)"). "Carried forward" and "Page subtotal" are not totals.

TITLE
- A SHORT (2-5 words) description of the main items being purchased.

Document Text:
{pdf_text}"""


def _build_classification_prompt(quote: _Quote, groups_list: str) -> str:
    lines = "\n".join(
        f"- {line.position_description}"
        for line in quote.order_lines
        if line.line_type == "item"
    )
    # The vendor's name is left out on purpose: the group depends on what is
    # bought, and names like "… Greenscapes" pull the model off course.
    return f"""Classify this purchase into the SINGLE most appropriate commodity group, based on the PRIMARY nature and purpose of what is bought.

Purchase: {quote.title or ""}
Items:
{lines}

Guidance:
- Branded office decor/furniture -> Office Equipment (15)
- Traditional promotional items (brochures, giveaways, banners) -> Promotional Materials (43)
- Software -> Software (31); IT services -> IT Services (30); computer hardware -> Hardware (29)
- If genuinely uncertain, return null for both fields

Available Commodity Groups (ID | Category | Name):
{groups_list}"""


def _classify(
    client: OpenAI, quote: _Quote, groups: list[CommodityGroup]
) -> CommodityGroup | None:
    """Pick the commodity group in a second, small call. Failure is non-fatal."""
    base_groups = [g for g in groups if _VARIANT_SEPARATOR not in g.name]
    groups_list = "\n".join(f"{g.id} | {g.category} | {g.name}" for g in base_groups)
    try:
        response = client.chat.completions.parse(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
                {"role": "user", "content": _build_classification_prompt(quote, groups_list)},
            ],
            response_format=_Classification,
            max_completion_tokens=200,
            temperature=0.3,
        )
        choice = response.choices[0].message.parsed
    except Exception:  # noqa: BLE001 — the user can still pick the group by hand
        logger.exception("Error classifying commodity group")
        return None
    if choice is None:
        return None

    # Models copy names more reliably than numbers: trust the name, fall back to the id.
    by_name = {g.name.lower(): g for g in base_groups}
    by_id = {g.id: g for g in base_groups}
    group = by_name.get((choice.commodity_group_name or "").strip().lower()) or by_id.get(
        choice.commodity_group_id
    )
    if group is None and choice.commodity_group_id is not None:
        logger.warning(
            "Unknown commodity group returned: %s / %s",
            choice.commodity_group_id,
            choice.commodity_group_name,
        )
    return group


def _to_api_data(quote: _Quote, group: CommodityGroup | None) -> ExtractedVendorData:
    return ExtractedVendorData(
        title=quote.title,
        vendor_name=quote.vendor_name,
        vat_id=quote.vat_id,
        department=quote.department,
        order_lines=[
            OrderLineData(
                position_description=line.position_description,
                unit_price=line.unit_price or 0,
                amount=line.amount or 0,
                unit=line.unit or "",
                total_price=line.total_price or 0,
            )
            for line in quote.order_lines
            # Alternatives, section headers and tax rows aren't part of the order.
            if line.line_type in _ORDERED_LINE_TYPES
        ],
        total_cost=quote.totals.grand_total,
        commodity_group_id=group.id if group else None,
        commodity_group_name=group.name if group else None,
    )


def extract_vendor_data(pdf_text: str, db: Session) -> ExtractionResponse:
    """Extract vendor data from a quote, then classify its commodity group.

    Two structured-output calls: extraction sees only the document, so the
    quote isn't buried under the commodity taxonomy; classification sees only
    the extracted purchase and the base commodity groups. Values the document
    lacks come back as null and are listed in ``missing_fields`` rather than
    failing the extraction.
    """
    if not pdf_text or not pdf_text.strip():
        return ExtractionResponse(success=False, error="No text provided for extraction")

    if not settings.openai_api_key:
        return ExtractionResponse(
            success=False,
            error="OPENAI_API_KEY is not configured on the server",
        )

    commodity_groups = list(
        db.scalars(select(CommodityGroup).order_by(CommodityGroup.id))
    )
    if not commodity_groups:
        return ExtractionResponse(
            success=False,
            error="Failed to load commodity groups for classification",
        )

    client = OpenAI(api_key=settings.openai_api_key)
    try:
        response = client.chat.completions.parse(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": _build_extraction_prompt(pdf_text)},
            ],
            response_format=_Quote,
            max_completion_tokens=4000,
            temperature=0.3,
        )
        message = response.choices[0].message
        if message.parsed is None:
            logger.warning("Extraction returned no data (refusal: %s)", message.refusal)
            return ExtractionResponse(success=False, error=EXTRACTION_FAILED_MESSAGE)
    except Exception:  # noqa: BLE001 — API/network errors surface as a clean envelope
        logger.exception("Error extracting vendor data")
        return ExtractionResponse(success=False, error=EXTRACTION_FAILED_MESSAGE)

    quote = message.parsed
    extracted = _to_api_data(quote, _classify(client, quote, commodity_groups))

    # Report what the document didn't give us so the UI can ask for it.
    missing_fields: list[str] = []
    if not (extracted.title or "").strip():
        missing_fields.append("Title/Short Description")
    if not (extracted.vendor_name or "").strip():
        missing_fields.append("Vendor Name")
    if not (extracted.vat_id or "").strip():
        missing_fields.append("Tax ID")
    if not (extracted.department or "").strip():
        missing_fields.append("Department")
    if not extracted.order_lines:
        missing_fields.append("Order Lines")
    if not extracted.total_cost:
        missing_fields.append("Total Cost")
    if extracted.commodity_group_id is None:
        missing_fields.append("Commodity Group")

    return ExtractionResponse(
        success=True,
        data=extracted,
        missing_fields=missing_fields or None,
    )
