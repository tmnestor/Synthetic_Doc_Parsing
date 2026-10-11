"""Tests for the deterministic distractor value rules and text pools."""

import copy
import random
import re
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

from generators import abn_register
from generators.abn_register import AbnRegister
from generators.common import validate_abn
from generators.content_engine import ContentEngine, build_engine
from generators.distractors import values
from generators.distractors.config import load_distractor_config
from generators.distractors.values import (
    RULE_TYPES,
    RULES,
    RuleContext,
    ValueRuleError,
    parse_bank_rows,
)
from tests.conftest import REPO_ROOT, assert_diagnostic_error

GT = REPO_ROOT / "ground_truth"


@lru_cache(maxsize=None)
def _engine() -> ContentEngine:
    return build_engine()


@lru_cache(maxsize=None)
def _gt(name: str) -> dict:
    return yaml.safe_load((GT / name).read_text())


def ctx(seed: int = 1, fields: dict | None = None, **kw) -> RuleContext:
    base = {
        "SUPPLIER_NAME": "Harrowgate Trade Supplies Pty Ltd",
        "BUSINESS_ADDRESS": "12 Example St, Paddington QLD 4064",
        "INVOICE_DATE": "28/02/2024",
        "TOTAL_AMOUNT": "216.89",
        "LINE_ITEM_DESCRIPTIONS": "a|b|c",
        "STATEMENT_DATE_RANGE": "01/09/2023 - 23/09/2023",
    }
    base.update(fields or {})
    engine = _engine()
    return RuleContext(
        rng=random.Random(seed),
        fields=base,
        engine=engine,
        pools=engine.pools,
        case_id=kw.pop("case_id", "CASE999"),
        rows=kw.pop("rows", None),
        selected=kw.pop("selected", {}),
        service_category=kw.pop("service_category", None),
    )


def bank_ctx(case: str = "CASE001", seed: int = 3) -> RuleContext:
    fields = _gt("bank_statements.yml")[case]["fields"]
    return ctx(seed, fields, case_id=case, rows=parse_bank_rows(fields))


def test_rule_types_cover_the_catalogue() -> None:
    assert RULE_TYPES == frozenset(RULES) | frozenset(values.MULTI_VALUE_RULES)
    used = {item.rule["type"] for item in load_distractor_config().items.values()}
    assert used <= RULE_TYPES


@pytest.mark.parametrize("name", sorted(RULES))
def test_every_rule_is_deterministic(name: str) -> None:
    samples: dict[str, dict] = {
        "digits": {"length": 6, "prefix": "INV-"},
        "phone": {"kind": "landline"},
        "email": {"pool": "email_local"},
        "amount": {"min": "1.00", "max": "25.00"},
        "amount_fraction": {"of": "TOTAL_AMOUNT", "min_fraction": "0.03", "max_fraction": "0.25"},
        "fixed": {"value": "0.00"},
        "repeat": {"field": "TOTAL_AMOUNT"},
        "date_offset": {"from": "INVOICE_DATE", "days": [7, 14]},
        "month_period": {"from": "INVOICE_DATE", "months": [-1, 1]},
        "state_text": {
            "state": "payment",
            "pools": {"paid": "direct_debit_paid", "unpaid": "direct_debit_notice"},
        },
        "range_end_offset": {"days": [1, 2]},
        "pool_text": {"pool": "returns_policy"},
        "annotated_rows": {"extra": [1, 3]},
        "row_marks": {"marks": ["tick", "circle", "underline"]},
        "pick": {"values": ["PAID", "PAID IN FULL", "RECEIVED"]},
        "address": {"state_of": "BUSINESS_ADDRESS"},
        "po_box": {"state_of": "BUSINESS_ADDRESS"},
        "amount_ratio": {
            "of": "TOTAL_AMOUNT",
            "min_ratio": "0.30",
            "max_ratio": "1.50",
            "element": "whole",
            "cap": "none",
        },
    }
    rule = {"type": name, **samples.get(name, {})}

    def make(seed: int) -> RuleContext:
        if name in ("annotated_rows", "annotation_total", "row_marks"):
            c = bank_ctx(seed=seed)
            if name in ("annotation_total", "row_marks"):
                debits = [i for i, row in enumerate(c.rows or []) if row["paid"] is not None][:2]
                c.selected["DX_ANNOTATED_ROWS"] = "|".join(map(str, debits))
            return c
        if name == "link_date":
            # The paid date reads the case's own link, so it needs a corpus case.
            return ctx(seed, case_id="CASE001")
        c = ctx(seed, selected={"DX_SURVEY_CODE": "12345678"})
        c.states["payment"] = "paid"
        return c

    first = RULES[name](make(5), rule)
    assert RULES[name](make(5), rule) == first
    assert first


def test_digits_shape() -> None:
    value = RULES["digits"](ctx(), {"type": "digits", "length": 6, "prefix": "INV-"})
    assert re.fullmatch(r"INV-[1-9]\d{5}", value)
    assert re.fullmatch(
        r"[1-9]\d{14}", RULES["digits"](ctx(), {"type": "digits", "length": 15, "prefix": ""})
    )


