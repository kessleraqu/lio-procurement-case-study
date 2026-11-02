import logging
import uuid
from dataclasses import dataclass

from openai import OpenAI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import CommodityGroup, Organization
from app.schemas.extraction import (
    ExtractedVendorData,
    ExtractionResponse,
    ExtractionWarning,
    OrderLineData,
)
from app.schemas.organizations import CommodityRule
from app.services.commodity_groups import visible_groups
from app.services.quote_checks import (
    Issue,
    additional_charges,
    find_issues,
    normalize_tax_id,
)
from app.services.quote_model import Quote, QuoteLine

logger = logging.getLogger(__name__)

EXTRACTION_FAILED_MESSAGE = (
    "We couldn't read this document automatically. Please fill in the request manually."
)

# The generated taxonomy names variants "<base> — <variant>" (e.g. "Backup
# Software — Cloud"). Classifying against the ~380 base groups keeps the prompt
# small (~4k instead of ~27k tokens) without losing any distinct category.
_VARIANT_SEPARATOR = " — "


class _LineRule(BaseModel):
    line: int
    rule: str | None = Field(description="The booking rule covering this line, copied exactly; else null")


class _Classification(BaseModel):
    # Asked first: which of the organization's booking rules covers each item line?
    line_rules: list[_LineRule]
    commodity_group_name: str | None = Field(description="Name exactly as listed")
    commodity_group_id: int | None


# A booking covering at least this share of a quote's item value, other than the
# one the request is booked under, is worth telling the requester about.
MIXED_BOOKING_SHARE = 0.2


@dataclass(frozen=True)
class _GroupChoice:
    group: CommodityGroup | None
    # Set when one of the organization's booking rules decided the group.
    rule: CommodityRule | None = None
    # (label, group name, value, share) per booking when the quote is mixed.
    mixed: tuple[tuple[str, str, float, float], ...] = ()


