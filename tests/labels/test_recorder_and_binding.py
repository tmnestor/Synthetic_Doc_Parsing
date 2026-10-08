"""The recorder keeps labels, regions and what each ink line says; bound_label sees past a label site."""

import pytest

from generators.exporters.geometry import BoxRecorder, InkRecord
from generators.layout_dsl.binding import bound_label

FIELDS = {"BUSINESS_ABN": "12 345 678 901", "LABEL_BUSINESS_ABN": "ABN: ", "DX_ACN": "123 456 789"}


@pytest.mark.parametrize(
    ("template", "label"),
    [
        ("{LABEL_BUSINESS_ABN}{BUSINESS_ABN}", "ABN: "),
        ("ABN: {BUSINESS_ABN}", "ABN: "),
        ("Seller {LABEL_BUSINESS_ABN}{BUSINESS_ABN}", "Seller ABN: "),
        ("{BUSINESS_ABN}", ""),
        ("{DX_ACN} {BUSINESS_ABN}", ""),
        ("{LABEL_BUSINESS_ABN}{BUSINESS_ABN} ", ""),
        ("{BUSINESS_ABN}{LABEL_BUSINESS_ABN}{BUSINESS_ABN}", ""),
    ],
)
def test_the_label_before_a_bound_field_is_everything_but_the_field(template: str, label: str) -> None:
    assert bound_label(template, "BUSINESS_ABN", FIELDS) == label


def test_an_unlabelled_site_has_no_label() -> None:
    assert (
        bound_label(
            "{LABEL_BUSINESS_ABN}{BUSINESS_ABN}", "BUSINESS_ABN", {**FIELDS, "LABEL_BUSINESS_ABN": ""}
        )
        == ""
    )


def test_ink_records_carry_text_size_and_weight() -> None:
    recorder = BoxRecorder(100, 100)
    recorder.record_ink((0, 0, 10, 10), "text", text="Total:", size=48, bold=True)
    recorder.record_ink((0, 20, 10, 30), "distractor", text="1234567890123", size=0, bold=False, mark=True)
    assert recorder.ink_records() == [
        InkRecord("text", (0, 0, 10, 10), False, "Total:", 48, True),
        InkRecord("distractor", (0, 20, 10, 30), True, "1234567890123", 0, False),
    ]


@pytest.mark.parametrize(("text", "size"), [("", 18), ("   ", 18), ("x", 0), ("x", -1)])
def test_a_blank_or_sizeless_text_line_is_refused(text: str, size: int) -> None:
    with pytest.raises(ValueError, match="Remediation"):
        BoxRecorder(100, 100).record_ink((0, 0, 1, 1), "text", text=text, size=size, bold=False)


@pytest.mark.parametrize(("size", "bold"), [(12, False), (0, True)])
def test_a_mark_with_a_font_is_refused(size: int, bold: bool) -> None:
    with pytest.raises(ValueError, match="Remediation"):
        BoxRecorder(100, 100).record_ink(
            (0, 0, 1, 1), "distractor", text="", size=size, bold=bold, mark=True
        )


def test_labels_are_recorded_once_per_field() -> None:
    recorder = BoxRecorder(100, 100)
    recorder.record_label("TOTAL_AMOUNT", "Total:", (1, 2, 3, 4))
    recorder.record_label("GST_AMOUNT", "", None)
    assert recorder.labels() == {"TOTAL_AMOUNT": ("Total:", (1, 2, 3, 4)), "GST_AMOUNT": ("", None)}
    with pytest.raises(ValueError, match="already has a recorded label"):
        recorder.record_label("TOTAL_AMOUNT", "", None)


@pytest.mark.parametrize(("text", "box"), [("Total:", None), ("", (1, 2, 3, 4))])
def test_a_label_whose_text_and_box_disagree_is_refused(text: str, box: tuple | None) -> None:
    with pytest.raises(ValueError, match="Remediation"):
        BoxRecorder(100, 100).record_label("TOTAL_AMOUNT", text, box)


def test_regions_are_recorded_once_per_name() -> None:
    recorder = BoxRecorder(100, 100)
    recorder.record_region("totals", (1, 2, 30, 40))
    assert recorder.regions() == {"totals": (1, 2, 30, 40)}
    with pytest.raises(ValueError, match="already recorded"):
        recorder.record_region("totals", (1, 2, 30, 40))


def test_labels_and_regions_never_reach_the_exported_boxes() -> None:
    recorder = BoxRecorder(100, 200)
    recorder.record("TOTAL_AMOUNT", (10, 20, 30, 40), text="x")
    recorder.record_label("TOTAL_AMOUNT", "Total:", (0, 20, 9, 40))
    recorder.record_region("totals", (0, 0, 100, 100))
    assert recorder.as_dict() == {"TOTAL_AMOUNT": [0.1, 0.1, 0.3, 0.2]}