def test_digits_missing_parameter_is_a_diagnostic() -> None:
    with pytest.raises(ValueRuleError) as err:
        RULES["digits"](ctx(), {"type": "digits", "length": 6})
    assert_diagnostic_error(err.value)
    assert "prefix" in str(err.value)


def test_acn_check_digit_is_valid() -> None:
    """ACN weights 8,7,6,5,4,3,2,1 over the first 8 digits; check = (10 - sum % 10) % 10."""
    for seed in range(20):
        value = RULES["acn"](ctx(seed=seed), {"type": "acn"})
        assert re.fullmatch(r"\d{3} \d{3} \d{3}", value)
        digits = [int(ch) for ch in value.replace(" ", "")]
        weighted = sum(d * w for d, w in zip(digits[:8], range(8, 0, -1), strict=True))
        assert len(digits) == 9 and digits[8] == (10 - weighted % 10) % 10


def _company_abn(acn: str) -> str:
    """The checksum-valid ABN a company with this ACN would hold: two digits, then the ACN."""
    digits = acn.replace(" ", "")
    prefix = next(p for p in range(10, 100) if validate_abn(f"{p}{digits}"))
    return f"{prefix} {digits[:3]} {digits[3:6]} {digits[6:]}"


def test_acn_of_a_registered_company_is_redrawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ACN that is the tail of a registered ABN belongs to a real company, so it is redrawn."""
    first = RULES["acn"](ctx(seed=7), {"type": "acn"})
    real = AbnRegister.from_abns([int(_company_abn(first).replace(" ", ""))])
    monkeypatch.setattr(abn_register, "load_abn_register", lambda pools, pools_path: real)

    redrawn_ctx = ctx(seed=7)
    value = RULES["acn"](redrawn_ctx, {"type": "acn"})

    assert value != first
    digits = [int(ch) for ch in value.replace(" ", "")]
    weighted = sum(d * w for d, w in zip(digits[:8], range(8, 0, -1), strict=True))
    assert digits[8] == (10 - weighted % 10) % 10
    assert _company_abn(value) not in real
    # The redraw comes from its own stream, so the document's later draws are unchanged.
    plain_ctx = ctx(seed=7)
    RULES["acn"](plain_ctx, {"type": "acn"})
    assert redrawn_ctx.rng.random() == plain_ctx.rng.random()


def test_acn_screen_fails_fast_when_every_draw_is_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Everything:
        def __contains__(self, abn: object) -> bool:
            return True

    monkeypatch.setattr(abn_register, "load_abn_register", lambda pools, pools_path: _Everything())
    with pytest.raises(ValueRuleError) as err:
        RULES["acn"](ctx(seed=7), {"type": "acn"})
    assert_diagnostic_error(err.value)


def test_bsb_shape() -> None:
    for seed in range(20):
        assert re.fullmatch(r"[1-9]\d{2}-\d{3}", RULES["bsb"](ctx(seed=seed), {"type": "bsb"}))


def test_phone_kinds() -> None:
    land = RULES["phone"](ctx(), {"type": "phone", "kind": "landline"})
    assert re.fullmatch(r"\(07\) \d{4} \d{4}", land)  # QLD
    wa = ctx(fields={"BUSINESS_ADDRESS": "1 A St, Fremantle WA 6160"})
    assert RULES["phone"](wa, {"type": "phone", "kind": "landline"}).startswith("(08) ")
    assert re.fullmatch(r"04\d\d \d{3} \d{3}", RULES["phone"](ctx(), {"type": "phone", "kind": "mobile"}))
    assert re.fullmatch(r"1300 \d{3} \d{3}", RULES["phone"](ctx(), {"type": "phone", "kind": "freecall"}))


def test_phone_without_a_state_is_a_diagnostic() -> None:
    with pytest.raises(ValueRuleError) as err:
        RULES["phone"](ctx(fields={"BUSINESS_ADDRESS": "nowhere"}), {"type": "phone", "kind": "landline"})
    assert_diagnostic_error(err.value)


def test_slug_email_website_social() -> None:
    assert RULES["website"](ctx(), {"type": "website"}) == "www.harrowgatetradesupplies.com.au"
    assert RULES["social"](ctx(), {"type": "social"}) == "@harrowgatetradesupplies"
    email = RULES["email"](ctx(), {"type": "email", "pool": "email_local"})
    local, domain = email.split("@")
    assert domain == "harrowgatetradesupplies.com.au"
    assert local in _engine().pools["distractor_text"]["email_local"]


def test_slug_matching_a_real_business_is_refused() -> None:
    with pytest.raises(ValueRuleError) as err:
        RULES["website"](ctx(fields={"SUPPLIER_NAME": "Dan Murphy's Pty Ltd"}), {"type": "website"})
    assert_diagnostic_error(err.value)
    assert "danmurphys" in str(err.value)


def test_survey_url_reads_selected_code() -> None:
    c = ctx(selected={"DX_SURVEY_CODE": "12345678"})
    assert RULES["survey_url"](c, {"type": "survey_url"}) == (
        "https://survey.harrowgatetradesupplies.com.au/12345678"
    )
    with pytest.raises(ValueRuleError) as err:
        RULES["survey_url"](ctx(), {"type": "survey_url"})
    assert_diagnostic_error(err.value)


def test_amount_in_range_two_decimals() -> None:
    for seed in range(30):
        value = RULES["amount"](ctx(seed=seed), {"type": "amount", "min": "1.00", "max": "25.00"})
        assert re.fullmatch(r"\d+\.\d{2}", value)
        assert Decimal("1.00") <= Decimal(value) <= Decimal("25.00")


FRACTION = {"type": "amount_fraction", "of": "TOTAL_AMOUNT", "min_fraction": "0.03", "max_fraction": "0.25"}


@pytest.mark.parametrize("total", ["0.02", "0.05", "0.37", "1.00", "18.22", "216.89", "4999.99"])
def test_amount_fraction_is_always_strictly_below_the_total(total: str) -> None:
    whole = Decimal(total)
    for seed in range(300):
        value = RULES["amount_fraction"](ctx(seed=seed, fields={"TOTAL_AMOUNT": total}), FRACTION)
        assert re.fullmatch(r"\d+\.\d{2}", value), value
        assert Decimal("0.01") <= Decimal(value) < whole, (total, value)
        if whole >= Decimal("10.00"):
            assert whole * Decimal("0.03") - Decimal("0.01") <= Decimal(value) <= whole * Decimal("0.25")


def test_amount_fraction_is_deterministic_per_seed() -> None:
    draws = [RULES["amount_fraction"](ctx(seed=9), FRACTION) for _ in range(3)]
    assert len(set(draws)) == 1
    assert len({RULES["amount_fraction"](ctx(seed=s), FRACTION) for s in range(40)}) > 1


@pytest.mark.parametrize("total", ["0.01", "0.00", "abc"])
def test_amount_fraction_without_room_below_the_total_is_a_diagnostic(total: str) -> None:
    with pytest.raises(ValueRuleError) as err:
        RULES["amount_fraction"](ctx(fields={"TOTAL_AMOUNT": total}), FRACTION)
    assert_diagnostic_error(err.value)
    assert err.value.field == "TOTAL_AMOUNT"


def test_every_shipped_saving_is_below_its_receipts_total() -> None:
    """CASE040 once printed a saving of 21.04 under a total of 18.22."""
    from generators.distractors.providers import draw_all_eligible

    item = load_distractor_config().items["rc_you_saved"]
    assert item.rule == FRACTION
    for case_id, entry in _gt("receipts.yml").items():
        drawn = draw_all_eligible({**entry, "case_id": str(case_id)}, "receipt")
        saved = drawn["DX_YOU_SAVED"]
        assert Decimal(saved) < Decimal(entry["fields"]["TOTAL_AMOUNT"]), (case_id, saved)


def test_fixed_repeat_item_count() -> None:
    assert RULES["fixed"](ctx(), {"type": "fixed", "value": "0.00"}) == "0.00"
    assert RULES["repeat"](ctx(), {"type": "repeat", "field": "TOTAL_AMOUNT"}) == "216.89"
    assert RULES["item_count"](ctx(), {"type": "item_count"}) == "3"


def test_date_offsets() -> None:
    value = RULES["date_offset"](ctx(), {"type": "date_offset", "from": "INVOICE_DATE", "days": [7]})
    assert value == "06/03/2024"  # 2024 is a leap year
    back = RULES["date_offset"](ctx(), {"type": "date_offset", "from": "INVOICE_DATE", "days": [-1]})
    assert back == "27/02/2024"
    end = RULES["range_end_offset"](ctx(), {"type": "range_end_offset", "days": [3]})
    assert end == "26/09/2023"
    for seed in range(10):
        drawn = RULES["date_offset"](
            ctx(seed=seed), {"type": "date_offset", "from": "INVOICE_DATE", "days": [7, 14, 30]}
        )
        delta = datetime.strptime(drawn, "%d/%m/%Y") - datetime(2024, 2, 28)
        assert delta.days in (7, 14, 30)


def test_address_po_box_person() -> None:
    assert re.search(
        r" (NSW|VIC|QLD|SA|WA|TAS|ACT|NT) \d{4}$",
        RULES["address"](ctx(), {"type": "address", "state_of": "any"}),
    )
    assert re.fullmatch(
        r"PO Box [1-9]\d{3}, .+ (NSW|VIC|QLD|SA|WA|TAS|ACT|NT) \d{4}",
        RULES["po_box"](ctx(), {"type": "po_box", "state_of": "any"}),
    )
    assert " " in RULES["person"](ctx(), {"type": "person"})


LEGAL = {"SUPPLIER_NAME": "Ashby Legal Group"}


def test_pool_text_draws_from_pool() -> None:
    value = RULES["pool_text"](ctx(), {"type": "pool_text", "pool": "returns_policy"})
    assert value in _engine().pools["distractor_text"]["returns_policy"]
    value = RULES["pool_text"](
        ctx(fields=LEGAL, service_category="legal"), {"type": "pool_text", "pool": "terms"}
    )
    assert value in _engine().pools["distractor_text"]["terms"]["legal_services"]
    with pytest.raises(ValueRuleError) as err:
        RULES["pool_text"](ctx(), {"type": "pool_text", "pool": "no_such_pool"})
    assert_diagnostic_error(err.value)


# --- pools ---------------------------------------------------------------------------------------


def test_every_catalogue_pool_exists_with_five_entries() -> None:
    texts = _engine().pools["distractor_text"]
    named = {item.rule["pool"] for item in load_distractor_config().items.values() if "pool" in item.rule}
    assert named
    for pool in named:
        if isinstance(texts[pool], dict):
            assert all(len(sub) >= 2 for sub in texts[pool].values()), pool
        else:
            assert len(texts[pool]) >= 5, pool


def test_pool_entries_pass_the_screen_and_use_british_style() -> None:
    c = ctx()
    for name, pool in c.pools["distractor_text"].items():
        entries = [e for sub in pool.values() for e in sub] if isinstance(pool, dict) else pool
        assert entries, name
        for entry in entries:
            values._screen_text(c, str(entry), name)
            assert "\u2014" not in entry and "–" not in entry and "--" not in entry
            assert not re.search(r"\b(color|center|organize|favorite)\b", entry, re.I)


def test_phone_area_codes_cover_every_state() -> None:
    assert set(_engine().pools["phone_area_codes"]) == {"NSW", "ACT", "VIC", "TAS", "QLD", "SA", "WA", "NT"}


def test_a_blocklisted_pool_entry_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pool sentence naming a real business (taken from the blocklist itself) is refused."""
    pools = copy.deepcopy(_engine().pools)
    real = pools["retailers"][0]["name"]
    pools["distractor_text"]["tagline"]["legal_services"] = [f"Proudly stocked by {real} since 1998"]
    c = RuleContext(
        random.Random(1),
        dict(LEGAL),
        ContentEngine(pools),
        pools,
        "CASE999",
        None,
        {},
        service_category="legal",
    )
    with pytest.raises(ValueRuleError) as err:
        RULES["pool_text"](c, {"type": "pool_text", "pool": "tagline"})
    assert_diagnostic_error(err.value)
    assert real in str(err.value)
    # and the unmodified pools draw cleanly
    assert RULES["pool_text"](
        ctx(fields=LEGAL, service_category="legal"), {"type": "pool_text", "pool": "tagline"}
    )


