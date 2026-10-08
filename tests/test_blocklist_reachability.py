"""No real business name may be reachable by the fictional-name grammar.

`fictional_business_name` composes `{surname | suburb_prefix} {category_noun}`
and rejects blocklisted candidates at runtime. That guard is the last line of
defence; `reachable_blocked_names` is the first, and `pipeline validate` calls
it so a pool edit that makes a real company emittable fails when the pool is
edited rather than when a corpus is seeded.

Today nothing on the blocklist is reachable (the nouns are trade phrases and
real brands are not shaped like that), so these tests pin a property that
currently holds with room to spare, and would break loudly the moment it
stopped.
"""

import copy
import random

import pytest
import yaml

from generators.content_engine import ContentEngine, reachable_blocked_names
from tests.conftest import REPO_ROOT


@pytest.fixture
def pools() -> dict:
    """A fresh copy of the real pools, safe to mutate."""
    return copy.deepcopy(yaml.safe_load((REPO_ROOT / "config" / "data_pools.yml").read_text()))


def test_no_blocklisted_name_is_reachable_from_the_real_pools(pools):
    """The shipped pools make no blocklisted name expressible by the grammar."""
    assert reachable_blocked_names(pools) == []


def test_a_colliding_pool_edit_is_detected(pools):
    """Adding 'Origin' + 'Energy' must surface Origin Energy as reachable."""
    pools["business_name_parts"]["suburb_prefixes"].append("Origin")
    pools["business_name_parts"]["category_nouns"]["fuel"].append("Energy")
    assert reachable_blocked_names(pools) == ["Origin Energy"]


def test_matching_is_case_insensitive(pools):
    """A lowercase pool entry must still be caught: the runtime guard lowercases too."""
    pools["business_name_parts"]["suburb_prefixes"].append("oRiGiN")
    pools["business_name_parts"]["category_nouns"]["fuel"].append("eNeRgY")
    assert reachable_blocked_names(pools) == ["Origin Energy"]


_REAL_POOLS = yaml.safe_load((REPO_ROOT / "config" / "data_pools.yml").read_text())
_REAL_NAMES = [e["name"] for e in _REAL_POOLS["retailers"] + _REAL_POOLS["professional_services"]]
_MULTI_WORD = [n for n in _REAL_NAMES if " " in n]
_SINGLE_WORD = [n for n in _REAL_NAMES if " " not in n]


def _compose(pools: dict, name: str) -> tuple[dict, str, str]:
    """Narrow the grammar so `name` is the only candidate it can emit.

    The name is split at its first space into prefix and noun, which is exactly
    how the grammar joins its parts. Built on the copy only; the YAML is untouched.
    """
    prefix, noun = name.split(" ", 1)
    parts = pools["business_name_parts"]
    parts["surnames"] = [prefix]
    parts["suburb_prefixes"] = [prefix]
    parts["category_nouns"] = {"hardware": [noun]}
    return pools, prefix, noun


def _without(pools: dict, name: str) -> dict:
    """Remove `name` from every blocklist source of a pools copy."""
    pools["retailers"] = [r for r in pools["retailers"] if r["name"] != name]
    pools["professional_services"] = [p for p in pools["professional_services"] if p["name"] != name]
    pools["real_name_blocklist_extra"] = [
        n for n in pools["real_name_blocklist_extra"] if n.lower() != name.lower()
    ]
    return pools


@pytest.mark.parametrize("name", _REAL_NAMES)
def test_every_retailer_and_service_name_is_in_the_runtime_blocklist(pools, name):
    """The engine's own screen lists every retailer and professional-service name."""
    assert name.lower() in ContentEngine(pools)._blocklist


@pytest.mark.parametrize("name", _MULTI_WORD)
def test_reachable_blocked_names_flags_every_real_name_when_made_reachable(pools, name):
    """Each retailer and service name, made reachable by a pool edit, is flagged first-line."""
    narrowed, _, _ = _compose(pools, name)
    assert reachable_blocked_names(narrowed) == [name]


@pytest.mark.parametrize("name", _MULTI_WORD)
def test_engine_screen_rejects_every_real_name_when_it_is_the_only_candidate(pools, name):
    """The runtime guard refuses each real name; with the name unlisted the same pool emits it."""
    narrowed, _, _ = _compose(copy.deepcopy(pools), name)
    with pytest.raises(RuntimeError, match="retry budget"):
        ContentEngine(narrowed).fictional_business_name(random.Random(1), "hardware")

    control = _without(narrowed, name)
    assert ContentEngine(control).fictional_business_name(random.Random(1), "hardware").lower() == (
        name.lower()
    )
    assert reachable_blocked_names(control) == []


@pytest.mark.parametrize("name", _SINGLE_WORD)
def test_single_word_real_names_cannot_be_composed_by_the_grammar(pools, name):
    """The grammar always joins two parts with a space, so one-word brands are unreachable."""
    parts = pools["business_name_parts"]
    parts["surnames"] = parts["suburb_prefixes"] = [name]
    parts["category_nouns"] = {"hardware": [name]}
    assert reachable_blocked_names(pools) == []


def test_a_near_miss_is_not_flagged(pools):
    """Only exact `{prefix} {noun}` matches count; resemblance is not reachability."""
    pools["business_name_parts"]["suburb_prefixes"].append("Origin")
    pools["business_name_parts"]["category_nouns"]["fuel"].append("Energies")
    assert reachable_blocked_names(pools) == []
