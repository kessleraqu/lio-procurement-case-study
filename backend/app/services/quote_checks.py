"""Deterministic sanity checks on an extracted quote.

A quote is internally consistent by construction: line totals follow from
quantity × price, and the grand total from the lines plus shipping, fees and
tax. When the extraction breaks that arithmetic, something was misread (an
alternative counted as ordered, a line missed, a quantity confused with an
item number). These checks find that without knowing anything about a
particular vendor's layout.
"""
import re
from dataclasses import dataclass

from app.services.quote_model import Quote

_LEGAL_SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|lp|gmbh)\b"
)


def _close(a: float, b: float) -> bool:
    """Equal up to rounding: a few cents, or 0.1% on large amounts."""
    return abs(a - b) <= max(0.05, abs(b) * 0.001)


def _company_key(name: str | None) -> str:
    normalized = _LEGAL_SUFFIXES.sub("", (name or "").lower())
    return re.sub(r"[^a-z0-9]", "", normalized)


def _money(value: float) -> str:
    return f"{value:,.2f}"


@dataclass(frozen=True)
class Issue:
    message: str
    # False for slips that only affect fields the request doesn't use (e.g. the
    # tax breakdown when the grand total itself was read correctly): worth a
    # retry, not worth alarming the user.
    affects_request: bool = True


def normalize_tax_id(tax_id: str | None) -> str | None:
    """Format a US EIN as ``XX-XXXXXXX``; leave anything else as printed."""
    if not tax_id or not tax_id.strip():
        return None
    digits = re.sub(r"\D", "", tax_id)
    if len(digits) == 9 and re.fullmatch(r"[\d\s-]+", tax_id.strip()):
        return f"{digits[:2]}-{digits[2:]}"
    return tax_id.strip()


def find_issues(quote: Quote) -> list[Issue]:
    """Inconsistencies in the extraction, phrased so the model (and a user) can act on them."""
    issues: list[Issue] = []

    vendor, customer = _company_key(quote.vendor_name), _company_key(quote.customer_name)
    if vendor and customer and (vendor in customer or customer in vendor):
        issues.append(Issue(
            f"The vendor ({quote.vendor_name!r}) is the same company as the customer "
            "the quote is addressed to; the vendor is the company that issued the quote."
        ))

    ordered = quote.ordered_lines
    if not ordered:
        issues.append(Issue("No ordered line items were found."))

    for line in ordered:
        if line.line_type == "discount" or None in (
            line.amount,
            line.unit_price,
            line.total_price,
        ):
            continue
        adjustment = line.price_adjustment_percent or 0
        expected = line.amount * line.unit_price * (1 + adjustment / 100)
        if not _close(expected, line.total_price):
            adjustment_note = f" {adjustment:+g}%" if adjustment else ""
            issues.append(Issue(
                f"Line {line.position_description[:60]!r}: {line.amount:g} × "
                f"{_money(line.unit_price)}{adjustment_note} = {_money(expected)}, "
                f"but its total is {_money(line.total_price)}."
            ))

    totals = quote.totals
    if totals.grand_total is None:
        issues.append(Issue("No grand total was found."))
    if not ordered:
        return issues

    # Two independent sums, so the message points at the actual mistake:
    # (a) lines vs the subtotal printed on the quote -> wrong/missing/extra lines,
    # (b) the totals block itself -> a missed tax, shipping or fee row.
    # Without a printed subtotal, fall back to lines vs the grand total.
    lines_sum = sum(line.total_price or 0 for line in ordered)
    items_sum = sum(line.total_price or 0 for line in ordered if line.line_type in
                    ("item", "discount"))
    subtotal = totals.items_subtotal
    if subtotal is not None:
        # The printed subtotal may or may not include shipping/fee lines.
        if not (_close(items_sum, subtotal) or _close(lines_sum, subtotal)):
            issues.append(Issue(
                f"The ordered lines add up to {_money(lines_sum)}, but the quote's items "
                f"subtotal is {_money(subtotal)} (difference {_money(lines_sum - subtotal)}). "
                "Check for alternative or optional items counted as ordered, missing "
                "lines, or misread amounts."
            ))
        base = subtotal
    else:
        base = lines_sum

    if totals.grand_total is not None:
        tax = totals.tax or 0
        # Shipping and fees may be listed as order lines *or* only in the totals
        # block, so accept either reading.
        candidates = [
            base + tax + shipping + fees
            for shipping in {0, totals.shipping or 0}
            for fees in {0, totals.fees or 0}
        ]
        if not any(_close(c, totals.grand_total) for c in candidates):
            gap = totals.grand_total - max(candidates)
            source = "items subtotal" if subtotal is not None else "ordered lines"
            issues.append(Issue(
                f"The {source} ({_money(base)}) plus shipping ({_money(totals.shipping or 0)}), "
                f"fees ({_money(totals.fees or 0)}) and tax ({_money(tax)}) does not add up to "
                f"the grand total {_money(totals.grand_total)} (difference {_money(gap)}). "
                "Check for a missed tax, shipping or fee row, or lines counted wrongly.",
                # With a printed subtotal the lines were checked above; this only
                # concerns the tax/shipping breakdown, which the request doesn't store.
                affects_request=subtotal is None,
            ))

    return issues
