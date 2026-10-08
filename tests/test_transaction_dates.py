"""Bank-statement transaction dates must be realistic and internally consistent.

Target state (all four directives):
1. A linked receipt/invoice and its bank transaction share the SAME date
   (the common real-world case: a purchase appears on the statement on its date).
2. Every transaction date falls within the statement's ~1-month period
   (all dates share one calendar month and year).
3. Each statement's transactions are in chronological (ascending) order.
4. STATEMENT_DATE_RANGE == earliest - latest of the date column.

Difficulty is defined by description quality (the date is always an exact
match): easy = full merchant name in the bank description, medium = an
abbreviation, hard = no merchant reference at all.

Reads the committed ground truth only; nothing is written.
"""

import copy
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from tests.conftest import REPO_ROOT

GT = REPO_ROOT / "ground_truth"


def _load(name: str) -> dict:
    return yaml.safe_load((GT / f"{name}.yml").read_text())


def _parse(value: str) -> datetime:
    return datetime.strptime(value, "%d/%m/%Y")


def _dates(entry: dict) -> list[str]:
    return entry["fields"]["TRANSACTION_DATES"].split("|")


@pytest.fixture(scope="module")
def banks() -> dict:
    return _load("bank_statements")


@pytest.fixture(scope="module")
def links() -> dict:
    return _load("transaction_links")


def _statement_for(banks: dict, image: str) -> dict:
    """Resolve a link's bank_statement image name (CASEnnn_<layout>.png) to its entry."""
    case_id, layout = Path(image).stem.split("_", 1)
    entry = banks[case_id]
    assert entry["layout"] == layout, f"{image}: statement layout is {entry['layout']}"
    return entry


def _source_entry(receipts: dict, invoices: dict, key: str) -> dict:
    """Resolve a link key (CASEnnn_<layout>.png) to the receipt or invoice entry it names."""
    case_id, layout = Path(key).stem.split("_", 1)
    pool = receipts if layout.startswith("receipt_") else invoices
    entry = pool[case_id]
    assert entry["layout"] == layout, f"{key}: layout is {entry['layout']}"
    return entry


def _all_links(links: dict) -> list[tuple[str, dict]]:
    return [(key, link) for key, group in links.items() for link in group]


def _order_problems(entry: dict) -> list[str]:
    """Rule 3: report a statement whose transaction dates are not ascending."""
    parsed = [_parse(d) for d in _dates(entry)]
    return [] if parsed == sorted(parsed) else ["transaction dates are not ascending"]


def _range_problems(entry: dict) -> list[str]:
    """Rule 4: report a STATEMENT_DATE_RANGE that is not earliest - latest of the date column."""
    parsed = [_parse(d) for d in _dates(entry)]
    start, end = (_parse(p.strip()) for p in entry["fields"]["STATEMENT_DATE_RANGE"].split(" - "))
    problems = []
    if start != min(parsed):
        problems.append(f"range starts {start:%d/%m/%Y}, earliest transaction is {min(parsed):%d/%m/%Y}")
    if end != max(parsed):
        problems.append(f"range ends {end:%d/%m/%Y}, latest transaction is {max(parsed):%d/%m/%Y}")
    return problems


def _month_problems(entry: dict) -> list[str]:
    """Rule 2: report a statement whose dates do not share one calendar month and year."""
    months = {(_parse(d).year, _parse(d).month) for d in _dates(entry)}
    return [] if len(months) == 1 else [f"dates span {sorted(months)}"]


def _link_date_problems(link: dict) -> list[str]:
    """Rule 1: report a link whose receipt date differs from its bank row's date."""
    if link["receipt_date"] == link["bank_date"]:
        return []
    return [f"receipt_date {link['receipt_date']} != bank_date {link['bank_date']}"]


def _statement(dates: list[str], date_range: str) -> dict:
    """An invented in-memory statement entry (years 2031+, CASE9xx ids are used by callers)."""
    return {"fields": {"TRANSACTION_DATES": "|".join(dates), "STATEMENT_DATE_RANGE": date_range}}


def test_bank_transaction_dates_are_chronological(banks):
    """Every statement lists its transactions in ascending date order."""
    assert banks
    for case_id, entry in banks.items():
        assert _order_problems(entry) == [], case_id


def test_statement_date_range_equals_earliest_to_latest(banks):
    """STATEMENT_DATE_RANGE is the earliest and latest date of the date column."""
    assert banks
    for case_id, entry in banks.items():
        assert _range_problems(entry) == [], case_id


def test_all_transaction_dates_fall_within_one_month(banks):
    """All of a statement's transactions share one calendar month and year."""
    assert banks
    for case_id, entry in banks.items():
        assert _month_problems(entry) == [], case_id


def test_linked_receipt_and_bank_dates_are_identical(links):
    """A link's receipt_date equals its bank_date for every link."""
    assert links
    for key, link in _all_links(links):
        assert _link_date_problems(link) == [], key


def test_order_rule_reports_an_out_of_order_statement():
    """CASE901 lists 20/03/2031 before 05/03/2031, which the order rule must report."""
    bad = _statement(["20/03/2031", "05/03/2031"], "05/03/2031 - 20/03/2031")
    good = _statement(["05/03/2031", "20/03/2031"], "05/03/2031 - 20/03/2031")
    assert _order_problems(bad) == ["transaction dates are not ascending"]
    assert _order_problems(good) == []