# --- bank rows -----------------------------------------------------------------------------------


def _first_case_with_links() -> str:
    links = yaml.safe_load((GT / "transaction_links.yml").read_text())
    for entries in links.values():
        for link in entries:
            return link["bank_statement"].split("_")[0]
    raise AssertionError("no links")


def _linked(case: str) -> list[dict]:
    links = yaml.safe_load((GT / "transaction_links.yml").read_text())
    return [
        link
        for entries in links.values()
        for link in entries
        if link["bank_statement"].startswith(f"{case}_")
    ]


def test_annotated_rows_include_every_linked_row() -> None:
    """Every row a receipt or invoice links to is annotated; extras are other debits."""
    case = _first_case_with_links()
    c = bank_ctx(case)
    assert c.rows is not None
    out = RULES["annotated_rows"](c, {"type": "annotated_rows", "extra": [1, 3]})
    indices = [int(i) for i in out.split("|")]
    assert indices == sorted(indices) and len(set(indices)) == len(indices)
    links = _linked(case)
    assert links
    linked_idx: set[int] = set()
    for link in links:
        match = [
            i
            for i, r in enumerate(c.rows)
            if i not in linked_idx
            and r["date"] == link["bank_date"]
            and r["paid"] == str(link["bank_amount"])
            and " ".join(r["description"].split()) == " ".join(str(link["bank_description"]).split())
        ]
        assert match and match[0] in indices
        linked_idx.add(match[0])
    assert len(linked_idx) == len(links)
    extras = [i for i in indices if i not in linked_idx]
    assert 1 <= len(extras) <= 3
    assert all(c.rows[i]["paid"] is not None for i in indices)


