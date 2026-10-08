"""Loader-coverage test: the real config/data_pools.yml satisfies content_engine's
required core keys and has the expected shape (local-only; tests/ is gitignored).
"""

import copy
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from generators.content_engine import load_pools
from generators.payment_block import load_pos_pools
from tests.conftest import REPO_ROOT, assert_diagnostic_error

POOLS_PATH = REPO_ROOT / "config" / "data_pools.yml"

# The three-letter acronym the project never writes, assembled so this file does not spell it.
_FORBIDDEN_ACRONYM = "".join(("A", "T", "O"))

_STAFF_NAMES = [
    "Sarah",
    "James",
    "Emma",
    "Liam",
    "Olivia",
    "Noah",
    "Chloe",
    "Jack",
    "Mia",
    "Ethan",
    "Ava",
    "Will",
    "Sophie",
    "Ben",
    "Isla",
    "Tom",
]


@pytest.fixture(scope="module")
def pools() -> dict:
    """The real pools, loaded through the validating loader."""
    return load_pools(POOLS_PATH)


def _write_pos_pools(tmp_path: Path, mutate: Callable[[dict], None]) -> Path:
    """Copy the real pools file, apply `mutate` to pos_terminal, write it out.

    Each call writes to a fresh directory because `load_pos_pools` is cached per path.
    """
    data = copy.deepcopy(yaml.safe_load(POOLS_PATH.read_text()))
    mutate(data["pos_terminal"])
    directory = tmp_path / f"pools_{len(list(tmp_path.iterdir()))}"
    directory.mkdir()
    path = directory / "data_pools.yml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_real_data_pools_loads_without_error(pools):
    """The shipped file passes every required-key check."""
    assert pools["faker_config"]["locale"] == "en_AU"
    assert pools["faker_config"]["seed_base"] == 42


def test_business_name_parts_covers_every_retailer_and_service_category(pools):
    """Every retailer and professional-service category has at least three nouns to name from."""
    nouns = pools["business_name_parts"]["category_nouns"]
    for entry in pools["retailers"] + pools["professional_services"]:
        cat = entry["category"]
        assert cat in nouns, f"missing category_nouns entry for {cat}"
        assert len(nouns[cat]) >= 3, f"category_nouns[{cat}] has fewer than 3 nouns"


def test_product_and_service_catalogs_have_positive_price_ranges(pools):
    """Every catalogue item has price_low above zero and price_high not below price_low."""
    for catalogue in (pools["product_catalog"], pools["category_catalog"]):
        for item in (line for lines in catalogue.values() for line in lines):
            assert item["price_low"] > 0, item
            assert item["price_high"] >= item["price_low"], item


def test_bank_descriptions_grammar_never_embeds_the_forbidden_acronym(pools):
    """No bank-description template contains the forbidden acronym, in any case."""
    for direction in ("debit", "credit"):
        for name, spec in pools["bank_descriptions"][direction].items():
            assert _FORBIDDEN_ACRONYM not in spec["template"].upper(), f"{name}: {spec['template']}"


def test_retailers_and_professional_services_are_still_the_real_pools(pools):
    """The blocklist pools keep their real entries (20 retailers, 5 professional services)."""
    assert len(pools["retailers"]) == 20
    assert len(pools["professional_services"]) == 5


def test_deleted_pools_are_gone(pools):
    """Pools removed with the trust and account-holder content are not back."""
    for stale_key in ("account_holders", "transaction_patterns", "trust_names", "trustee_names"):
        assert stale_key not in pools


def test_receipt_and_service_categories_present_and_cover_category_nouns(pools):
    """The receipt/service category partition is explicit and each member has name nouns."""
    assert pools["receipt_categories"] == [
        "hardware",
        "grocery",
        "office",
        "electronics",
        "retail",
        "pharmacy",
        "liquor",
        "fuel",
        "automotive",
        "cafe",
    ]
    assert pools["service_categories"] == [
        "accounting",
        "legal",
        "it_services",
        "consulting",
        "marketing",
        "plumbing",
        "electrical",
        "conveyancing",
        "patent_attorneys",
        "food_wholesale",
        "cleaning_wholesale",
        "cloud_software",
        "security_monitoring",
    ]
    nouns = pools["business_name_parts"]["category_nouns"]
    for cat in pools["receipt_categories"] + pools["service_categories"]:
        assert cat in nouns, f"{cat} has no category_nouns entry"


def test_loads_real_pos_pools_file():
    """The real file carries every pos_terminal key the loader requires."""
    pos = load_pos_pools(POOLS_PATH)
    for key in (
        "staff_names",
        "hour_min",
        "hour_span",
        "register_min",
        "register_span",
        "receipt_number_prefix",
        "receipt_number_digest_length",
    ):
        assert key in pos, f"{key} missing from pos_terminal"


