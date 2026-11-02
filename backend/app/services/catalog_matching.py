"""Suggest negotiated catalog articles for request lines.

The model only decides *which* catalog products are similar to each line —
wording on quotes rarely matches the catalog ("MacBook Air" vs "Business
laptop"). Everything else is code: only the requester's own organization's
articles, the quote's own vendor first, other suppliers cheapest first. No
savings are claimed: the form shows both prices and the requester reviews
whether the product is really equivalent.
"""
import logging
import re
import uuid
from collections import defaultdict
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.models import Article
from app.schemas.articles import ArticleSuggestion, SuggestionLineIn
from app.services.quote_checks import company_key

logger = logging.getLogger(__name__)

# Distinct catalog products sent to the model in one prompt (~12 tokens each).
# Larger catalogs are narrowed by a keyword pre-filter first.
MAX_PRODUCTS_IN_PROMPT = 600
MAX_PRODUCTS_PER_LINE = 2
MAX_SUPPLIERS_PER_PRODUCT = 2
# Plausibility: a negotiated price this many times cheaper/dearer than the
# quoted one is almost certainly a different product.
MAX_PRICE_RATIO = 10

_UNIT_ALIASES = {
    "pieces": "piece", "pcs": "piece", "pc": "piece", "ea": "piece", "each": "piece",
    "unit": "piece", "units": "piece", "item": "piece", "items": "piece",
    "licenses": "license", "licences": "license", "licence": "license", "seat": "license",
    "seats": "license", "months": "month", "hours": "hour", "hrs": "hour", "days": "day",
    "packs": "pack", "sets": "set", "feet": "ft", "foot": "ft",
    "squarefeet": "sqft", "squarefoot": "sqft", "sqfeet": "sqft", "sf": "sqft", "ft2": "sqft",
    "linearfeet": "linft", "linearfoot": "linft", "lf": "linft", "linfeet": "linft",
}


# Products are referenced by name, not by number: with numbered lists the model
# falls back to product 0 when nothing fits. An unknown name is simply dropped.
class _ProductMatch(BaseModel):
    product_name: str
    match: Literal["same", "comparable"]


class _LineMatches(BaseModel):
    line: int
    # Named first, so matches are held to it ("cable ties": tape is not a match).
    product_kind: str
    matches: list[_ProductMatch]


class _Matches(BaseModel):
    lines: list[_LineMatches]


def _unit_key(unit: str | None) -> str:
    unit = re.sub(r"[^a-z0-9]", "", (unit or "").lower())
    return _UNIT_ALIASES.get(unit, unit)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def _prefilter(products: list[str], lines: list[SuggestionLineIn]) -> list[str]:
    """Keep the products sharing the most words with any line (large catalogs only)."""
    line_words = set().union(*(_words(line.description) for line in lines))
    ranked = sorted(products, key=lambda p: len(_words(p) & line_words), reverse=True)
    return ranked[:MAX_PRODUCTS_IN_PROMPT]


def _build_prompt(lines: list[SuggestionLineIn], products: list[str]) -> str:
    line_list = "\n".join(f"{i}: {line.description}" for i, line in enumerate(lines))
    product_list = "\n".join(f"- {p}" for p in products)
    return f"""A company has negotiated prices for the catalog products below. For each requested line, first name the kind of product it is (e.g. "laptop", "cable ties", "printer toner"), then list the catalog products of exactly that kind.

Rules:
- A match must be the same kind of product. A different product that could do a similar job is NOT a match (adhesive tape is not cable ties; a machining service is not a machine; a floor mat with a logo is not a logo sign). Services like shipping or installation only match the same service.
- "same" = an identical product: the same manufacturer/model, or a generic commodity with an identical specification (e.g. "black toner, high yield"). Custom-made, branded or vendor-specific products from another maker are "comparable" at most. "comparable" = same kind of product, different brand or specification (e.g. a business laptop for a specific laptop model).
- Prefer the closest specification (size, resolution, capacity, color).
- If the catalog has nothing of that kind, return no matches for the line — that is the normal case.
- At most {MAX_PRODUCTS_PER_LINE} products per line. Copy product names exactly as listed.

Requested lines:
{line_list}

Catalog products:
{product_list}"""


def _is_vendor(article: Article, vendor_name: str | None, vendor_tax_id: str | None) -> bool:
    """Is the article's supplier the request's vendor? Tax ID first, then name."""
    supplier = article.supplier
    if supplier is None:
        return False
    tax_digits = re.sub(r"\D", "", vendor_tax_id or "")
    if tax_digits and re.sub(r"\D", "", supplier.vat_id or "") == tax_digits:
        return True
    key = company_key(vendor_name)
    return bool(key) and company_key(supplier.name) == key