def test_annotated_rows_across_every_case() -> None:
    """Every real statement resolves all of its links without a diagnostic."""
    for case in _gt("bank_statements.yml"):
        c = bank_ctx(case)
        out = RULES["annotated_rows"](c, {"type": "annotated_rows", "extra": [1, 3]})
        assert len(out.split("|")) >= len(_linked(case))


def test_annotation_total_is_the_sum_of_paid() -> None:
    c = bank_ctx()
    assert c.rows is not None
    out = RULES["annotated_rows"](c, {"type": "annotated_rows", "extra": [1, 3]})
    c.selected["DX_ANNOTATED_ROWS"] = out
    total = RULES["annotation_total"](c, {"type": "annotation_total"})
    expected = sum(Decimal(c.rows[int(i)]["paid"]) for i in out.split("|"))
    assert total == f"{expected:.2f}"


def test_a_link_missing_from_the_statement_rows_is_a_diagnostic() -> None:
    """Ground truth and links out of step fail fast, naming case and link."""
    case = _first_case_with_links()
    c = bank_ctx(case)
    assert c.rows is not None
    link = _linked(case)[0]
    rows = [r for r in c.rows if not (r["date"] == link["bank_date"] and r["paid"] == link["bank_amount"])]
    broken = RuleContext(c.rng, c.fields, c.engine, c.pools, case, rows, {})
    with pytest.raises(ValueRuleError) as err:
        RULES["annotated_rows"](broken, {"type": "annotated_rows", "extra": [1, 3]})
    assert_diagnostic_error(err.value)
    assert case in str(err.value) and link["bank_description"] in str(err.value)