def test_range_rule_reports_a_range_that_is_not_earliest_to_latest():
    """CASE902's range starts on the 1st and ends on the 31st but the rows run 04th to 18th."""
    bad = _statement(["04/05/2032", "18/05/2032"], "01/05/2032 - 31/05/2032")
    good = _statement(["04/05/2032", "18/05/2032"], "04/05/2032 - 18/05/2032")
    problems = _range_problems(bad)
    assert len(problems) == 2
    assert "starts 01/05/2032" in problems[0] and "ends 31/05/2032" in problems[1]
    assert _range_problems(good) == []


def test_month_rule_reports_a_date_in_another_month():
    """CASE903 has one row in April 2033 among May 2033 rows, which the month rule must report."""
    bad = _statement(["30/04/2033", "05/05/2033"], "30/04/2033 - 05/05/2033")
    good = _statement(["02/05/2033", "05/05/2033"], "02/05/2033 - 05/05/2033")
    assert len(_month_problems(bad)) == 1
    assert _month_problems(good) == []
    # Same month number in a different year is also a violation.
    assert len(_month_problems(_statement(["05/05/2033", "05/05/2034"], "05/05/2033 - 05/05/2034"))) == 1


def test_link_date_rule_reports_a_receipt_date_differing_from_its_bank_row():
    """CASE904's receipt is dated 11/06/2034 but its bank row is 12/06/2034."""
    bad = {"receipt_date": "11/06/2034", "bank_date": "12/06/2034"}
    good = {"receipt_date": "11/06/2034", "bank_date": "11/06/2034"}
    assert len(_link_date_problems(bad)) == 1
    assert _link_date_problems(good) == []


def test_link_dates_match_the_underlying_ground_truth(banks, links):
    """The link's date must be the receipt/invoice date AND appear on the named statement.

    It must sit against the linked amount; otherwise the link is unscoreable.
    """
    assert links
    receipts, invoices = _load("receipts"), _load("invoices")
    for key, link in _all_links(links):
        source = _source_entry(receipts, invoices, key)["fields"]
        assert link["receipt_date"] == source["INVOICE_DATE"], key
        assert link["receipt_total"] == source["TOTAL_AMOUNT"], key

        bank = _statement_for(banks, link["bank_statement"])["fields"]
        rows = zip(
            bank["TRANSACTION_DATES"].split("|"),
            bank["TRANSACTION_AMOUNTS_PAID"].split("|"),
            bank["TRANSACTION_DESCRIPTIONS"].split("|"),
            strict=True,
        )
        assert (link["bank_date"], link["bank_amount"], link["bank_description"]) in set(rows), (
            f"{key}: no row {link['bank_date']} / {link['bank_amount']} on {link['bank_statement']}"
        )


def _oracle_predictions(banks: dict, links: dict) -> dict[str, dict]:
    """Link each source document by its own date and total to its case's statement row, as a reader would."""
    receipts, invoices = _load("receipts"), _load("invoices")
    predictions = {}
    for key in links:
        source = _source_entry(receipts, invoices, key)["fields"]
        case_id = Path(key).stem.split("_", 1)[0]
        statement = banks[case_id]
        rows = zip(
            statement["fields"]["TRANSACTION_DATES"].split("|"),
            statement["fields"]["TRANSACTION_AMOUNTS_PAID"].split("|"),
            strict=True,
        )
        if (source["INVOICE_DATE"], source["TOTAL_AMOUNT"]) in set(rows):
            predictions[key] = {
                "bank_statement": f"{case_id}_{statement['layout']}.png",
                "bank_amount": source["TOTAL_AMOUNT"],
            }
    return predictions


def test_links_are_complete_and_every_difficulty_is_present(banks, links):
    """Review Focus 4: 110 links, every one recoverable from the documents (F1 1.0), all levels present.

    The exact easy/medium/hard split is the seed's (52/36/22 today) and is not pinned, so a reseed may
    move it; a level vanishing, or a link no reader could recover, fails here.
    """
    from linking.link_validator import validate_links

    assert len(_all_links(links)) == 110
    counts = Counter(link["match_difficulty"] for _, link in _all_links(links))
    assert set(counts) == {"easy", "medium", "hard"} and all(counts.values()), counts
    score = validate_links(links, _oracle_predictions(banks, links))
    assert score.f1 == 1.0, (score.true_positives, score.false_negatives, score.false_positives)
    assert all(level.f1 == 1.0 for level in score.by_difficulty.values())
    # The check can fail: a statement whose linked row lost its amount scores below 1.0.
    key, link = _all_links(links)[0]
    case_id = Path(key).stem.split("_", 1)[0]
    doctored = {**banks, case_id: copy.deepcopy(banks[case_id])}
    fields = doctored[case_id]["fields"]
    fields["TRANSACTION_AMOUNTS_PAID"] = fields["TRANSACTION_AMOUNTS_PAID"].replace(
        str(link["bank_amount"]), "0.00"
    )
    assert validate_links(links, _oracle_predictions(doctored, links)).f1 < 1.0


def test_description_quality_matches_difficulty(links):
    """Easy means the full merchant name is present; medium only an abbreviation; hard no merchant."""
    assert links
    for key, link in _all_links(links):
        supplier = link["supplier"].upper()
        first_word = supplier.split()[0][:10]
        description = link["bank_description"].upper()
        difficulty = link["match_difficulty"]
        if difficulty == "easy":
            assert supplier in description, f"{key}: full merchant name absent on an easy link"
        elif difficulty == "medium":
            assert supplier not in description, f"{key}: full name present on a medium link"
            assert first_word in description, f"{key}: no abbreviation on a medium link"
        else:
            assert first_word not in description, f"{key}: merchant reference on a hard link"
