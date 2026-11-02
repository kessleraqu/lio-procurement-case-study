"""Consistency checks on extracted quotes, and the one-retry flow built on them.

Uses synthetic quotes only — the checks are plain arithmetic and must hold for
any vendor's layout.
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.seed import seed_commodity_groups
from app.services.extraction import extract_vendor_data
from app.services.quote_checks import additional_charges, find_issues, normalize_tax_id
from app.services.quote_model import Quote


def _line(line_type="item", description="Widget", amount=2, unit_price=10.0,
          total_price=20.0, price_adjustment_percent=None):
    return {
        "line_type": line_type,
        "position_description": description,
        "amount": amount,
        "unit": "pieces",
        "unit_price": unit_price,
        "price_adjustment_percent": price_adjustment_percent,
        "total_price": total_price,
    }


def _quote(order_lines=None, shipping=None, fees=None, tax=None, grand_total=20.0,
           vendor="Acme Widgets, Inc.", customer="Buyer Corp"):
    return {
        "title": "Widgets",
        "customer_name": customer,
        "vendor_evidence": f"{vendor} letterhead",
        "vendor_name": vendor,
        "vat_id": "12-3456789",
        "department": None,
        "order_lines": order_lines if order_lines is not None else [_line()],
        "totals": {
            "items_subtotal": None,
            "shipping": shipping,
            "fees": fees,
            "tax": tax,
            "grand_total": grand_total,
        },
    }


def _issues(**kwargs) -> list[str]:
    return [i.message for i in find_issues(Quote.model_validate(_quote(**kwargs)))]


# --- find_issues -------------------------------------------------------------

def test_consistent_quote_has_no_issues():
    assert _issues() == []


def test_shipping_and_tax_from_totals_block_reconcile():
    assert _issues(shipping=15.0, tax=3.0, grand_total=38.0) == []


def test_shipping_listed_as_a_line_is_not_double_counted():
    lines = [_line(), _line("shipping", "Freight", 1, 15.0, 15.0)]
    assert _issues(order_lines=lines, shipping=15.0, grand_total=35.0) == []


def test_line_discount_is_applied():
    lines = [_line(amount=1, unit_price=100.0, total_price=80.0, price_adjustment_percent=-20)]
    assert _issues(order_lines=lines, grand_total=80.0) == []


def test_line_surcharge_is_applied():
    lines = [_line(amount=2, unit_price=50.0, total_price=105.0, price_adjustment_percent=5)]
    assert _issues(order_lines=lines, grand_total=105.0) == []


def test_line_whose_total_does_not_match_quantity_times_price():
    lines = [_line(amount=2, unit_price=10.0, total_price=35.0)]
    issues = _issues(order_lines=lines, grand_total=35.0)
    assert len(issues) == 1 and "2 × 10.00 = 20.00" in issues[0]


def test_alternative_counted_as_ordered_breaks_the_grand_total():
    lines = [_line(), _line(description="Optional upgrade", amount=1, unit_price=50.0,
                            total_price=50.0)]
    issues = _issues(order_lines=lines, grand_total=20.0)
    assert len(issues) == 1 and "grand total" in issues[0]


def _with_subtotal(subtotal, **kwargs):
    quote = _quote(**kwargs)
    quote["totals"]["items_subtotal"] = subtotal
    return find_issues(Quote.model_validate(quote))


def test_extra_line_is_reported_against_the_printed_subtotal_with_the_difference():
    lines = [_line(), _line(description="Optional upgrade", amount=1, unit_price=50.0,
                            total_price=50.0)]
    issues = _with_subtotal(20.0, order_lines=lines, tax=2.0, grand_total=22.0)
    assert len(issues) == 1 and issues[0].affects_request
    assert "items subtotal is 20.00 (difference 50.00)" in issues[0].message


def test_missed_tax_row_is_reported_on_the_totals_block_not_the_lines():
    # Lines match the subtotal; the model only read one of two tax rows.
    issues = _with_subtotal(20.0, shipping=10.0, tax=1.0, grand_total=32.5)
    assert len(issues) == 1
    assert "missed tax, shipping or fee row" in issues[0].message
    assert "difference 1.50" in issues[0].message
    # Lines and grand total are right; only the (unstored) tax breakdown is off.
    assert not issues[0].affects_request


def test_subtotal_that_includes_shipping_lines_is_accepted():
    lines = [_line(), _line("shipping", "Freight", 1, 15.0, 15.0)]
    assert _with_subtotal(35.0, order_lines=lines, tax=3.0, grand_total=38.0) == []


def test_alternatives_headers_and_tax_rows_are_ignored_in_the_sum():
    lines = [
        _line("header", "Section A", None, None, None),
        _line(),
        _line("alternative", "Instead: premium widget", 2, 30.0, 60.0),
        _line("tax", "Sales tax", None, None, 2.0),
    ]
    assert _issues(order_lines=lines, tax=2.0, grand_total=22.0) == []


def test_rounding_differences_are_tolerated():
    lines = [_line(amount=15.75, unit_price=7.65, total_price=120.49)]  # 120.4875
    assert _issues(order_lines=lines, grand_total=120.49) == []


@pytest.mark.parametrize("vendor,customer", [
    ("Buyer Corp", "Buyer Corp"),
    ("Buyer Corporation", "BUYER CORP."),
    ("Buyer, Inc.", "Buyer Inc"),
])
def test_vendor_equal_to_customer_is_flagged(vendor, customer):
    assert any("same company" in issue for issue in _issues(vendor=vendor, customer=customer))


def test_missing_grand_total_and_lines_are_flagged():
    issues = _issues(order_lines=[], grand_total=None)
    assert "No ordered line items were found." in issues
    assert "No grand total was found." in issues


@pytest.mark.parametrize("raw,expected", [
    ("94 1985704", "94-1985704"),
    ("941985704", "94-1985704"),
    ("94-1985704", "94-1985704"),
    ("DE 123456789", "DE 123456789"),  # not a US EIN: keep as printed
    ("  ", None),
    (None, None),
])
def test_normalize_tax_id(raw, expected):
    assert normalize_tax_id(raw) == expected


# --- additional_charges -----------------------------------------------------

def _charges(subtotal=None, **kwargs) -> float:
    quote = _quote(**kwargs)
    quote["totals"]["items_subtotal"] = subtotal
    return additional_charges(Quote.model_validate(quote))


def test_charges_are_everything_between_subtotal_and_grand_total():
    assert _charges(subtotal=20.0, shipping=10.0, tax=2.5, grand_total=32.5) == 12.5


def test_shipping_line_outside_the_subtotal_is_not_counted_twice():
    lines = [_line(), _line("shipping", "Freight", 1, 10.0, 10.0)]
    # Subtotal covers items only; the freight line is already in the lines.
    assert _charges(subtotal=20.0, order_lines=lines, tax=2.5, grand_total=32.5) == 2.5


def test_shipping_line_inside_the_subtotal_is_not_counted_twice():
    lines = [_line(), _line("shipping", "Freight", 1, 10.0, 10.0)]
    assert _charges(subtotal=30.0, order_lines=lines, tax=2.5, grand_total=32.5) == 2.5


def test_without_a_subtotal_charges_come_from_the_totals_block():
    assert _charges(shipping=10.0, fees=1.0, tax=2.5, grand_total=33.5) == 13.5


def test_misread_lines_do_not_change_the_charges():
    # An alternative counted as ordered must stay visible as a gap, not be absorbed.
    lines = [_line(), _line(description="Optional upgrade", amount=1, unit_price=50.0,
                            total_price=50.0)]
    assert _charges(subtotal=20.0, order_lines=lines, tax=2.0, grand_total=22.0) == 2.0


# --- extraction retry flow ---------------------------------------------------

@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr("app.services.extraction.settings.openai_api_key", "test-key")
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    seed_commodity_groups(session)
    session.commit()
    yield session
    session.close()


def _fake_openai(*extraction_replies: dict):
    """Returns the extraction replies in order; classification always picks Hardware."""
    replies = iter(extraction_replies)
    calls = []

    def parse(*, response_format, messages, **_):
        calls.append(messages)
        if response_format is Quote:
            reply = next(replies)
        else:
            reply = {"commodity_group_name": "Hardware", "commodity_group_id": 29}
        message = SimpleNamespace(
            parsed=response_format.model_validate(reply),
            content=json.dumps(reply),
            refusal=None,
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    return (lambda **_: client), calls


INCONSISTENT = _quote(
    order_lines=[_line(), _line(description="Optional upgrade", amount=1, unit_price=50.0,
                                total_price=50.0)],
    grand_total=20.0,
)
CORRECTED = _quote(
    order_lines=[_line(), _line("alternative", "Optional upgrade", 1, 50.0, 50.0)],
    grand_total=20.0,
)


def test_inconsistent_extraction_is_retried_with_the_problems_spelled_out(db):
    fake, calls = _fake_openai(INCONSISTENT, CORRECTED)

    with patch("app.services.extraction.OpenAI", fake):
        result = extract_vendor_data("quote text", db)

    assert result.success and not result.warnings
    assert [line.position_description for line in result.data.order_lines] == ["Widget"]
    retry_prompt = calls[1][-1]["content"]
    assert "grand total" in retry_prompt


def test_consistent_extraction_is_not_retried(db):
    fake, calls = _fake_openai(CORRECTED)

    with patch("app.services.extraction.OpenAI", fake):
        result = extract_vendor_data("quote text", db)

    assert result.success and not result.warnings
    assert len(calls) == 2  # one extraction + one classification


def test_issues_that_survive_the_retry_are_returned_as_warnings(db):
    fake, _ = _fake_openai(INCONSISTENT, INCONSISTENT)

    with patch("app.services.extraction.OpenAI", fake):
        result = extract_vendor_data("quote text", db)

    assert result.success
    assert result.warnings and result.warnings[0].code == "lines_vs_grand_total"
    assert "grand total" in result.warnings[0].message


def test_priced_alternatives_are_offered_separately_and_not_ordered(db):
    reply = _quote(
        order_lines=[
            _line(),
            _line("alternative", "Alternative: premium option", None, None, None),  # heading
            _line("alternative", "Premium widget", 2, 30.0, None),  # priced per unit only
        ],
        grand_total=20.0,
    )
    fake, _ = _fake_openai(reply)

    with patch("app.services.extraction.OpenAI", fake):
        result = extract_vendor_data("quote text", db)

    assert [line.position_description for line in result.data.order_lines] == ["Widget"]
    assert [(a.position_description, a.total_price) for a in result.data.alternatives] == [
        ("Premium widget", 60.0)
    ]
