"""The subscription layout's direct-debit notice agrees with the page's payment state.

A paid page says the debit has been taken, an unpaid page that it is still to come, and no page carries
both the notice and a remittance slip (a customer on direct debit posts no slip). Both are declared in
config/distractors.yml: the notice's `state_text` rule keys its pool by the payment state, and the
`exclusive` set keeps one of the notice and the slip per page. Malformed declarations fail fast.
"""

import random
import re
from pathlib import Path

import pytest
import yaml

from generators.content_engine import ContentEngine, load_pools
from generators.distractors import providers
from generators.distractors.config import DISTRACTORS_PATH, DistractorConfigError, load_distractor_config
from generators.distractors.values import RuleContext, ValueRuleError, draw_values
from tests.conftest import assert_diagnostic_error
from tests.distractor_helpers import force_every_item, selected
from tests.invoice_fixtures import SEED_LINE_COUNTS, synthetic_entries
from tests.renderer_helpers import corpus_entries, layouts_for
from tests.test_invoice_distractors import GENERAL_LAYOUTS, force_state

# Synthetic entries have no transaction link in the links file; this gives each one, as the seed would,
# so a paid synthetic invoice's stamp date can be drawn.
pytestmark = pytest.mark.usefixtures("synthetic_invoice_links")

DOC = "invoice"
SUBSCRIPTION = "tax_invoice_subscription"
CONFIG = load_distractor_config()
POOLS = load_pools()
# Words that put a debit still to come; none may appear in a paid invoice's notice.
FUTURE = re.compile(r"\b(will|scheduled|ensure|before|to change|applies if)\b", re.I)


def _pages() -> dict[str, dict]:
    """Every synthetic subscription entry, and every corpus invoice moved onto the subscription layout."""
    pages = {**synthetic_entries(SUBSCRIPTION), **synthetic_entries(SUBSCRIPTION, SEED_LINE_COUNTS)}
    pages.update({cid: {**e, "layout": SUBSCRIPTION} for cid, e in corpus_entries(DOC).items()})
    return pages


def test_the_notice_pools_are_worded_for_their_state() -> None:
    texts = POOLS["distractor_text"]
    assert CONFIG.items["in_direct_debit"].rule == {
        "type": "state_text",
        "state": "payment",
        "pools": {"paid": "direct_debit_paid", "unpaid": "direct_debit_notice"},
    }
    assert not [t for t in texts["direct_debit_paid"] if FUTURE.search(t)]
    assert all(FUTURE.search(t) or "collected" in t for t in texts["direct_debit_notice"])
    assert not set(texts["direct_debit_paid"]) & set(texts["direct_debit_notice"])


