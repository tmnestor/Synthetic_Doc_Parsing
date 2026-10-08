"""The label_styles provider maps a choice onto LABEL_ keys, and registers as its own kind (Task 1)."""

import pytest

from generators.labels.config import load_label_config
from generators.labels.keys import LabelChoice, LabelPick, label_key
from generators.labels.provider import PROVIDER_NAME, label_styles
from generators.layout_dsl.field_providers import (
    FieldProviderError,
    apply_field_providers,
    field_provider,
    field_provider_emits,
    get_label_provider,
    get_ordinary_provider,
    is_distractor_provider,
    is_label_provider,
)
from tests.conftest import assert_diagnostic_error

SITES = {
    "BUSINESS_ABN": {"labelled": "ABN: ", "pool": "abn_receipt"},
    "GST_AMOUNT": {"labelled": "GST", "pool": "gst_receipt"},
    "TOTAL_AMOUNT": {"labelled": "TOTAL", "pool": "total_receipt"},
}
PARAMS = {"sites": SITES}
ENTRY = {"case_id": "CASE900", "layout": "x", "fields": {}}


def test_no_choice_emits_every_sites_own_wording() -> None:
    assert label_styles(ENTRY, PARAMS, choice=None) == {
        "LABEL_BUSINESS_ABN": "ABN: ",
        "LABEL_GST_AMOUNT": "GST",
        "LABEL_TOTAL_AMOUNT": "TOTAL",
    }


@pytest.mark.parametrize("field", list(SITES))
@pytest.mark.parametrize(
    ("pick", "expected"),
    [
        (LabelPick("labelled"), None),
        (LabelPick("variant", "Amount due:"), "Amount due:"),
        (LabelPick("unlabelled"), ""),
    ],
)
def test_each_style_forced_per_field_gives_its_key(
    field: str, pick: LabelPick, expected: str | None
) -> None:
    emitted = label_styles(ENTRY, PARAMS, choice=LabelChoice({field: pick}))
    for other, site in SITES.items():
        want = (site["labelled"] if expected is None else expected) if other == field else site["labelled"]
        assert emitted[label_key(other)] == want


def test_an_empty_choice_draws_today() -> None:
    assert label_styles(ENTRY, PARAMS, choice=LabelChoice({})) == label_styles(ENTRY, PARAMS, choice=None)


def test_a_choice_naming_a_field_with_no_site_is_refused() -> None:
    with pytest.raises(ValueError) as err:
        label_styles(ENTRY, PARAMS, choice=LabelChoice({"PAYER_NAME": LabelPick("unlabelled")}))
    assert_diagnostic_error(err.value)
    assert "PAYER_NAME" in str(err.value)


def test_the_provider_registers_as_a_label_provider_emitting_every_varied_key() -> None:
    assert is_label_provider(PROVIDER_NAME)
    assert not is_distractor_provider(PROVIDER_NAME)
    varied = {f for fields in load_label_config().doc_types.values() for f in fields}
    assert set(field_provider_emits(PROVIDER_NAME)) == {label_key(f) for f in varied}
    assert get_label_provider(PROVIDER_NAME) is label_styles


def test_a_label_provider_is_not_an_ordinary_one() -> None:
    with pytest.raises(FieldProviderError) as err:
        get_ordinary_provider(PROVIDER_NAME)
    assert_diagnostic_error(err.value)


def test_an_ordinary_provider_is_not_a_label_one() -> None:
    with pytest.raises(FieldProviderError) as err:
        get_label_provider("computed_totals")
    assert_diagnostic_error(err.value)


def test_a_label_provider_emitting_another_key_is_refused() -> None:
    with pytest.raises(FieldProviderError) as err:

        @field_provider("t1_bad_label", emits=("LABEL_X", "POS_X"), labels=True)
        def bad(entry: dict, params: dict, *, choice: LabelChoice | None) -> dict[str, str]:
            return {}

    assert_diagnostic_error(err.value)
    assert "POS_X" in str(err.value)


def test_a_label_provider_emitting_a_scored_column_is_refused() -> None:
    with pytest.raises(FieldProviderError) as err:

        @field_provider("t1_scored_label", emits=("TOTAL_AMOUNT",), labels=True)
        def bad(entry: dict, params: dict, *, choice: LabelChoice | None) -> dict[str, str]:
            return {}

    assert_diagnostic_error(err.value)