def test_pos_staff_names_are_byte_identical_to_the_legacy_pool_in_order():
    """The digest indexes into this list by position (`staff_idx = hash % len(staff_names)`),
    so a reordering, not just a membership change, would silently change every derived
    staff name.
    """
    assert load_pos_pools(POOLS_PATH)["staff_names"] == _STAFF_NAMES


def test_pos_windows_match_the_legacy_hardcoded_ranges():
    """08:00-19:59 for the hour window, register 01-08, and 'R-' plus 6 hex characters."""
    pos = load_pos_pools(POOLS_PATH)
    assert (pos["hour_min"], pos["hour_span"]) == (8, 12)
    assert (pos["register_min"], pos["register_span"]) == (1, 8)
    assert pos["receipt_number_prefix"] == "R-"
    assert pos["receipt_number_digest_length"] == 6


def test_pos_missing_required_key_is_four_element_diagnostic(tmp_path):
    """Dropping any one required key fails naming that key, with all four elements."""
    for key in load_pos_pools(POOLS_PATH):
        path = _write_pos_pools(tmp_path, lambda block, key=key: block.pop(key))
        with pytest.raises(ValueError) as exc_info:
            load_pos_pools(path)
        assert key in str(exc_info.value)
        assert_diagnostic_error(exc_info.value)


def test_pos_missing_pos_terminal_block_is_diagnostic(tmp_path):
    """A file with no pos_terminal block fails with a four-element diagnostic naming it."""
    data = yaml.safe_load(POOLS_PATH.read_text())
    del data["pos_terminal"]
    path = tmp_path / "data_pools.yml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError) as exc_info:
        load_pos_pools(path)
    assert "pos_terminal" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_pos_staff_names_empty_list_is_diagnostic(tmp_path):
    """An empty staff_names list is rejected."""
    path = _write_pos_pools(tmp_path, lambda block: block.update(staff_names=[]))
    with pytest.raises(ValueError) as exc_info:
        load_pos_pools(path)
    assert "staff_names" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_pos_staff_names_non_string_entry_is_diagnostic(tmp_path):
    """A non-string entry in staff_names is rejected."""
    path = _write_pos_pools(tmp_path, lambda block: block.update(staff_names=["Sarah", 7]))
    with pytest.raises(ValueError) as exc_info:
        load_pos_pools(path)
    assert "staff_names" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_pos_non_negative_int_key_rejects_a_negative_value(tmp_path):
    """hour_min and register_min accept zero upwards and reject a negative value."""
    for key in ("hour_min", "register_min"):
        ok = _write_pos_pools(tmp_path, lambda block, key=key: block.update({key: 0}))
        assert load_pos_pools(ok)[key] == 0
        bad = _write_pos_pools(tmp_path, lambda block, key=key: block.update({key: -1}))
        with pytest.raises(ValueError) as exc_info:
            load_pos_pools(bad)
        assert key in str(exc_info.value)
        assert_diagnostic_error(exc_info.value)


def test_pos_positive_int_key_rejects_zero(tmp_path):
    """hour_span, register_span and receipt_number_digest_length must be at least one."""
    for key in ("hour_span", "register_span", "receipt_number_digest_length"):
        path = _write_pos_pools(tmp_path, lambda block, key=key: block.update({key: 0}))
        with pytest.raises(ValueError) as exc_info:
            load_pos_pools(path)
        assert key in str(exc_info.value)
        assert_diagnostic_error(exc_info.value)


def test_pos_int_key_rejects_a_string_value(tmp_path):
    """An integer key holding a string (here '8') is rejected, not coerced."""
    for key in ("hour_min", "register_min", "hour_span", "register_span", "receipt_number_digest_length"):
        path = _write_pos_pools(tmp_path, lambda block, key=key: block.update({key: "8"}))
        with pytest.raises(ValueError) as exc_info:
            load_pos_pools(path)
        assert key in str(exc_info.value)
        assert_diagnostic_error(exc_info.value)


def test_pos_receipt_number_prefix_empty_string_is_diagnostic(tmp_path):
    """An empty receipt_number_prefix is rejected."""
    path = _write_pos_pools(tmp_path, lambda block: block.update(receipt_number_prefix=""))
    with pytest.raises(ValueError) as exc_info:
        load_pos_pools(path)
    assert "receipt_number_prefix" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_pos_missing_file_raises_file_not_found(tmp_path):
    """A nonexistent pools file raises FileNotFoundError with a four-element diagnostic."""
    with pytest.raises(FileNotFoundError) as exc_info:
        load_pos_pools(tmp_path / "nope.yml")
    assert_diagnostic_error(exc_info.value)