def suggest_articles(
    lines: list[SuggestionLineIn],
    organization_id: uuid.UUID,
    db: Session,
    vendor_name: str | None = None,
    vendor_tax_id: str | None = None,
) -> list[list[ArticleSuggestion]]:
    """Negotiated catalog options per line (empty list = no match).

    Prices negotiated with the request's own vendor come first: that is the
    agreement the quote should honor. Other suppliers follow, cheapest first.
    """
    suggestions: list[list[ArticleSuggestion]] = [[] for _ in lines]
    if not lines or not settings.openai_api_key:
        return suggestions

    articles = db.scalars(
        select(Article)
        .options(joinedload(Article.supplier))
        .where(Article.organization_id == organization_id)
    ).all()
    by_product: dict[str, list[Article]] = defaultdict(list)
    for article in articles:
        by_product[article.description].append(article)
    if not by_product:
        return suggestions

    products = sorted(by_product)
    if len(products) > MAX_PRODUCTS_IN_PROMPT:
        products = _prefilter(products, lines)

    try:
        response = OpenAI(api_key=settings.openai_api_key).chat.completions.parse(
            model=settings.openai_model,
            messages=[{"role": "user", "content": _build_prompt(lines, products)}],
            response_format=_Matches,
            max_completion_tokens=1000,
            temperature=0.3,
        )
        matches = response.choices[0].message.parsed
    except Exception:  # noqa: BLE001 — suggestions are optional; never block the request
        logger.exception("Error matching catalog articles")
        return suggestions
    if matches is None:
        return suggestions

    offered = set(products)
    for line_matches in matches.lines:
        if not 0 <= line_matches.line < len(lines):
            continue  # ignore unknown lines
        known = [m for m in line_matches.matches[:MAX_PRODUCTS_PER_LINE]
                 if m.product_name in offered]
        # If the exact product is negotiated, substitutes are just noise.
        if any(m.match == "same" for m in known):
            known = [m for m in known if m.match == "same"]
        for m in known:
            _add_options(suggestions[line_matches.line], lines[line_matches.line],
                         by_product[m.product_name], m.match, vendor_name, vendor_tax_id)
    for options in suggestions:
        options.sort(key=lambda s: not s.same_vendor)  # stable: keeps cheapest-first
    return suggestions


def _plausible(line: SuggestionLineIn, article: Article) -> bool:
    """Only suggest what can be compared on price: same unit, similar price range.

    Deterministic guard against the model's occasional far-fetched matches
    (a per-hour machining service for a machine, a $5 parcel for a $320 transport).
    """
    if line.unit and _unit_key(line.unit) != _unit_key(article.unit):
        return False
    if line.unit_price and line.unit_price > 0:
        ratio = float(article.unit_price) / line.unit_price
        return 1 / MAX_PRICE_RATIO <= ratio <= MAX_PRICE_RATIO
    return True


def _add_options(
    target: list[ArticleSuggestion],
    line: SuggestionLineIn,
    articles: list[Article],
    match: str,
    vendor_name: str | None,
    vendor_tax_id: str | None,
) -> None:
    """Add the vendor's own article (if any) and the cheapest other suppliers."""
    ranked = sorted((a for a in articles if _plausible(line, a)), key=lambda a: a.unit_price)
    own = [a for a in ranked if _is_vendor(a, vendor_name, vendor_tax_id)]
    others = [a for a in ranked if a not in own][:MAX_SUPPLIERS_PER_PRODUCT]
    for article in own + others:
        target.append(
            ArticleSuggestion(
                article_id=article.id,
                article_number=article.article_number,
                description=article.description,
                supplier_name=article.supplier.name if article.supplier else "",
                unit_price=float(article.unit_price),
                unit=article.unit,
                quantity=float(article.quantity),
                match=match,
                same_vendor=article in own,
                same_unit=_unit_key(line.unit) == _unit_key(article.unit),
            )
        )


def snapshot_alternatives(
    db: Session,
    organization_id: uuid.UUID,
    order_lines: list,
    refs: list,
) -> list[dict]:
    """What the approver needs to know: per line, the negotiated options offered.

    Built from the organization's own catalog (never from client-sent prices)
    and stored with the request, so prices stay as they were at the time.
    ``refs`` are ``CatalogAlternativesIn``; ``order_lines`` are ``OrderLineIn``.
    """
    ids = {article_id for ref in refs for article_id in ref.article_ids}
    if not ids:
        return []
    articles = {
        a.id: a
        for a in db.scalars(
            select(Article)
            .options(joinedload(Article.supplier))
            .where(Article.organization_id == organization_id, Article.id.in_(ids))
        )
    }
    snapshot = []
    for ref in refs:
        if ref.line_index >= len(order_lines):
            continue
        options = [
            {
                "supplier_name": a.supplier.name if a.supplier else "",
                "article_number": a.article_number,
                "description": a.description,
                "unit_price": float(a.unit_price),
                "unit": a.unit,
            }
            for a in (articles.get(article_id) for article_id in ref.article_ids)
            if a is not None
        ]
        if options:
            line = order_lines[ref.line_index]
            snapshot.append({
                "line_description": line.position_description,
                "quoted_unit_price": line.unit_price,
                "unit": line.unit,
                "options": options,
            })
    return snapshot
