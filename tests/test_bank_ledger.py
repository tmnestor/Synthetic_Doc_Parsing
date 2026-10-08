"""Bank statement rows: each row's direction agrees with its description, the running balance of the
personal account stays at or above its floor, and the taxpayer's annotations claim only money out
(final review I1 and I2; Ruling 9)."""

import copy
import random
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import generators.bank_ledger as ledger
from generators.bank_ledger import (
    BankLedgerError,
    balance_floor,
    check_statement,
    classify,
    direction_prefixes,
    running_balances,
)
from generators.content_engine import load_pools
from generators.distractors.providers import draw_all_eligible
from generators.pipeline import app
from scripts import seed_ground_truth as seed
from scripts.seed_transaction_links import _link_description
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.distractor_helpers import selected
from tests.renderer_helpers import corpus_entries, layouts_for

POOLS = load_pools()
SOURCE = Path("/abs/ground_truth/bank_statements.yml")
DOC = "bank_statement"


def _statements() -> dict[str, dict]:
    return corpus_entries(DOC)


def _edit_row(fields: dict, index: int, *, description: str | None = None, swap: bool = False) -> dict:
    fields = dict(fields)
    descriptions = fields["TRANSACTION_DESCRIPTIONS"].split("|")
    paid = fields["TRANSACTION_AMOUNTS_PAID"].split("|")
    received = fields["TRANSACTION_AMOUNTS_RECEIVED"].split("|")
    if description is not None:
        descriptions[index] = description
    if swap:
        paid[index], received[index] = received[index], paid[index]
    fields["TRANSACTION_DESCRIPTIONS"] = "|".join(descriptions)
    fields["TRANSACTION_AMOUNTS_PAID"] = "|".join(paid)
    fields["TRANSACTION_AMOUNTS_RECEIVED"] = "|".join(received)
    return fields


# --- the corpus ----------------------------------------------------------------------------------------


def test_every_corpus_statement_is_coherent() -> None:
    """No row is printed against its description, and no running balance falls below the floor."""
    for case_id, entry in _statements().items():
        assert check_statement(case_id, entry["fields"], POOLS, source=SOURCE) == [], case_id


def test_salary_is_always_money_in_and_purchases_always_money_out() -> None:
    """The defect as the review counted it: no salary in the debit column, no purchase in the credit one."""
    purchases = ("EFTPOS ", "VISA DEBIT ", "MASTERCARD DEBIT ", "ATM WITHDRAWAL ")
    for entry in _statements().values():
        fields = entry["fields"]
        for description, paid in zip(
            fields["TRANSACTION_DESCRIPTIONS"].split("|"),
            fields["TRANSACTION_AMOUNTS_PAID"].split("|"),
            strict=True,
        ):
            if description.startswith("SALARY "):
                assert paid == "NOT_FOUND", description
            if description.startswith(purchases):
                assert paid != "NOT_FOUND", description


def test_the_lowest_balance_sits_within_the_drawn_margin_above_the_floor() -> None:
    floor = balance_floor(POOLS)
    low, high = POOLS["bank_balance"]["lowest_above_floor"]
    for case_id, entry in _statements().items():
        lowest = min(running_balances(entry["fields"]))
        assert floor + Decimal(str(low)) <= lowest <= floor + Decimal(str(high)), case_id


def test_every_template_and_every_linked_description_reads_as_its_direction() -> None:
    """The pools and the classifier agree, and so do the link seeder's linked rows (all money out)."""
    prefixes = direction_prefixes(POOLS)
    fill = {
        "merchant": "ALPHA",
        "location": "Town",
        "last4": "1234",
        "biller": "ALPHA",
        "crn": 1,
        "ref": "R1",
        "mhf": 1,
        "name": "Kim",
        "savings": "4321",
    }
    for direction in ("debit", "credit"):
        for kind, spec in POOLS["bank_descriptions"][direction].items():
            assert classify(spec["template"].format(**fill), prefixes) == direction, kind
    rng = random.Random(0)
    for is_invoice in (False, True):
        for difficulty in ("easy", "medium", "hard"):
            for _ in range(20):
                text = _link_description("Alpha Beta Supplies", "Town", is_invoice, difficulty, rng)
                assert classify(text, prefixes) == "debit", text


