"""Organization booking rules and organization-only commodity groups (complaint 3).

The model only says which rule covers a purchase; these tests pin down what the
code then does with that answer, using Acme's real seeded rules.
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import CommodityGroup, Organization
from app.seed import seed
from app.services.extraction import extract_vendor_data
from app.services.quote_model import Quote
from tests.test_extraction_checks import _line, _quote


@pytest.fixture(scope="module")
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    seed(session)
    session.commit()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setattr("app.services.extraction.settings.openai_api_key", "test-key")


def _org(db, slug):
    return db.scalar(select(Organization).where(Organization.slug == slug)).id


def _run(db, org_id, *, matched_rule=None, model_group, grand_total=100.0, lines=None):
    """Extract a quote; the fake model picks ``model_group`` as the general group.

    ``lines`` is [(price, rule)]; by default one line covered by ``matched_rule``.
    """
    group = db.scalar(select(CommodityGroup).where(CommodityGroup.name == model_group))
    lines = lines or [(grand_total, matched_rule)]
    prompts = []

    def parse(*, response_format, messages, **_):
        prompts.append(messages[-1]["content"])
        if response_format is Quote:
            reply = _quote(order_lines=[_line(description=f"item {i}", amount=1, unit_price=p,
                                              total_price=p) for i, (p, _) in enumerate(lines)],
                           grand_total=grand_total)
        else:
            reply = {"line_rules": [{"line": i, "rule": r} for i, (_, r) in enumerate(lines)],
                     "commodity_group_name": group.name, "commodity_group_id": group.id}
        message = SimpleNamespace(parsed=response_format.model_validate(reply),
                                  content=json.dumps(reply), refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    with patch("app.services.extraction.OpenAI", lambda **_: client):
        result = extract_vendor_data("quote text", db, org_id)
    return result, prompts[-1]


def test_matching_rule_overrides_the_models_group(db):
    data = _run(db, _org(db, "acme"), matched_rule="Printer toner",
                model_group="Toner Cartridges")[0].data
    assert data.commodity_group_name == "Hardware"
    assert data.commodity_rule == "printer toner"


def test_rule_with_a_limit_only_applies_up_to_it(db):
    acme = _org(db, "acme")
    small = _run(db, acme, matched_rule="software subscriptions", model_group="Software",
                 grand_total=480.0)[0].data
    large = _run(db, acme, matched_rule="software subscriptions", model_group="Software",
                 grand_total=12_000.0)[0].data
    assert small.commodity_group_name == "Office Supplies"
    assert large.commodity_group_name == "Software" and large.commodity_rule is None


def test_no_or_invented_rule_keeps_the_models_group(db):
    acme = _org(db, "acme")
    for matched_rule in (None, "office chairs"):
        data = _run(db, acme, matched_rule=matched_rule, model_group="Toner Cartridges")[0].data
        assert data.commodity_group_name == "Toner Cartridges"
        assert data.commodity_rule is None


def test_other_organizations_get_neither_acme_rules_nor_acme_groups(db):
    result, prompt = _run(db, _org(db, "walmart"), matched_rule="printer toner",
                          model_group="Toner Cartridges")
    data = result.data
    assert data.commodity_group_name == "Toner Cartridges" and data.commodity_rule is None
    assert "Office Supplies" not in prompt and "booking rules" not in prompt


def test_acme_prompt_lists_its_rules_and_its_own_group(db):
    _, prompt = _run(db, _org(db, "acme"), matched_rule=None, model_group="Software")
    assert '"cable ties"' in prompt and '"printer toner"' in prompt
    assert "Office Supplies" in prompt


def test_seeding_twice_creates_nothing_new(db):
    assert seed(db)["acme_groups"] == 0


def test_group_follows_the_largest_share_without_notice_for_small_extras(db):
    # $840 toner + $45 other item: toner (95%) decides, the 5% isn't worth a notice.
    result, _ = _run(db, _org(db, "acme"), model_group="Office Equipment", grand_total=885.0,
                     lines=[(840.0, "printer toner"), (45.0, None)])
    assert result.data.commodity_group_name == "Hardware"
    assert not any(w.code == "mixed_booking" for w in result.warnings or [])


def test_big_discrepancy_is_reported_but_the_majority_still_decides(db):
    # $300 general + $100 cable ties (25%): general group wins, requester is told.
    result, _ = _run(db, _org(db, "acme"), model_group="Office Equipment", grand_total=400.0,
                     lines=[(300.0, None), (100.0, "cable ties")])
    assert result.data.commodity_group_name == "Office Equipment"
    assert result.data.commodity_rule is None
    [notice] = [w for w in result.warnings if w.code == "mixed_booking"]
    assert "cable ties → Electrical Engineering ($100.00, 25%)" in notice.message
    assert "booked under Office Equipment" in notice.message