EXTRACTION_SYSTEM_PROMPT = (
    "You extract structured data from vendor quotes (US business documents) for a "
    "procurement system. Read the whole document carefully; accuracy matters more than "
    "completeness — return null rather than guessing. "
    "For alternative lines, position_description must be a standalone product/variant "
    "description, not a verbatim transcription of cross-references. Replace references "
    "to previous items or document item numbers with the referenced product and the "
    "alternative's changes. Never leave 'as previous item', 'as above' or references "
    "like 'item 1.1' in an alternative description. Use only document-supported details; "
    "if the reference is unclear, describe only the explicitly stated variant. "
    "Do not replace a numbered reference with another reference such as 'as described "
    "in the original item': state the inherited feature directly or omit a redundant "
    "reference. The reader must not need another item's text to understand the alternative. "
    "Preserve exactly which component the alternative changes: a change to one part "
    "must not be described as a change to the whole product. Retain the alternative's "
    "explicit modifications in full rather than summarizing them; only resolve their references."
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
- Rewrite each alternative's position_description as a self-contained product description. Resolve all cross-references using the original item, even across pages, and retain the alternative's explicit changes in full without retaining replaced details. Example: original "Office chair with armrests", alternative "As item 2.1, but without armrests" → "Office chair without armrests". Do not copy document item references into the description. This changes only the description, not the line_type, quantity or prices.
- Section headings, title rows without their own quantity and price, and subtotal/total rows are "header".
- amount is the number in the Qty column. Do not confuse it with item numbers, position numbers (1.1, 2) or tax codes.
- unit_price is the price per unit as printed; total_price is the line total as printed. If a discount column applies (e.g. -20%), total_price is the discounted total.
- Shipping, tax and fees that appear only in the totals block go into totals, not into order lines.
- For the unit, write out abbreviations in plain English ("ea." -> "pieces", "lic." -> "licenses"). Numbers are US-formatted ("1,438.00" = 1438.00); return plain numbers without currency symbols.

ALTERNATIVE DESCRIPTION EXAMPLE
Original item 2.1: "Sign with a red background and white acrylic lettering."
Alternative: "As item 2.1, but a blue background. Lettering as described in item 2.1."
Return position_description: "Sign with a blue background and white acrylic lettering."
The inherited lettering is named directly, not replaced with "lettering as described in the original item". Apply this same reference resolution to the actual document below.

TOTALS
- grand_total is the final amount payable on the LAST page (often "Grand total", "Total (gross)", "Total (USD)"). "Carried forward" and "Page subtotal" are not totals.
- tax is the sum of ALL tax rows; quotes often tax items and shipping on separate rows.

TITLE
- A SHORT (2-5 words) description of the main items being purchased.

Document Text:
{pdf_text}"""


def _item_lines(quote: Quote) -> list[QuoteLine]:
    return [line for line in quote.order_lines if line.line_type == "item"]


def _build_classification_prompt(
    quote: Quote, groups_list: str, rules: list[CommodityRule]
) -> str:
    lines = "\n".join(
        f"{i}: {line.position_description}" for i, line in enumerate(_item_lines(quote))
    )
    # The vendor's name is left out on purpose: the group depends on what is
    # bought, and names like "… Greenscapes" pull the model off course.
    rule_section = ""
    if rules:
        rule_list = "\n".join(f'- "{rule.applies_to}"' for rule in rules)
        rule_section = f"""
This company has booking rules for specific kinds of purchases:
{rule_list}
For EACH item line above, say which rule covers it (also under other names, brands or model numbers, e.g. "zip ties" are cable ties) by copying the rule exactly, or null if none does.
"""
    return f"""Classify this purchase into the SINGLE most appropriate commodity group, based on the PRIMARY nature and purpose of what is bought.

Purchase: {quote.title or ""}
Item lines:
{lines}
{rule_section}
Guidance:
- Branded office decor/furniture -> Office Equipment (15)
- Traditional promotional items (brochures, giveaways, banners) -> Promotional Materials (43)
- Software -> Software (31); IT services -> IT Services (30); computer hardware -> Hardware (29)
- If genuinely uncertain, return null for both fields

Available Commodity Groups (ID | Category | Name):
{groups_list}"""


def _classify(
    client: OpenAI,
    quote: Quote,
    groups: list[CommodityGroup],
    rules: list[CommodityRule],
) -> _GroupChoice:
    """Pick one commodity group for the whole request. Failure is non-fatal.

    The model picks a general group and says which booking rule (if any)
    covers each item line. Code decides: the item value is summed per booking
    (a rule's group, or the general group), and the largest share wins — a
    rule only within its ``max_total`` (checked against the grand total). If
    another booking also covers a large share, that's reported, not acted on.
    """
    base_groups = [g for g in groups if _VARIANT_SEPARATOR not in g.name]
    groups_list = "\n".join(f"{g.id} | {g.category} | {g.name}" for g in base_groups)
    try:
        response = client.chat.completions.parse(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _build_classification_prompt(quote, groups_list, rules),
                },
            ],
            response_format=_Classification,
            max_completion_tokens=500,
            temperature=settings.openai_temperature,
            seed=settings.openai_seed,
        )
        choice = response.choices[0].message.parsed
    except Exception:  # noqa: BLE001 — the user can still pick the group by hand
        logger.exception("Error classifying commodity group")
        return _GroupChoice(None)
    if choice is None:
        return _GroupChoice(None)

    # Models copy names more reliably than numbers: trust the name, fall back to the id.
    by_name = {g.name.lower(): g for g in base_groups}
    by_id = {g.id: g for g in base_groups}
    general = by_name.get((choice.commodity_group_name or "").strip().lower()) or by_id.get(
        choice.commodity_group_id
    )
    if general is None and choice.commodity_group_id is not None:
        logger.warning(
            "Unknown commodity group returned: %s / %s",
            choice.commodity_group_id,
            choice.commodity_group_name,
        )

    # Which rule applies to each item line (unknown rules, missing groups and
    # rules above their limit fall back to the general group).
    groups_by_id = {g.id: g for g in groups}
    rules_by_text = {r.applies_to.strip().lower(): r for r in rules}
    total = quote.totals.grand_total
    items = _item_lines(quote)
    line_rule: dict[int, CommodityRule] = {}
    for tag in choice.line_rules:
        rule = rules_by_text.get((tag.rule or "").strip().lower())
        if (
            rule
            and 0 <= tag.line < len(items)
            and rule.commodity_group_id in groups_by_id
            and (rule.max_total is None or (total is not None and total <= rule.max_total))
        ):
            line_rule[tag.line] = rule

    # Value per booking; without prices, every line counts the same.
    use_value = any((line.total_price or 0) > 0 for line in items)
    buckets: dict[str | None, float] = {}
    for i, line in enumerate(items):
        key = line_rule[i].applies_to if i in line_rule else None
        buckets[key] = buckets.get(key, 0) + ((line.total_price or 0) if use_value else 1)
    if not buckets:
        return _GroupChoice(general)

    winner = max(buckets, key=lambda k: buckets[k])
    rule = next((r for r in line_rule.values() if r.applies_to == winner), None)
    group = groups_by_id[rule.commodity_group_id] if rule else general

    grand = sum(buckets.values()) or 1
    mixed: tuple = ()
    if any(k != winner and v / grand >= MIXED_BOOKING_SHARE for k, v in buckets.items()):
        def label(key):
            if key is None:
                return ("other items", general.name if general else "no group")
            r = next(r for r in line_rule.values() if r.applies_to == key)
            return (key, groups_by_id[r.commodity_group_id].name)
        mixed = tuple(
            (*label(k), v if use_value else 0.0, v / grand)
            for k, v in sorted(buckets.items(), key=lambda kv: -kv[1])
        )
    return _GroupChoice(group, rule, mixed)


def _to_line_data(line: QuoteLine) -> OrderLineData:
    total = line.total_price
    if total is None and line.unit_price is not None:
        # Alternatives are often priced per unit only ("U.P."), without a total.
        adjustment = line.price_adjustment_percent or 0
        total = round((line.amount or 1) * line.unit_price * (1 + adjustment / 100), 2)
    return OrderLineData(
        position_description=line.position_description,
        unit_price=line.unit_price or 0,
        amount=line.amount or 0,
        unit=line.unit or "",
        total_price=total or 0,
        price_adjustment_percent=line.price_adjustment_percent,
    )


def _to_api_data(quote: Quote, choice: _GroupChoice) -> ExtractedVendorData:
    group = choice.group
    return ExtractedVendorData(
        title=quote.title,
        vendor_name=quote.vendor_name,
        vat_id=normalize_tax_id(quote.vat_id),
        department=quote.department,
        # Alternatives, section headers and tax rows aren't part of the order.
        order_lines=[_to_line_data(line) for line in quote.ordered_lines],
        total_cost=quote.totals.grand_total,
        additional_charges=additional_charges(quote),
        # Priced alternatives are offered to the requester instead of dropped
        # (an "Alternative: …" heading without a price is not an option).
        alternatives=[
            _to_line_data(line)
            for line in quote.order_lines
            if line.line_type == "alternative"
            and (line.unit_price is not None or line.total_price is not None)
        ],
        commodity_group_id=group.id if group else None,
        commodity_group_name=group.name if group else None,
        commodity_rule=choice.rule.applies_to if choice.rule else None,
    )


def _read_quote(client: OpenAI, messages: list[dict]) -> tuple[Quote | None, str | None]:
    """One structured-output extraction call. Returns (quote, raw JSON reply)."""
    response = client.chat.completions.parse(
        model=settings.openai_model,
        messages=messages,
        response_format=Quote,
        max_completion_tokens=4000,
        temperature=settings.openai_temperature,
        seed=settings.openai_seed,
    )
    message = response.choices[0].message
    if message.parsed is None:
        logger.warning("Extraction returned no data (refusal: %s)", message.refusal)
    return message.parsed, message.content


def _extract_with_checks(client: OpenAI, pdf_text: str) -> tuple[Quote | None, list[Issue]]:
    """Extract, check the arithmetic, and give the model one chance to fix what doesn't add up.

    Returns the more consistent of the two readings and its remaining issues.
    """
    messages = [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": _build_extraction_prompt(pdf_text)},
    ]
    quote, raw = _read_quote(client, messages)
    if quote is None:
        return None, []
    issues = find_issues(quote)
    if not issues:
        return quote, []

    logger.info("Extraction inconsistent, retrying once: %s", [i.message for i in issues])
    feedback = (
        "Your extraction is inconsistent with the document:\n"
        + "\n".join(f"- {issue.message}" for issue in issues)
        + "\nRe-read the document and return the corrected extraction. If the document "
        "itself prints these numbers, keep them as printed."
    )
    try:
        retry, _ = _read_quote(
            client,
            [*messages, {"role": "assistant", "content": raw}, {"role": "user", "content": feedback}],
        )
    except Exception:  # noqa: BLE001 — keep the first reading if the retry fails
        logger.exception("Extraction retry failed")
        return quote, issues
    if retry is None:
        return quote, issues
    retry_issues = find_issues(retry)
    return (retry, retry_issues) if len(retry_issues) <= len(issues) else (quote, issues)


def _mixed_booking_warnings(choice: _GroupChoice) -> list[ExtractionWarning]:
    """A notice (no action) when a quote's items would be booked differently."""
    if not choice.mixed or choice.group is None:
        return []
    parts = "; ".join(
        f"{label} → {group}" + (f" (${value:,.2f}, {share:.0%})" if value else f" ({share:.0%})")
        for label, group, value, share in choice.mixed
    )
    return [ExtractionWarning(
        code="mixed_booking",
        message=(
            f"This quote mixes items that are booked differently: {parts}. The request is "
            f"booked under {choice.group.name} (the largest share); split it if "
            "accounting needs separate bookings."
        ),
    )]


def extract_vendor_data(
    pdf_text: str, db: Session, organization_id: uuid.UUID | None = None
) -> ExtractionResponse:
    """Extract vendor data from a quote, then classify its commodity group.

    Extraction sees only the document, so the quote isn't buried under the
    commodity taxonomy; its result is checked for internal consistency (line
    and grand totals add up, vendor isn't the customer) and retried once with
    the problems spelled out. Classification is a second, small call over the
    extracted purchase and the base commodity groups. Values the document lacks
    come back as null and are listed in ``missing_fields``; inconsistencies that
    survive the retry are returned as ``warnings`` for the user to check.
    """
    if not pdf_text or not pdf_text.strip():
        return ExtractionResponse(success=False, error="No text provided for extraction")

    if not settings.openai_api_key:
        return ExtractionResponse(
            success=False,
            error="OPENAI_API_KEY is not configured on the server",
        )

    # Shared groups plus the organization's own, and its booking rules.
    commodity_groups = visible_groups(db, organization_id)
    org = db.get(Organization, organization_id) if organization_id else None
    rules = [
        CommodityRule.model_validate(rule)
        for rule in ((org.settings or {}).get("commodity_rules", []) if org else [])
    ]
    if not commodity_groups:
        return ExtractionResponse(
            success=False,
            error="Failed to load commodity groups for classification",
        )

    client = OpenAI(api_key=settings.openai_api_key)
    try:
        quote, issues = _extract_with_checks(client, pdf_text)
    except Exception:  # noqa: BLE001 — API/network errors surface as a clean envelope
        logger.exception("Error extracting vendor data")
        return ExtractionResponse(success=False, error=EXTRACTION_FAILED_MESSAGE)
    if quote is None:
        return ExtractionResponse(success=False, error=EXTRACTION_FAILED_MESSAGE)

    choice = _classify(client, quote, commodity_groups, rules)
    extracted = _to_api_data(quote, choice)

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
        warnings=[
            ExtractionWarning(code=i.code, message=i.message)
            for i in issues
            if i.affects_request
        ] + _mixed_booking_warnings(choice) or None,
    )