def test_the_pool_sizes_keep_receipts_and_invoices_where_they_were() -> None:
    """Eight debit and four credit kinds: a power-of-two choice draws the generator exactly as the old
    eight-template choice did, which is why this reseed moved no receipt or invoice. A pool edit that
    changes a size reshuffles the whole corpus; this pins the sizes so that happens only on purpose."""
    assert len(POOLS["bank_descriptions"]["debit"]) == 8
    assert len(POOLS["bank_descriptions"]["credit"]) == 4


# --- refusals ------------------------------------------------------------------------------------------


def test_a_salary_in_the_debit_column_is_refused() -> None:
    case_id, entry = next(iter(_statements().items()))
    paid = entry["fields"]["TRANSACTION_AMOUNTS_PAID"].split("|")
    index = next(i for i, p in enumerate(paid) if p != "NOT_FOUND")
    fields = _edit_row(entry["fields"], index, description="SALARY ALPHA PAYROLL REF10001")
    errors = check_statement(case_id, fields, POOLS, source=SOURCE)
    assert len(errors) == 1 and f"row {index} " in errors[0] and "reads as credit" in errors[0]
    assert_diagnostic_error(errors[0])


def test_a_purchase_in_the_credit_column_is_refused() -> None:
    case_id, entry = next(iter(_statements().items()))
    received = entry["fields"]["TRANSACTION_AMOUNTS_RECEIVED"].split("|")
    index = next(i for i, r in enumerate(received) if r != "NOT_FOUND")
    fields = _edit_row(entry["fields"], index, description="EFTPOS ALPHA Town AUS")
    errors = check_statement(case_id, fields, POOLS, source=SOURCE)
    assert any(f"row {index} " in e and "reads as debit" in e for e in errors)
    assert_diagnostic_error(errors[0])


def test_an_unrecognised_description_is_refused() -> None:
    case_id, entry = next(iter(_statements().items()))
    fields = _edit_row(entry["fields"], 0, description="SOMETHING ELSE")
    errors = check_statement(case_id, fields, POOLS, source=SOURCE)
    assert errors and "reads as neither" in errors[0]


def test_an_overdrawn_statement_is_refused() -> None:
    case_id, entry = next(iter(_statements().items()))
    fields = dict(entry["fields"])
    lowest = min(running_balances(fields))
    fields["ACCOUNT_BALANCE"] = f"{Decimal(fields['ACCOUNT_BALANCE']) - lowest - Decimal('25.00'):.2f}"
    errors = check_statement(case_id, fields, POOLS, source=SOURCE)
    assert len(errors) == 1 and "below the floor" in errors[0] and "-25.00" in errors[0]
    assert_diagnostic_error(errors[0])


def test_overlapping_prefixes_are_refused() -> None:
    pools = copy.deepcopy(POOLS)
    pools["bank_descriptions"]["direction_prefixes"]["credit"].append("DIRECT ")
    with pytest.raises(BankLedgerError) as caught:
        direction_prefixes(pools)
    assert_diagnostic_error(caught.value)


@pytest.mark.parametrize(
    "edit",
    [
        lambda b: b.pop("floors"),
        lambda b: b.update(account_kind="business"),
        lambda b: b.update(lowest_above_floor=[500, 100]),
    ],
)
def test_a_malformed_balance_rule_is_refused(edit) -> None:  # type: ignore[no-untyped-def]
    pools = copy.deepcopy(POOLS)
    edit(pools["bank_balance"])
    with pytest.raises(BankLedgerError) as caught:
        ledger.lowest_above_floor(pools)
    assert_diagnostic_error(caught.value)


# --- the seed ------------------------------------------------------------------------------------------


def test_the_seed_refuses_a_pool_that_describes_money_in_as_money_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pools = copy.deepcopy(POOLS)
    pools["bank_descriptions"]["credit"]["salary"]["template"] = "EFTPOS {biller} {location} AUS"
    real = seed.build_engine

    def engine():  # type: ignore[no-untyped-def]
        built = real()
        built.pools = pools
        return built

    monkeypatch.setattr(seed, "build_engine", engine)
    with pytest.raises(RuntimeError) as caught:
        seed.build_all_entries()
    assert "reads as debit" in str(caught.value)
    assert_diagnostic_error(caught.value)