def test_an_unquoted_float_link_amount_still_matches_its_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A YAML float drops the trailing zero (61.2); the rows carry 61.20. Compared as decimals."""
    description = "EFTPOS Quenbrook Fodder"
    rows = [
        {"date": "02/03/2031", "description": "ATM Withdrawal", "paid": "40.00", "received": None},
        {"date": "05/03/2031", "description": description, "paid": "61.20", "received": None},
        {"date": "07/03/2031", "description": "Salary", "paid": None, "received": "900.00"},
        {"date": "09/03/2031", "description": "Card fee", "paid": "3.50", "received": None},
    ]
    links = {
        "CASE999_receipt.png": [
            {
                "bank_statement": "CASE999_bank.png",
                "bank_date": "05/03/2031",
                "bank_amount": 61.2,
                "bank_description": description,
            }
        ]
    }
    path = tmp_path / "transaction_links.yml"
    path.write_text(yaml.safe_dump(links))
    assert isinstance(yaml.safe_load(path.read_text())["CASE999_receipt.png"][0]["bank_amount"], float)
    monkeypatch.setattr(values, "LINKS_PATH", path)
    c = ctx(seed=4, rows=rows, case_id="CASE999")
    out = RULES["annotated_rows"](c, {"type": "annotated_rows", "extra": [1, 1]})
    indices = [int(i) for i in out.split("|")]
    assert 1 in indices and len(indices) == 2
    assert all(rows[i]["paid"] is not None for i in indices)


def test_invented_link_values_are_absent_from_the_ground_truth() -> None:
    for name in ("bank_statements.yml", "transaction_links.yml"):
        assert "Quenbrook" not in (GT / name).read_text()


def test_rows_rule_without_rows_is_a_diagnostic() -> None:
    with pytest.raises(ValueRuleError) as err:
        RULES["annotated_rows"](ctx(), {"type": "annotated_rows", "extra": [1, 3]})
    assert_diagnostic_error(err.value)


def test_rules_never_use_the_global_random(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the global random functions to raise; every rule still works from ctx.rng."""

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("global random used")

    for name in ("random", "randint", "choice", "sample", "shuffle", "randrange", "uniform", "choices"):
        monkeypatch.setattr(random, name, boom)

    rules: dict[str, dict] = {
        "digits": {"length": 6, "prefix": "P-"},
        "acn": {},
        "bsb": {},
        "phone": {"kind": "landline"},
        "email": {"pool": "payid_local"},
        "website": {},
        "social": {},
        "amount": {"min": "1.00", "max": "9.00"},
        "amount_fraction": {"of": "TOTAL_AMOUNT", "min_fraction": "0.03", "max_fraction": "0.25"},
        "fixed": {"value": "0.00"},
        "repeat": {"field": "TOTAL_AMOUNT"},
        "item_count": {},
        "date_offset": {"from": "INVOICE_DATE", "days": [7, 14]},
        "month_period": {"from": "INVOICE_DATE", "months": [-1, 1]},
        "state_text": {
            "state": "payment",
            "pools": {"paid": "direct_debit_paid", "unpaid": "direct_debit_notice"},
        },
        "range_end_offset": {"days": [1, 2]},
        "address": {"state_of": "any"},
        "amount_ratio": {
            "of": "TOTAL_AMOUNT",
            "min_ratio": "0.30",
            "max_ratio": "1.50",
            "element": "whole",
            "cap": "none",
        },
        "po_box": {"state_of": "any"},
        "person": {},
        "pool_text": {"pool": "thank_you"},
        "survey_url": {},
        "bank_account": {"account_length": 8},
        "bpay": {"biller_length": 5, "reference_length": 10},
        "pick": {"values": ["PAID", "PAID IN FULL", "RECEIVED"]},
    }
    for name, params in rules.items():
        c = ctx(selected={"DX_SURVEY_CODE": "12345678"})
        c.states["payment"] = "unpaid"
        assert all(values.draw_values(c, {"type": name, **params}))
    c = bank_ctx()
    out = RULES["annotated_rows"](c, {"type": "annotated_rows", "extra": [1, 3]})
    c.selected["DX_ANNOTATED_ROWS"] = out
    assert RULES["annotation_total"](c, {"type": "annotation_total"})
    assert RULES["row_marks"](c, {"type": "row_marks", "marks": ["tick", "circle"]})
    assert RULES["link_date"](ctx(case_id="CASE001"), {"type": "link_date"})
    assert (
        set(rules) | {"annotated_rows", "annotation_total", "row_marks", "link_date"} == values.RULE_TYPES
    )


# --- carried fixes ------------------------------------------------------------------------------


def test_the_engine_exposes_a_read_only_blocklist() -> None:
    engine = _engine()
    assert isinstance(engine.blocklist, frozenset)
    assert "woolworths" in engine.blocklist
    with pytest.raises(AttributeError):
        engine.blocklist = frozenset()  # type: ignore[misc]


def test_values_reads_the_public_blocklist_only() -> None:
    source = (REPO_ROOT / "generators" / "distractors" / "values.py").read_text()
    assert "_blocklist" not in source.replace("def _blocklist", "")
    assert "engine.blocklist" in source


