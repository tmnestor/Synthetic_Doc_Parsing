"""Unit tests for generators/content_engine.py."""

import copy
import random
import re
from pathlib import Path

import pytest
import yaml

from generators.abn_register import AbnRegister
from generators.common import validate_abn
from generators.content_engine import (
    ContentEngine,
    NonRepeatingSampler,
    build_engine,
    load_pools,
    sample,
)
from tests.conftest import REPO_ROOT, assert_diagnostic_error

POOLS_PATH = REPO_ROOT / "config" / "data_pools.yml"
# No ABN is registered, so every first draw is kept.
_NO_REGISTER = AbnRegister.from_abns([])


def _real_pools() -> dict:
    """Return a fresh deep copy of the real pools so tests may mutate it freely."""
    return copy.deepcopy(yaml.safe_load(POOLS_PATH.read_text()))


def _write_pools(tmp_path: Path, pools: dict | None) -> Path:
    """Write `pools` (or an empty file when None) to a temporary data_pools.yml."""
    path = tmp_path / "data_pools.yml"
    path.write_text("" if pools is None else yaml.safe_dump(pools))
    return path


@pytest.fixture(scope="module")
def engine() -> ContentEngine:
    """An engine built from the real pools file."""
    return build_engine()


def test_load_pools_succeeds_with_all_required_keys(tmp_path):
    """A complete pools file loads and keeps its values."""
    pools = load_pools(_write_pools(tmp_path, _real_pools()))
    assert pools["faker_config"]["locale"] == "en_AU"
    assert pools["faker_config"]["seed_base"] == 42


def test_load_pools_fails_fast_on_missing_top_level_key(tmp_path):
    """A missing top-level key raises ValueError naming the key, with a four-element diagnostic."""
    pools = _real_pools()
    del pools["banks"]
    path = _write_pools(tmp_path, pools)
    with pytest.raises(ValueError) as exc_info:
        load_pools(path)
    assert "'banks'" in str(exc_info.value)
    assert str(path) in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_load_pools_fails_fast_on_missing_nested_key(tmp_path):
    """A missing nested key is reported by its dotted path."""
    pools = _real_pools()
    del pools["faker_config"]["locale"]
    with pytest.raises(ValueError) as exc_info:
        load_pools(_write_pools(tmp_path, pools))
    assert "faker_config.locale" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_load_pools_fails_fast_on_missing_file(tmp_path):
    """A nonexistent path raises FileNotFoundError with a four-element diagnostic."""
    missing = tmp_path / "does_not_exist.yml"
    with pytest.raises(FileNotFoundError) as exc_info:
        load_pools(missing)
    assert str(missing) in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_load_pools_fails_fast_on_empty_file(tmp_path):
    """An empty file is not a mapping and fails with a four-element diagnostic."""
    path = tmp_path / "empty.yml"
    path.write_text("")
    with pytest.raises(ValueError) as exc_info:
        load_pools(path)
    assert "empty or not a mapping" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_load_pools_fails_fast_on_non_mapping_nested_parent(tmp_path):
    """A parent that must hold sub-keys but is a scalar reports 'is not a YAML mapping'."""
    pools = _real_pools()
    pools["faker_config"] = "oops-not-a-mapping"
    with pytest.raises(ValueError) as exc_info:
        load_pools(_write_pools(tmp_path, pools))
    assert "is not a YAML mapping" in str(exc_info.value)
    assert "faker_config" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_sample_is_deterministic_for_same_seed():
    """Equal seeds draw equal sequences from the same pool."""
    pool = ["a", "b", "c", "d", "e"]
    first = [sample(random.Random(10), pool) for _ in range(5)]
    again = [sample(random.Random(10), pool) for _ in range(5)]
    assert first == again
    rng_a, rng_b = random.Random(20), random.Random(20)
    assert [sample(rng_a, pool) for _ in range(30)] == [sample(rng_b, pool) for _ in range(30)]
    assert all(item in pool for item in [sample(rng_a, pool) for _ in range(30)])


def test_sample_empty_pool_fails_fast():
    """Drawing from an empty pool raises a four-element diagnostic."""
    with pytest.raises(ValueError) as exc_info:
        sample(random.Random(1), [])
    assert_diagnostic_error(exc_info.value)


def test_non_repeating_sampler_visits_every_item_before_repeating():
    """Each pass of len(pool) draws is a permutation of the pool."""
    pool = ["a", "b", "c", "d"]
    sampler = NonRepeatingSampler(random.Random(1), pool)
    for _ in range(5):
        assert sorted(sampler.draw() for _ in range(len(pool))) == sorted(pool)


def test_non_repeating_sampler_reshuffles_on_exhaustion():
    """Later passes are not all the same fixed order: the pool is reshuffled on exhaustion."""
    pool = ["a", "b", "c"]
    sampler = NonRepeatingSampler(random.Random(2), pool)
    passes = {tuple(sampler.draw() for _ in range(3)) for _ in range(12)}
    assert len(passes) > 1
    assert all(sorted(p) == pool for p in passes)


def test_non_repeating_sampler_is_deterministic_for_same_seed():
    """Equal seeds produce equal draw sequences across several passes."""
    pool = ["a", "b", "c", "d", "e", "f"]
    one = NonRepeatingSampler(random.Random(99), pool)
    two = NonRepeatingSampler(random.Random(99), pool)
    assert [one.draw() for _ in range(24)] == [two.draw() for _ in range(24)]


def test_non_repeating_sampler_distribution_is_not_lockstep_across_seeds():
    """Samplers over one pool with different seeds do not walk the pool in lockstep."""
    pool = list(range(10))
    one = NonRepeatingSampler(random.Random(1), pool)
    two = NonRepeatingSampler(random.Random(2), pool)
    seq_one = [one.draw() for _ in range(20)]
    seq_two = [two.draw() for _ in range(20)]
    assert seq_one != seq_two
    assert sum(a == b for a, b in zip(seq_one, seq_two, strict=True)) < len(seq_one)