def test_the_seed_refuses_a_statement_below_the_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """The seed draws the opening balance against its floor; checked against a higher floor (as a
    validate rule tightened after seeding would be), the same statements are refused."""
    monkeypatch.setattr(ledger, "balance_floor", lambda pools: Decimal("100000.00"))
    with pytest.raises(RuntimeError) as caught:
        seed.build_all_entries()
    assert "below the floor 100000.00" in str(caught.value)
    assert_diagnostic_error(caught.value)


def test_validate_refuses_an_incoherent_statement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    statements = yaml.safe_load((REPO_ROOT / "ground_truth/bank_statements.yml").read_text())
    case_id = next(iter(statements))
    entry = statements[case_id]
    received = entry["fields"]["TRANSACTION_AMOUNTS_RECEIVED"].split("|")
    index = next(i for i, r in enumerate(received) if r != "NOT_FOUND")
    entry["fields"] = _edit_row(entry["fields"], index, swap=True)
    ground_truth = tmp_path / "bank_statements.yml"
    ground_truth.write_text(yaml.safe_dump({case_id: entry}, sort_keys=False))
    config = {
        "output_dir": str(tmp_path / "output"),
        "derived_dir": str(tmp_path / "derived"),
        "ground_truth_dir": str(REPO_ROOT / "ground_truth"),
        "distractor_output_subdir": "distractor",
        "labelled_varied_output_subdir": "labelled_varied",
        "document_types": {
            "bank_statements": {
                "ground_truth": str(ground_truth),
                "layouts": str(REPO_ROOT / "config/layouts/bank_statements.yml"),
                "output_subdir": "bank_statements",
                "generate_clean": True,
            }
        },
    }
    config_path = tmp_path / "generation_config.yml"
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.chdir(REPO_ROOT)
    result = CliRunner().invoke(app, ["validate", "--config", str(config_path)])
    assert result.exit_code == 1, result.output
    assert "printed against their descriptions" in result.output


# --- the annotations claim money out only (A.4) -----------------------------------------------------------


def _annotations(values: dict[str, str]) -> tuple[list[int], str | None]:
    rows = [int(i) for i in values.get("DX_ANNOTATED_ROWS", "").split("|") if i]
    return rows, values.get("DX_ANNOTATION_TOTAL")


def _assert_debits_and_total(case_id: str, fields: dict, rows: list[int], total: str | None) -> None:
    prefixes = direction_prefixes(POOLS)
    descriptions = fields["TRANSACTION_DESCRIPTIONS"].split("|")
    paid = fields["TRANSACTION_AMOUNTS_PAID"].split("|")
    received = fields["TRANSACTION_AMOUNTS_RECEIVED"].split("|")
    for i in rows:
        assert paid[i] != "NOT_FOUND" and received[i] == "NOT_FOUND", (case_id, i)
        assert classify(descriptions[i], prefixes) == "debit", (case_id, i, descriptions[i])
    if total is not None:
        assert Decimal(total) == sum((Decimal(paid[i]) for i in rows), Decimal("0")), case_id


def test_no_annotated_row_is_a_credit_and_every_total_is_its_rows_sum() -> None:
    """Over the corpus as rendered: every highlighted row is money out, described as money out, and the
    handwritten total is the sum of exactly the highlighted rows."""
    annotated = 0
    for case_id, entry in _statements().items():
        values = selected(entry, layouts_for(DOC)[entry["layout"]])
        rows, total = _annotations(values)
        if rows:
            annotated += 1
            _assert_debits_and_total(case_id, entry["fields"], rows, total)
    assert annotated >= 40


def test_with_every_item_drawn_no_annotated_row_is_a_credit() -> None:
    for case_id, entry in _statements().items():
        rows, total = _annotations(draw_all_eligible(entry, "bank_statement"))
        assert rows, case_id
        _assert_debits_and_total(case_id, entry["fields"], rows, total)
