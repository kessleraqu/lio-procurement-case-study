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
from concurrent.futures import ThreadPoolExecutor
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
_UNKNOWN_KINDS = {"", "unknown", "none", "n/a", "unclear"}


Scope = Literal["same", "different"]


class _ProductMatch(BaseModel):
    product_name: str
    # What the catalog product is, in a few words — checked against the line's
    # kind in code, so a "floor mat" can't pass as a "logo sign".
    product_kind: str
    match: Literal["same", "comparable"]
    # Does the catalog product cover the same as the line, or a different scope
    # (e.g. a complete panel with logo for a line that is only the logo)?
    scope: Scope


class _LineResult(BaseModel):
    # Named first, so matches are held to it ("cable ties": tape is not a match).
    product_kind: str
    matches: list[_ProductMatch]


def _unit_key(unit: str | None) -> str:
    unit = re.sub(r"[^a-z0-9]", "", (unit or "").lower())
    return _UNIT_ALIASES.get(unit, unit)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def _prefilter(products: list[str], line: SuggestionLineIn) -> list[str]:
    """Keep the products sharing the most words with the line (large catalogs only)."""
    line_words = _words(line.description)
    ranked = sorted(products, key=lambda p: len(_words(p) & line_words), reverse=True)
    return ranked[:MAX_PRODUCTS_IN_PROMPT]


def _kind_words(kind: str) -> set[str]:
    # Crude singular form, so "cartridges" and "cartridge" agree.
    return {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in _words(kind)}


def _same_kind(line_kind: str, product_kind: str) -> bool:
    """The model's own two answers must agree: the line's kind and the product's kind
    share a word ("printer toner" / "toner cartridge"; not "logo sign" / "floor mat")."""
    return bool(_kind_words(line_kind) & _kind_words(product_kind))


def _exact_product(line: SuggestionLineIn, products: list[str]) -> str | None:
    """A catalog product whose every word appears in the line, without the model.

    Catches lines typed from the catalog ("Toner cartridge cyan") deterministically;
    the most specific product (most words) wins.
    """
    line_words = _words(line.description)
    candidates = [p for p in products if len(_words(p)) >= 2 and _words(p) <= line_words]
    return max(candidates, key=lambda p: len(_words(p)), default=None)


def _build_prompt(line: SuggestionLineIn, products: list[str]) -> str:
    product_list = "\n".join(f"- {p}" for p in products)
    return f"""A company has negotiated prices for the catalog products below. For the requested line, first name the kind of product it is (e.g. "laptop", "cable ties", "printer toner"), then list the catalog products of exactly that kind.

Rules:
- A match must be the same kind of product. A different product that could do a similar job is NOT a match (adhesive tape is not cable ties; a machining service is not a machine; a floor mat with a logo is not a logo sign). Services like shipping or installation only match the same service.
- "same" = an identical product: the same manufacturer/model, or a generic commodity with an identical specification (e.g. "black toner, high yield"). Custom-made, branded or vendor-specific products from another maker are "comparable" at most. "comparable" = same kind of product, different brand or specification (e.g. a business laptop for a specific laptop model).
- scope: "same" if the catalog product covers exactly what the line covers; "different" if it covers more or less (e.g. a complete panel with a logo, for a line that is only the logo).
- Prefer the closest specification (size, resolution, capacity, color).
- If the catalog has nothing of that kind, return no matches — that is the normal case.
- Brand names, model numbers and vendor codes in a description are normal — name the kind from the rest of it (e.g. "Contoso PRO-X 2400 series, 27 in IPS" is a monitor). Only a line that gives no clue at all what is being bought (e.g. "test", "xxx", "item 1") gets the kind "unknown" and no matches.
- For each match, also name the kind of the catalog product itself in a few words (e.g. "toner cartridge", "floor mat").
- At most {MAX_PRODUCTS_PER_LINE} products. Copy product names exactly as listed.

Requested line: {line.description}

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


def _ask_model(client: OpenAI, line: SuggestionLineIn, products: list[str]) -> _LineResult | None:
    """One small call for one line, so other lines can't sway its result."""
    try:
        response = client.chat.completions.parse(
            model=settings.openai_model,
            messages=[{"role": "user", "content": _build_prompt(line, products)}],
            response_format=_LineResult,
            max_completion_tokens=400,
            temperature=settings.openai_temperature,
            seed=settings.openai_seed,
        )
        return response.choices[0].message.parsed
    except Exception:  # noqa: BLE001 — suggestions are optional; never block the request
        logger.exception("Error matching catalog articles")
        return None