@pytest.mark.parametrize(
    ("low", "high", "token"),
    [
        ("-1.00", "5.00", "min"),
        ("1.00", "-5.00", "max"),
        ("1.005", "5.00", "min"),
        ("1.00", "5.001", "max"),
        ("abc", "5.00", "min"),
        ("1.00", "NaN", "max"),
        ("9.00", "5.00", "min"),
    ],
)
def test_amount_bounds_are_validated(low: str, high: str, token: str) -> None:
    with pytest.raises(ValueRuleError) as err:
        RULES["amount"](ctx(), {"type": "amount", "min": low, "max": high})
    assert_diagnostic_error(err.value)
    assert f"rule.{token}" in str(err.value)


def test_amount_formats_by_quantize_and_never_leaves_the_bounds() -> None:
    assert RULES["amount"](ctx(), {"type": "amount", "min": "1.5", "max": "1.5"}) == "1.50"
    assert RULES["amount"](ctx(), {"type": "amount", "min": "0.05", "max": "0.05"}) == "0.05"
    assert RULES["amount"](ctx(), {"type": "amount", "min": 3, "max": 3}) == "3.00"
    for seed in range(50):
        value = RULES["amount"](ctx(seed=seed), {"type": "amount", "min": "0.01", "max": "0.09"})
        assert re.fullmatch(r"0\.0[1-9]", value)


def _blocked_engine(monkeypatch: pytest.MonkeyPatch, method: str, value: object) -> RuleContext:
    engine = ContentEngine(_engine().pools)
    monkeypatch.setattr(engine, method, lambda rng, *state: value)
    return RuleContext(random.Random(1), {}, engine, engine.pools, "CASE999", None, {})


def test_a_person_naming_a_real_business_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    real = _engine().pools["retailers"][0]["name"]
    c = _blocked_engine(monkeypatch, "person", {"full_name": f"{real} Smith"})
    with pytest.raises(ValueRuleError) as err:
        RULES["person"](c, {"type": "person"})
    assert_diagnostic_error(err.value)
    assert real in str(err.value)


def test_an_address_naming_a_real_business_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    real = _engine().pools["real_name_blocklist_extra"][0]
    c = _blocked_engine(monkeypatch, "address", f"12 {real} St, Unley SA 5061")
    with pytest.raises(ValueRuleError) as err:
        RULES["address"](c, {"type": "address", "state_of": "any"})
    assert_diagnostic_error(err.value)
    assert real in str(err.value)


def test_a_date_offset_crosses_the_year_end() -> None:
    c = ctx(fields={"INVOICE_DATE": "31/12/2031"})
    assert (
        RULES["date_offset"](c, {"type": "date_offset", "from": "INVOICE_DATE", "days": [1]})
        == "01/01/2032"
    )


def test_a_rule_failure_carries_the_ground_truth_field() -> None:
    with pytest.raises(ValueRuleError) as err:
        RULES["website"](ctx(fields={"SUPPLIER_NAME": ""}), {"type": "website"})
    assert err.value.field == "SUPPLIER_NAME"
    with pytest.raises(ValueRuleError) as err:
        RULES["digits"](ctx(), {"type": "digits", "length": 6})
    assert err.value.field is None


# --- multi-value rules and industry-keyed pools -----------------------------------------------------


def test_bank_account_and_bpay_draw_a_pair_each() -> None:
    bsb, account = values.MULTI_VALUE_RULES["bank_account"](
        ctx(), {"type": "bank_account", "account_length": 8}
    )
    assert re.fullmatch(r"[1-9]\d{2}-\d{3}", bsb) and re.fullmatch(r"[1-9]\d{7}", account)
    biller, reference = values.MULTI_VALUE_RULES["bpay"](
        ctx(), {"type": "bpay", "biller_length": 5, "reference_length": 10}
    )
    assert re.fullmatch(r"[1-9]\d{4}", biller) and re.fullmatch(r"[1-9]\d{9}", reference)
    assert values.rule_arity("bank_account") == values.rule_arity("bpay") == 2
    assert values.rule_arity("digits") == 1
    for name, params in (
        ("bank_account", {"account_length": 8}),
        ("bpay", {"biller_length": 5, "reference_length": 10}),
    ):
        rule = {"type": name, **params}
        assert values.draw_values(ctx(seed=5), rule) == values.draw_values(ctx(seed=5), rule)
        assert values.draw_values(ctx(seed=5), rule) != values.draw_values(ctx(seed=6), rule)


def test_the_category_is_the_stored_one_not_the_names() -> None:
    """A legal-sounding name stored as plumbing prints plumbing's text: the stored category decides."""
    c = ctx(fields=LEGAL, service_category="plumbing")
    value = RULES["pool_text"](c, {"type": "pool_text", "pool": "tagline"})
    assert value in _engine().pools["distractor_text"]["tagline"]["plumbing_gas"]
    assert values.case_category("plumbing", _engine().pools, "CASE999") == "plumbing"