def test_an_ordinary_provider_emitting_a_label_key_is_refused() -> None:
    with pytest.raises(FieldProviderError) as err:

        @field_provider("t1_sneaky", emits=("LABEL_TOTAL_AMOUNT",))
        def bad(entry: dict, params: dict) -> dict[str, str]:
            return {}

    assert_diagnostic_error(err.value)
    assert "labels=True" in str(err.value)


def test_a_label_provider_without_a_keyword_choice_is_refused() -> None:
    with pytest.raises(FieldProviderError) as err:

        @field_provider("t1_no_choice", emits=("LABEL_Q",), labels=True)  # type: ignore[arg-type]
        def bad(entry: dict, params: dict) -> dict[str, str]:
            return {}

    assert_diagnostic_error(err.value)
    assert "choice" in str(err.value)


def test_an_ordinary_provider_taking_a_choice_is_refused() -> None:
    with pytest.raises(FieldProviderError) as err:

        @field_provider("t1_stray_choice", emits=("POS_Q",))  # type: ignore[arg-type]
        def bad(entry: dict, params: dict, *, choice: object) -> dict[str, str]:
            return {}

    assert_diagnostic_error(err.value)


def test_a_variant_pick_needs_a_wording() -> None:
    with pytest.raises(ValueError) as err:
        LabelPick("variant", "  ")
    assert_diagnostic_error(err.value)


def test_an_unknown_style_is_refused() -> None:
    with pytest.raises(ValueError) as err:
        LabelPick("shouted")
    assert_diagnostic_error(err.value)


def test_a_choice_is_a_frozen_content_hashed_mapping() -> None:
    picks = {"TOTAL_AMOUNT": LabelPick("unlabelled")}
    choice = LabelChoice(picks)
    picks["GST_AMOUNT"] = LabelPick("labelled")
    assert list(choice) == ["TOTAL_AMOUNT"]
    assert choice == LabelChoice({"TOTAL_AMOUNT": LabelPick("unlabelled")})
    assert hash(choice) == hash(LabelChoice({"TOTAL_AMOUNT": LabelPick("unlabelled")}))
    with pytest.raises(TypeError):
        choice["GST_AMOUNT"] = LabelPick("labelled")  # type: ignore[index]
    with pytest.raises(TypeError):
        LabelChoice({"TOTAL_AMOUNT": "unlabelled"})  # type: ignore[dict-item]


def _layout() -> dict:
    return {"field_providers": [{"name": PROVIDER_NAME, "params": PARAMS}], "distractor_items": []}


def test_apply_hands_the_choice_to_the_label_provider() -> None:
    choice = LabelChoice({"TOTAL_AMOUNT": LabelPick("unlabelled")})
    fields = apply_field_providers(_layout(), ENTRY, distractors=True, labels=choice)["fields"]
    assert fields["LABEL_TOTAL_AMOUNT"] == ""
    assert fields["LABEL_GST_AMOUNT"] == "GST"


def test_a_plain_render_runs_the_label_provider_with_no_choice() -> None:
    fields = apply_field_providers(_layout(), ENTRY, distractors=False)["fields"]
    assert fields["LABEL_TOTAL_AMOUNT"] == "TOTAL"


def test_a_choice_without_distractors_is_refused() -> None:
    with pytest.raises(ValueError) as err:
        apply_field_providers(_layout(), ENTRY, distractors=False, labels=LabelChoice({}))
    assert_diagnostic_error(err.value)
    assert "distractors=True" in str(err.value)


def test_a_choice_on_a_layout_without_label_sites_is_refused_unless_empty() -> None:
    layout: dict = {"field_providers": [], "distractor_items": []}
    assert apply_field_providers(layout, ENTRY, distractors=True, labels=LabelChoice({}))["fields"] == {}
    with pytest.raises(ValueError) as err:
        apply_field_providers(
            layout, ENTRY, distractors=True, labels=LabelChoice({"TOTAL_AMOUNT": LabelPick("unlabelled")})
        )
    assert_diagnostic_error(err.value)