class _Verdict(BaseModel):
    accepted: list[str]


def _verify(client: OpenAI, line: SuggestionLineIn, names: list[str]) -> set[str]:
    """Second, narrow question per line: which candidates would a buyer accept instead?

    Easier for the model than open matching, so it catches far-fetched picks
    (a logo floor mat for a logo installation). If the check fails, keep them.
    """
    candidates = "\n".join(f"- {n}" for n in names)
    prompt = f"""A buyer needs: {line.description}
Would the buyer consider each catalog product below as an alternative — the same kind of thing for the same purpose? Another brand, model, size, configuration or a version that includes more (e.g. a generic business laptop for a specific laptop model) is acceptable. A product of another kind that merely shares a word or a feature is not (a floor mat with a logo is not a logo sign).
Return the names of the acceptable products, copied exactly; return none if none are.

{candidates}"""
    try:
        response = client.chat.completions.parse(
            model=settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            response_format=_Verdict,
            max_completion_tokens=200,
            temperature=settings.openai_temperature,
            seed=settings.openai_seed,
        )
        verdict = response.choices[0].message.parsed
        return set(verdict.accepted) if verdict else set(names)
    except Exception:  # noqa: BLE001 — the check is a filter; never lose suggestions to an outage
        logger.exception("Error verifying catalog matches")
        return set(names)


def suggest_articles(
    lines: list[SuggestionLineIn],
    organization_id: uuid.UUID,
    db: Session,
    vendor_name: str | None = None,
    vendor_tax_id: str | None = None,
) -> list[list[ArticleSuggestion]]:
    """Negotiated catalog options per line (empty list = no match).

    1. A line containing every word of a catalog product's name matches it
       directly — no model call, fully reproducible.
    2. Every other line gets its own small model call (in parallel), so a
       line's result depends on that line only.
    3. Code keeps the organization's own articles, filters implausible ones
       (other unit, price 10x off) and orders them: the quote's own vendor
       first, other suppliers cheapest first.
    """
    suggestions: list[list[ArticleSuggestion]] = [[] for _ in lines]
    if not lines:
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

    results: dict[int, _LineResult] = {}
    to_ask: dict[int, list[str]] = {}
    for i, line in enumerate(lines):
        if exact := _exact_product(line, products):
            results[i] = _LineResult(
                product_kind="exact catalog name",
                matches=[_ProductMatch(product_name=exact, product_kind="exact catalog name",
                                       match="same", scope="same")],
            )
        elif settings.openai_api_key:
            to_ask[i] = (
                _prefilter(products, line) if len(products) > MAX_PRODUCTS_IN_PROMPT else products
            )

    if to_ask:
        client = OpenAI(api_key=settings.openai_api_key)
        with ThreadPoolExecutor(max_workers=min(8, len(to_ask))) as pool:
            futures = {
                i: pool.submit(_ask_model, client, lines[i], candidates)
                for i, candidates in to_ask.items()
            }
            asked = {i: f.result() for i, f in futures.items() if f.result() is not None}
            # Verify the model's picks with a narrower yes/no question per line.
            checks = {
                i: pool.submit(_verify, client, lines[i], [m.product_name for m in r.matches])
                for i, r in asked.items() if r.matches
            }
            for i, check in checks.items():
                accepted = check.result()
                asked[i].matches = [m for m in asked[i].matches if m.product_name in accepted]
        results.update(asked)

    offered = set(products)
    for i, result in results.items():
        kind = result.product_kind.strip()
        if kind.lower() in _UNKNOWN_KINDS:
            continue  # the model couldn't tell what the line is: suggest nothing
        known = [
            m for m in result.matches[:MAX_PRODUCTS_PER_LINE]
            if m.product_name in offered and _same_kind(kind, m.product_kind)
        ]
        # If the exact product is negotiated, substitutes are just noise.
        if any(m.match == "same" for m in known):
            known = [m for m in known if m.match == "same"]
        for m in known:
            _add_options(suggestions[i], lines[i], by_product[m.product_name], m.match,
                         m.scope, kind, vendor_name, vendor_tax_id)
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
    scope: str,
    kind: str,
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
                scope=scope,
                same_vendor=article in own,
                same_unit=_unit_key(line.unit) == _unit_key(article.unit),
                matched_as=kind,
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
