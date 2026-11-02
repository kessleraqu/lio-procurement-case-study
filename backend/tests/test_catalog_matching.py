"""Negotiated-price suggestions: the model picks products, code does the rest."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import Article, Organization, Supplier
from app.schemas.articles import SuggestionLineIn
from app.seed import seed
from app.services.catalog_matching import suggest_articles

TONER = "Toner cartridge cyan"


@pytest.fixture(scope="module")
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    seed(session)  # Acme's real catalog + the other tenants
    # Another tenant sells the same product cheaper; Acme must never see it.
    walmart = session.scalar(select(Organization).where(Organization.slug == "walmart"))
    supplier = Supplier(organization_id=walmart.id, name="Walmart Supply")
    session.add(supplier)
    session.flush()
    session.add(Article(organization_id=walmart.id, article_number="WAL-1", description=TONER,
                        supplier_id=supplier.id, unit_price=1.0, unit="piece", quantity=1))
    session.commit()
    yield session
    session.close()


@pytest.fixture
def acme(db):
    return db.scalar(select(Organization).where(Organization.slug == "acme")).id


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setattr("app.services.catalog_matching.settings.openai_api_key", "test-key")


def _fake_openai(picks: dict[int, tuple[str, str]]):
    """Model stand-in: for line i, pick the product named picks[i] = (description, match)."""

    def parse(*, messages, response_format, **_):
        reply = {"lines": [
            {"line": line, "product_kind": "x", "matches": [{"product_name": name, "match": match}]}
            for line, (name, match) in picks.items()
        ]}
        message = SimpleNamespace(parsed=response_format.model_validate(reply))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    return lambda **_: client


def _suggest(db, org, lines, picks):
    with patch("app.services.catalog_matching.OpenAI", _fake_openai(picks)):
        return suggest_articles([SuggestionLineIn(**line) for line in lines], org, db)


def test_matched_product_lists_own_suppliers_cheapest_first(db, acme):
    [toner] = _suggest(db, acme, [{"description": "HP cyan toner", "unitPrice": 200,
                                   "amount": 3, "unit": "pieces"}], {0: (TONER, "same")})

    prices = [s.unit_price for s in toner]
    assert prices == sorted(prices) and 1 <= len(prices) <= 2
    assert all(s.description == TONER and s.match == "same" for s in toner)
    assert "Walmart Supply" not in {s.supplier_name for s in toner}  # tenant isolation
    # Other suppliers, priced in a comparable unit (pieces == piece).
    assert all(s.same_unit and not s.same_vendor for s in toner)


def test_options_in_another_unit_or_price_range_are_not_suggested(db, acme):
    other_unit, far_cheaper, no_unit = _suggest(db, acme, [
        {"description": "Toner", "unitPrice": 150, "unit": "hour"},
        {"description": "Toner", "unitPrice": 5000, "unit": "piece"},  # catalog ~$100-180
        {"description": "Toner"},
    ], {0: (TONER, "same"), 1: (TONER, "same"), 2: (TONER, "same")})
    assert other_unit == [] and far_cheaper == []
    assert no_unit  # nothing to compare against -> not filtered


def test_lines_without_a_match_get_no_suggestions(db, acme):
    result = _suggest(db, acme, [{"description": "Grinding machine"},
                                 {"description": "Toner"}], {1: (TONER, "comparable")})
    assert result[0] == [] and result[1][0].match == "comparable"


def test_unknown_lines_and_invented_products_from_the_model_are_ignored(db, acme):
    def parse(**_):
        def line(n, name):
            match = SimpleNamespace(product_name=name, match="same")
            return SimpleNamespace(line=n, product_kind="toner", matches=[match])
        reply = SimpleNamespace(lines=[line(5, TONER), line(0, "Toner cartridge cyan XL")])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=reply))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    with patch("app.services.catalog_matching.OpenAI", lambda **_: client):
        assert suggest_articles([SuggestionLineIn(description="x")], acme, db) == [[]]


def test_organization_without_a_catalog_gets_nothing_and_no_model_call(db):
    bmw = db.scalar(select(Organization).where(Organization.slug == "bmw")).id
    with patch("app.services.catalog_matching.OpenAI", side_effect=AssertionError("no call")):
        assert suggest_articles([SuggestionLineIn(description="Toner")], bmw, db) == [[]]


def test_substitutes_are_dropped_when_the_same_product_is_negotiated(db, acme):
    def parse(*, response_format, **_):
        reply = {"lines": [{"line": 0, "product_kind": "toner", "matches": [
            {"product_name": "Toner cartridge black, high-yield", "match": "same"},
            {"product_name": TONER, "match": "comparable"},
        ]}]}
        message = SimpleNamespace(parsed=response_format.model_validate(reply))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    with patch("app.services.catalog_matching.OpenAI", lambda **_: client):
        [result] = suggest_articles([SuggestionLineIn(description="HP black toner")], acme, db)
    assert result and {s.match for s in result} == {"same"}


def _priciest_supplier(db, acme, description):
    articles = db.scalars(select(Article).where(
        Article.organization_id == acme, Article.description == description)).all()
    return max(articles, key=lambda a: a.unit_price).supplier


@pytest.mark.parametrize("by", ["tax_id", "name"])
def test_price_negotiated_with_the_quotes_own_vendor_comes_first(db, acme, by):
    vendor = _priciest_supplier(db, acme, TONER)  # not among the 3 cheapest
    kwargs = ({"vendor_tax_id": vendor.vat_id.replace("-", " ")} if by == "tax_id"
              else {"vendor_name": vendor.name.upper().replace(" LP", ", L.P.")})
    line = SuggestionLineIn(description="Cyan toner", unitPrice=999, amount=2, unit="ea")
    with patch("app.services.catalog_matching.OpenAI", _fake_openai({0: (TONER, "same")})):
        [toner] = suggest_articles([line], acme, db, **kwargs)

    assert toner[0].supplier_name == vendor.name and toner[0].same_vendor
    assert not any(s.same_vendor for s in toner[1:])
    assert toner[0].same_unit  # "ea" == "piece"


def test_spellings_of_the_same_unit_are_compared():
    from app.services.catalog_matching import _unit_key
    assert _unit_key("square feet") == _unit_key("sq ft") == _unit_key("ft²".replace("²", "2"))
    assert _unit_key("linear feet") == _unit_key("lin. ft")
    assert _unit_key("ea.") == _unit_key("pieces")
    assert _unit_key("sq ft") != _unit_key("ft")


def test_snapshot_uses_own_catalog_prices_and_ignores_foreign_or_bad_refs(db, acme):
    from app.schemas.requests import CatalogAlternativesIn, OrderLineIn
    from app.services.catalog_matching import snapshot_alternatives

    own = db.scalar(select(Article).where(Article.organization_id == acme))
    foreign = db.scalar(select(Article).where(Article.article_number == "WAL-1"))
    line = OrderLineIn(positionDescription="Toner", unitPrice=210, amount=4, unit="pieces",
                       totalPrice=840)
    refs = [CatalogAlternativesIn(lineIndex=0, articleIds=[own.id, foreign.id]),
            CatalogAlternativesIn(lineIndex=7, articleIds=[own.id])]  # no such line

    [entry] = snapshot_alternatives(db, acme, [line], refs)

    assert entry["line_description"] == "Toner" and entry["quoted_unit_price"] == 210
    assert [o["article_number"] for o in entry["options"]] == [own.article_number]
    assert entry["options"][0]["unit_price"] == float(own.unit_price)