@pytest.mark.parametrize("state", ["paid", "unpaid"])
def test_a_direct_debit_notice_is_worded_for_the_pages_payment_state(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    """No paid page says a debit is still to come, and no unpaid page says it has been taken."""
    force_every_item(monkeypatch)
    force_state(monkeypatch, state)
    pool = POOLS["distractor_text"]["direct_debit_paid" if state == "paid" else "direct_debit_notice"]
    layout = layouts_for(DOC)[SUBSCRIPTION]
    printed = 0
    for case_id, entry in _pages().items():
        notice = selected(entry, layout).get("DX_DIRECT_DEBIT")
        if notice is None:
            continue
        printed += 1
        assert notice in pool, (case_id, state, notice)
        if state == "paid":
            assert not FUTURE.search(notice), (case_id, notice)
    assert printed > 10


@pytest.mark.parametrize("forced", [False, True])
def test_no_page_prints_the_direct_debit_notice_beside_a_remittance_slip(
    monkeypatch: pytest.MonkeyPatch, forced: bool
) -> None:
    """A customer paying by direct debit posts no slip: the two never share a page, sampled or forced."""
    if forced:
        force_every_item(monkeypatch)
    layout = layouts_for(DOC)[SUBSCRIPTION]
    seen = {"notice": 0, "slip": 0}
    for case_id, entry in _pages().items():
        chosen = set(selected(entry, layout))
        assert not {"DX_DIRECT_DEBIT", "DX_REMITTANCE_TOTAL"} <= chosen, case_id
        assert "DX_REMIT_BARCODE" not in chosen or "DX_REMITTANCE_TOTAL" in chosen, case_id
        seen["notice"] += "DX_DIRECT_DEBIT" in chosen
        seen["slip"] += "DX_REMITTANCE_TOTAL" in chosen
    # Both members still print, on different pages.
    assert seen["notice"] and seen["slip"], seen


def test_the_exclusive_set_leaves_the_general_layouts_alone() -> None:
    """A layout listing one member of a set drops nothing: the general four list only the slip."""
    assert CONFIG.exclusive[DOC] == (("in_direct_debit", "in_remittance_total"),)
    for layout_id in GENERAL_LAYOUTS:
        items = layouts_for(DOC)[layout_id]["distractor_items"]
        for case_id in corpus_entries(DOC):
            assert not providers.exclusive_excluded(CONFIG, case_id, DOC, items), (layout_id, case_id)


def test_a_state_worded_rule_without_a_drawn_state_fails_fast() -> None:
    ctx = RuleContext(
        rng=random.Random(1),
        fields={},
        engine=ContentEngine(POOLS),
        pools=POOLS,
        case_id="CASE000",
        rows=None,
        selected={},
    )
    with pytest.raises(ValueRuleError) as err:
        draw_values(ctx, CONFIG.items["in_direct_debit"].rule)
    assert_diagnostic_error(err.value)
    assert "payment" in str(err.value) and "CASE000" in str(err.value)


def _refused(tmp_path: Path, data: dict, token: str) -> None:
    path = tmp_path / "distractors.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(path)
    assert_diagnostic_error(err.value)
    assert token in str(err.value), (token, str(err.value))


def _shipped() -> dict:
    return yaml.safe_load(DISTRACTORS_PATH.read_text())


@pytest.mark.parametrize(
    ("edit", "token"),
    [
        (lambda d: d.pop("exclusive"), "exclusive"),
        (lambda d: d["exclusive"].pop("receipt"), "receipt"),
        (lambda d: d.update(exclusive=["in_direct_debit"]), "exclusive"),
        (lambda d: d["exclusive"].update(invoice="in_direct_debit"), "exclusive.invoice"),
        (lambda d: d["exclusive"]["invoice"].append(["in_phone"]), "exclusive.invoice[1]"),
        (lambda d: d["exclusive"]["invoice"].append(["in_phone", "in_phone"]), "exclusive.invoice[1]"),
        (lambda d: d["exclusive"]["invoice"].append(["in_phone", "in_fax"]), "in_fax"),
        (lambda d: d["exclusive"]["invoice"].append(["in_phone", "rc_acn"]), "rc_acn"),
        (lambda d: d["exclusive"]["invoice"].append(["in_phone", "in_direct_debit"]), "already"),
        (lambda d: d["exclusive"].update(astrology=[]), "astrology"),
    ],
)
def test_a_malformed_exclusive_declaration_is_refused(tmp_path: Path, edit, token: str) -> None:  # noqa: ANN001
    data = _shipped()
    edit(data)
    _refused(tmp_path, data, token)


@pytest.mark.parametrize(
    ("rule", "token"),
    [
        ({"state": "mood"}, "items.in_direct_debit.rule.state"),
        ({"pools": {"paid": "direct_debit_paid"}}, "items.in_direct_debit.rule.pools"),
        ({"pools": {"paid": "direct_debit_paid", "unpaid": "no_such_pool"}}, "rule.pools"),
        ({"pools": {"paid": "direct_debit_paid", "unpaid": "tagline"}}, "rule.pools"),
        ({"pools": "direct_debit_paid"}, "rule.pools"),
    ],
)
def test_a_malformed_state_worded_rule_is_refused(tmp_path: Path, rule: dict, token: str) -> None:
    data = _shipped()
    data["items"]["in_direct_debit"]["rule"].update(rule)
    _refused(tmp_path, data, token)