@pytest.mark.parametrize("stored", [None, "astrology", 3])
def test_an_invoice_without_a_valid_stored_category_fails_fast(stored: object) -> None:
    with pytest.raises(ValueRuleError) as err:
        RULES["pool_text"](
            ctx(fields=LEGAL, service_category=stored), {"type": "pool_text", "pool": "tagline"}
        )
    assert_diagnostic_error(err.value)
    assert "service_category" in str(err.value) and "CASE999" in str(err.value)
    assert "ground_truth/invoices.yml" in str(err.value)


def test_an_address_near_the_payer_is_drawn_in_the_payers_state() -> None:
    rule = {"type": "address", "state_of": "PAYER_ADDRESS"}
    for state, payer in (("SA", "4 Ockendon Ct, Unley SA 5061"), ("ACT", "9 Fenwick Pl, Manuka ACT 2603")):
        for seed in range(20):
            drawn = RULES["address"](ctx(seed=seed, fields={"PAYER_ADDRESS": payer}), rule)
            assert re.search(rf" {state} \d{{4}}$", drawn), (payer, drawn)
    # Unconstrained, the same seeds wander across states.
    anywhere = {
        RULES["address"](ctx(seed=seed), {"type": "address", "state_of": "any"}).rsplit(" ", 2)[1]
        for seed in range(20)
    }
    assert len(anywhere) > 2


def test_an_address_rule_whose_source_names_no_state_fails_fast() -> None:
    with pytest.raises(ValueRuleError) as err:
        RULES["address"](
            ctx(fields={"PAYER_ADDRESS": "Somewhere"}), {"type": "address", "state_of": "PAYER_ADDRESS"}
        )
    assert_diagnostic_error(err.value)
    assert err.value.field == "PAYER_ADDRESS"
    with pytest.raises(ValueRuleError) as err:
        RULES["address"](ctx(), {"type": "address"})
    assert_diagnostic_error(err.value)
    assert "state_of" in str(err.value)


def test_amount_ratio_scales_with_the_total() -> None:
    rule = {
        "type": "amount_ratio",
        "of": "TOTAL_AMOUNT",
        "min_ratio": "0.30",
        "max_ratio": "1.50",
        "element": "whole",
        "cap": "none",
    }
    for total in ("56.10", "216.89", "9046.55"):
        for seed in range(30):
            drawn = Decimal(RULES["amount_ratio"](ctx(seed=seed, fields={"TOTAL_AMOUNT": total}), rule))
            assert (
                Decimal(total) * Decimal("0.30") - Decimal("0.01")
                <= drawn
                <= Decimal(total) * Decimal("1.5")
            )
            assert re.fullmatch(r"\d+\.\d{2}", str(drawn))
    # A tiny total still yields a positive cent.
    assert RULES["amount_ratio"](ctx(fields={"TOTAL_AMOUNT": "0.01"}), rule) == "0.01"


@pytest.mark.parametrize(
    ("params", "token"),
    [
        ({"min_ratio": "0", "max_ratio": "1.50"}, "min_ratio"),
        ({"min_ratio": "abc", "max_ratio": "1.50"}, "min_ratio"),
        ({"min_ratio": "0.30", "max_ratio": "-1"}, "max_ratio"),
        ({"min_ratio": 0.3, "max_ratio": "1.50"}, "min_ratio"),
    ],
)
def test_amount_ratio_bounds_are_validated(params: dict, token: str) -> None:
    with pytest.raises(ValueRuleError) as err:
        RULES["amount_ratio"](
            ctx(),
            {"type": "amount_ratio", "of": "TOTAL_AMOUNT", "element": "whole", "cap": "none", **params},
        )
    assert_diagnostic_error(err.value)
    assert token in str(err.value)


def test_amount_ratio_refuses_a_total_that_is_not_positive() -> None:
    rule = {
        "type": "amount_ratio",
        "of": "TOTAL_AMOUNT",
        "min_ratio": "0.30",
        "max_ratio": "1.50",
        "element": "whole",
        "cap": "none",
    }
    with pytest.raises(ValueRuleError) as err:
        RULES["amount_ratio"](ctx(fields={"TOTAL_AMOUNT": "0.00"}), rule)
    assert_diagnostic_error(err.value)
    assert err.value.field == "TOTAL_AMOUNT"


def test_an_unmapped_category_fails_fast() -> None:
    pools = copy.deepcopy(_engine().pools)
    del pools["category_industries"]["legal"]
    c = RuleContext(
        random.Random(1),
        dict(LEGAL),
        ContentEngine(pools),
        pools,
        "CASE999",
        None,
        {},
        service_category="legal",
    )
    with pytest.raises(ValueRuleError) as err:
        RULES["pool_text"](c, {"type": "pool_text", "pool": "tagline"})
    assert_diagnostic_error(err.value)
    assert "category_industries.legal" in str(err.value)


def test_an_industry_with_no_sub_pool_fails_fast_at_draw_time() -> None:
    pools = copy.deepcopy(_engine().pools)
    del pools["distractor_text"]["tagline"]["legal_services"]
    c = RuleContext(
        random.Random(1),
        dict(LEGAL),
        ContentEngine(pools),
        pools,
        "CASE999",
        None,
        {},
        service_category="legal",
    )
    with pytest.raises(ValueRuleError) as err:
        RULES["pool_text"](c, {"type": "pool_text", "pool": "tagline"})
    assert_diagnostic_error(err.value)
    assert "distractor_text.tagline.legal_services" in str(err.value)