def test_non_repeating_sampler_empty_pool_fails_fast():
    """Constructing a sampler over an empty pool raises a four-element diagnostic."""
    with pytest.raises(ValueError) as exc_info:
        NonRepeatingSampler(random.Random(1), [])
    assert_diagnostic_error(exc_info.value)


def test_person_returns_en_au_name_shape(engine):
    """A person has non-empty first, last and full names, the last being 'first last'."""
    person = engine.person(random.Random(1))
    assert set(person) == {"first_name", "last_name", "full_name"}
    assert person["first_name"] and person["last_name"]
    assert person["full_name"] == f"{person['first_name']} {person['last_name']}"


def test_person_is_deterministic_for_same_seed(engine):
    """Equal rng seeds give equal people, whatever the engine's Faker did in between."""
    assert engine.person(random.Random(7)) == engine.person(random.Random(7))
    other = build_engine()
    assert engine.person(random.Random(7)) == other.person(random.Random(7))


def test_location_draws_from_the_locations_pool(engine):
    """Every drawn location is an entry of the locations pool."""
    rng = random.Random(3)
    for _ in range(20):
        assert engine.location(rng) in engine.pools["locations"]


def test_address_matches_expected_format(engine):
    """An address reads 'N Street Type, Suburb ST PPPP'."""
    street_types = "|".join(engine.pools["street_types"])
    pattern = rf"^\d+ .+ ({street_types}), .+ [A-Z]{{2,3}} \d{{4}}$"
    rng = random.Random(5)
    for _ in range(20):
        addr = engine.address(rng)
        assert re.match(pattern, addr), addr


def test_address_is_deterministic_for_same_seed(engine):
    """Equal seeds give equal addresses."""
    assert engine.address(random.Random(42)) == engine.address(random.Random(42))


def _blocklist(pools: dict) -> set[str]:
    return {
        n.lower()
        for n in (
            [r["name"] for r in pools["retailers"]]
            + [p["name"] for p in pools["professional_services"]]
            + pools["real_name_blocklist_extra"]
        )
    }


def test_fictional_business_never_emits_a_blocklisted_name(engine):
    """Across many draws in every category, no emitted name is a real business."""
    blocklist = _blocklist(engine.pools)
    rng = random.Random(11)
    for category in engine.pools["business_name_parts"]["category_nouns"]:
        for _ in range(300):
            assert engine.fictional_business(rng, category, _NO_REGISTER)["name"].lower() not in blocklist


def test_fictional_business_returns_a_valid_abn(engine):
    """A business has name, address, category and an ABN that passes the checksum."""
    biz = engine.fictional_business(random.Random(4), "grocery", _NO_REGISTER)
    assert set(biz) == {"name", "address", "abn", "category"}
    assert biz["category"] == "grocery"
    assert validate_abn(biz["abn"])
    assert re.fullmatch(r"\d{2} \d{3} \d{3} \d{3}", biz["abn"])


def test_fictional_business_unknown_category_fails_fast(engine):
    """An unknown category raises a four-element diagnostic naming it."""
    with pytest.raises(ValueError) as exc_info:
        engine.fictional_business(random.Random(2), "not_a_real_category", _NO_REGISTER)
    assert "not_a_real_category" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_fictional_business_exhausted_retries_fails_fast():
    """When every candidate is blocklisted the retry budget is exhausted with a diagnostic."""
    pools = _real_pools()
    pools["business_name_parts"]["surnames"] = ["Ashcroft"]
    pools["business_name_parts"]["suburb_prefixes"] = ["Metro"]
    pools["business_name_parts"]["category_nouns"]["hardware"] = ["Hardware"]
    pools["real_name_blocklist_extra"] = ["ashcroft hardware", "metro hardware"]
    with pytest.raises(RuntimeError) as exc_info:
        ContentEngine(pools).fictional_business(random.Random(9), "hardware", _NO_REGISTER)
    assert "retry budget" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_fictional_business_name_is_name_only_and_screened(engine):
    """fictional_business_name returns just a string, never on the blocklist."""
    blocklist = _blocklist(engine.pools)
    rng = random.Random(11)
    for _ in range(300):
        name = engine.fictional_business_name(rng, "hardware")
        assert isinstance(name, str)
        assert name.lower() not in blocklist
        assert not re.search(r"\d", name), f"name carries an address or ABN fragment: {name}"


def test_fictional_business_name_unknown_category_fails_fast(engine):
    """An unknown category fails with a four-element diagnostic."""
    with pytest.raises(ValueError) as exc_info:
        engine.fictional_business_name(random.Random(2), "nope")
    assert "nope" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_fictional_business_unchanged_by_name_only_refactor(engine):
    """fictional_business is the name draw followed by the address draw on one rng stream.

    The original golden values were not recoverable. The literals below are
    pinned from the code as it stands today, not recovered originals; they
    detect later drift in the name and address streams. The composition check
    (same name as the name-only method, then the address that follows it) is
    kept too. The ABN comes from the module-global RNG, so it is checked for
    validity rather than pinned.
    """
    biz = engine.fictional_business(random.Random(12), "hardware", _NO_REGISTER)
    assert biz["name"] == "Ravensdale Timber & Tools"
    assert biz["address"] == "365 Walker Rd, New Farm QLD 4005"
    rng = random.Random(12)
    expected_name = engine.fictional_business_name(rng, "hardware")
    expected_address = engine.address(rng)
    assert biz["name"] == expected_name
    assert biz["address"] == expected_address
    assert validate_abn(biz["abn"])