def _ratio_draws(rule: dict, total: str = "15000.00", fields: dict | None = None) -> list[Decimal]:
    return [
        Decimal(RULES["amount_ratio"](ctx(seed=s, fields=fields or {"TOTAL_AMOUNT": total}), rule))
        for s in range(40)
    ]


CAPPED = {
    "type": "amount_ratio",
    "of": "TOTAL_AMOUNT",
    "element": "whole",
    "min_ratio": "0.10",
    "max_ratio": "0.65",
    "cap": {"min": "400.00", "max": "999.00"},
}


def test_a_capped_amount_varies_within_its_cap_range_on_a_large_total() -> None:
    """Past the cap the figure is drawn within the declared range, never the cap itself every time."""
    drawn = _ratio_draws(CAPPED)
    assert Decimal("400.00") <= min(drawn) and max(drawn) <= Decimal("999.00")
    assert len(set(drawn)) > 20
    # A small total is untouched by the cap: tenth to two thirds of 300.00.
    small = _ratio_draws(CAPPED, "300.00")
    assert Decimal("30.00") <= min(small) and max(small) <= Decimal("195.00")
    # Without the cap the same draws run past 1,000: the cap is what holds them.
    assert max(_ratio_draws({**CAPPED, "cap": "none"})) > 1000


def test_clamping_to_the_cap_would_print_one_constant() -> None:
    """The old clamp (floor and ceiling both pinned to the cap) gives one figure; the range does not."""
    pinned = _ratio_draws({**CAPPED, "cap": {"min": "999.00", "max": "999.00"}})
    assert set(pinned) == {Decimal("999.00")}
    assert len(set(_ratio_draws(CAPPED))) > 1


@pytest.mark.parametrize("cap", ["999.00", "lots", {"min": "999.00"}, {"min": "9.00", "max": "1.00"}, None])
def test_a_malformed_cap_is_refused(cap: object) -> None:
    rule = {**CAPPED, "cap": cap}
    if cap is None:
        rule.pop("cap")
    with pytest.raises(ValueRuleError) as err:
        RULES["amount_ratio"](ctx(), rule)
    assert_diagnostic_error(err.value)
    assert "cap" in str(err.value)


def test_the_max_element_prices_on_the_dearest_line() -> None:
    """An upgrade offer is 1.2 to 2 times the dearest unit price billed, wherever that line prints."""
    rule = {
        **CAPPED,
        "of": "LINE_ITEM_PRICES",
        "element": "max",
        "min_ratio": "1.20",
        "max_ratio": "2.00",
        "cap": "none",
    }
    drawn = _ratio_draws(rule, fields={"LINE_ITEM_PRICES": "11.74|137.05|45.09"})
    assert Decimal("164.46") <= min(drawn) and max(drawn) <= Decimal("274.10")
    # The old `first` element is gone: an invoice's first line is drawn, so it need not be the plan.
    for element in ("first", "last"):
        with pytest.raises(ValueRuleError) as err:
            RULES["amount_ratio"](ctx(), {**rule, "element": element})
    assert_diagnostic_error(err.value)
    assert "element" in str(err.value)


def test_a_po_box_is_drawn_in_the_state_of_its_source_address() -> None:
    """A remittance PO Box is the supplier's own postal address: in BUSINESS_ADDRESS's state."""
    rule = {"type": "po_box", "state_of": "BUSINESS_ADDRESS"}
    for address, state in (("4 Kemp St, Unley SA 5061", "SA"), ("9 Lyle Pl, Braddon ACT 2612", "ACT")):
        for seed in range(20):
            drawn = RULES["po_box"](ctx(seed=seed, fields={"BUSINESS_ADDRESS": address}), rule)
            assert drawn.rsplit(" ", 2)[1] == state, (address, drawn)
    # Unbounded, the state wanders: the check can fail.
    states = {
        RULES["po_box"](ctx(seed=seed), {"type": "po_box", "state_of": "any"}).rsplit(" ", 2)[1]
        for seed in range(40)
    }
    assert len(states) > 1


def test_a_po_box_without_state_of_or_with_a_stateless_source_is_refused() -> None:
    with pytest.raises(ValueRuleError) as err:
        RULES["po_box"](ctx(), {"type": "po_box"})
    assert_diagnostic_error(err.value)
    assert "state_of" in str(err.value)
    with pytest.raises(ValueRuleError) as err:
        RULES["po_box"](
            ctx(fields={"BUSINESS_ADDRESS": "Somewhere"}),
            {"type": "po_box", "state_of": "BUSINESS_ADDRESS"},
        )
    assert_diagnostic_error(err.value)
    assert "BUSINESS_ADDRESS" in str(err.value) and "'po_box'" in str(err.value)


def test_the_remittance_po_box_is_declared_in_the_suppliers_state() -> None:
    from generators.distractors.config import load_distractor_config

    items = load_distractor_config().items
    assert items["in_remit_po_box"].rule["state_of"] == "BUSINESS_ADDRESS"
    assert items["in_branch_address"].rule["state_of"] == "any"
