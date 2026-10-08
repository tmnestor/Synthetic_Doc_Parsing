"""A taxpayer's annotations on bank statements: highlighter, pen marks and handwriting (Stage 5, Task 4).

Covers review focus 1 (ink on a scored value, the unboxed dates included), 2 (an annotation on the wrong
row), 3 (highlighter that makes text illegible), 4 (density measured without the overlay) and 5 (the
running total pushed off the page), the row_marks rule, the handwriting faces and the four-part
refusals of the new overlay blocks and settings.
"""

import copy
import random
from typing import Any
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
import yaml
from fontTools.ttLib import TTFont
from PIL import Image, ImageChops, ImageDraw

from generators.bank_statement import _LAYOUT_FILE, render_bank_statement
from generators.common import FONT_FAMILIES, font_path, load_font, vendored_weights
from generators.distractors import values
from generators.distractors.config import (
    DistractorConfigError,
    Hand,
    load_distractor_config,
)
from generators.distractors.density import DensityCheck
from generators.distractors.paths import DISTRACTORS_PATH, LINKS_PATH
from generators.distractors.values import RuleContext, parse_bank_rows
from generators.exporters.geometry import InkRecord
from generators.layout_dsl.schema import LayoutSchemaError, validate_layout
from generators.overlay import annotation, handwriting
from generators.overlay.verify import OverlayError, OverlaySite, check_contrast, contrast_ratio
from generators.overflow_check import check_overflow
from generators.schema import field_names_for
from tests.conftest import assert_diagnostic_error
from tests.distractor_helpers import capture_bank_overlay, decoded, selected
from tests.renderer_helpers import corpus_entries, layouts_for
from tests.test_bank_distractors import carried

DOC = "bank_statement"
KNOWN = set(field_names_for("bank_statements"))
CONFIG = load_distractor_config()
OVERLAY_KEYS = ("DX_ANNOTATED_ROWS", "DX_ROW_MARKS", "DX_MARGIN_NOTE", "DX_ANNOTATION_TOTAL")
# Every glyph a note or a sum can print: the digits and signs of a sum, and the note pool's characters.
NOTE_POOL = yaml.safe_load(Path("config/data_pools.yml").read_text())["distractor_text"]["margin_note"]
GLYPHS = sorted(set("0123456789$.,=+") | {c for note in NOTE_POOL for c in note if c != " "})


def _rows(entry: dict) -> list[dict]:
    return parse_bank_rows(entry["fields"])


def _linked(case_id: str, entry: dict) -> set[int]:
    """Ground-truth rows linked from the case's receipt and invoice, matched by date, amount and words."""
    links = yaml.safe_load(LINKS_PATH.read_text())
    rows = _rows(entry)
    found = set()
    for records in links.values():
        for link in records:
            if not str(link["bank_statement"]).startswith(f"{case_id}_"):
                continue
            hits = [
                i
                for i, row in enumerate(rows)
                if row["date"] == link["bank_date"]
                and row["paid"] is not None
                and Decimal(row["paid"]) == Decimal(str(link["bank_amount"]))
                and " ".join(row["description"].split()) == " ".join(str(link["bank_description"]).split())
            ]
            assert len(hits) >= 1, (case_id, link)
            found.add(next(i for i in hits if i not in found))
    return found


def _render(entry: dict, layout_id: str | None = None) -> tuple[Image.Image, dict]:
    layout = layouts_for(DOC)[layout_id or entry["layout"]]
    geometry: dict = {}
    image = render_bank_statement(entry, layout, distractors=True, geometry_out=geometry)
    return image, geometry


def _pixel_box(geometry: dict, key: str) -> tuple[int, int, int, int]:
    left, top, right, bottom = geometry["boxes"][key]
    w, h = geometry["width"], geometry["height"]
    return round(left * w), round(top * h), round(right * w), round(bottom * h)


# --- the row_marks rule ----------------------------------------------------------------------------------


def _rule_ctx(selected: dict[str, str], seed: int = 1) -> RuleContext:
    return RuleContext(
        rng=random.Random(seed),
        fields={},
        engine=None,  # type: ignore[arg-type]
        pools={},
        case_id="CASE900",
        rows=None,
        selected=selected,
    )  # type: ignore[arg-type]


def test_row_marks_gives_one_kind_per_annotated_row_deterministically() -> None:
    rule = CONFIG.items["bk_row_marks"].rule
    drawn = values.RULES["row_marks"](_rule_ctx({"DX_ANNOTATED_ROWS": "2|5|9|11"}), rule)
    kinds = drawn.split("|")
    assert len(kinds) == 4 and set(kinds) <= {"tick", "circle", "underline"}
    assert drawn == values.RULES["row_marks"](_rule_ctx({"DX_ANNOTATED_ROWS": "2|5|9|11"}), rule)


def test_row_marks_aligns_with_the_annotated_rows_on_every_case(every_item_forced: None) -> None:
    for case_id, entry in corpus_entries(DOC).items():
        chosen = selected(entry, layouts_for(DOC)[entry["layout"]])
        assert len(chosen["DX_ROW_MARKS"].split("|")) == len(chosen["DX_ANNOTATED_ROWS"].split("|")), (
            case_id
        )
        assert chosen == selected(entry, layouts_for(DOC)[entry["layout"]]), case_id


@pytest.mark.parametrize("marks", [[], ["tick", "tick"], "tick", [""]])
def test_row_marks_refuses_a_malformed_marks_list(marks: object) -> None:
    with pytest.raises(values.ValueRuleError) as caught:
        values.RULES["row_marks"](
            _rule_ctx({"DX_ANNOTATED_ROWS": "1"}), {"type": "row_marks", "marks": marks}
        )
    assert_diagnostic_error(caught.value)


# --- the hands -------------------------------------------------------------------------------------------


def test_every_vendored_handwriting_face_and_weight_is_a_hand() -> None:
    hands = set(CONFIG.overlay.handwriting.hands)
    expected = {
        Hand(family, bold)
        for family in ("kalam", "caveat", "indie_flower", "patrick_hand")
        for bold in vendored_weights(family)
    }
    assert hands == expected
    assert all(hand.family in FONT_FAMILIES for hand in hands)


def _glyph_mask(hand: Hand, size: int, char: str) -> bytes:
    image = Image.new("L", (3 * size, 3 * size), 0)
    font = load_font(size, family=hand.family, bold=hand.bold)
    ImageDraw.Draw(image).text((size, size), char, font=font, fill=255)
    return image.tobytes()


def _missing(hand: Hand, chars: list[str]) -> list[str]:
    """The characters a hand's face has no glyph for (it would draw its missing-glyph box, tofu)."""
    cmap = TTFont(font_path(hand.family, bold=hand.bold)).getBestCmap()
    return [char for char in chars if ord(char) not in cmap]


@pytest.mark.parametrize("hand", CONFIG.overlay.handwriting.hands, ids=lambda h: f"{h.family}-{h.bold}")
def test_every_hand_draws_every_note_and_sum_glyph_and_loads_at_every_size(hand: Hand) -> None:
    """No tofu: each glyph is in the face and inks; the face loads at every size a line can take."""
    low, high = CONFIG.overlay.handwriting.size_px
    jitter = CONFIG.overlay.handwriting.glyph_jitter.size_px
    for x_height in range(low, high + 1):
        size = handwriting.font_size_for(hand, x_height)
        for glyph_size in (size - jitter, size + jitter):
            load_font(glyph_size, family=hand.family, bold=hand.bold)
    assert _missing(hand, GLYPHS) == []
    size = handwriting.font_size_for(hand, high)
    for char in GLYPHS:
        assert max(_glyph_mask(hand, size, char)) > 0, (hand, char)


def test_the_tofu_check_can_fail() -> None:
    for hand in CONFIG.overlay.handwriting.hands:
        assert _missing(hand, [*GLYPHS, "中"]) == ["中"]


def test_every_hand_writes_at_least_one_corpus_statement() -> None:
    used = set()
    for case_id in corpus_entries(DOC):
        site = OverlaySite(case_id, "x", Path("x"), "x", "handwriting", DOC)
        ctx = annotation.OverlayContext(
            page=Image.new("RGB", (1, 1)),
            entry={},
            layout={},
            site=site,
            settings=CONFIG.overlay,
            policy="all_ink",
            protected=(),
            ink=(),
            rng=random.Random(0),
        )
        used.add(handwriting.statement_hand(ctx))
    assert used == set(CONFIG.overlay.handwriting.hands)


def test_one_statement_writes_in_one_hand_and_one_pen(every_item_forced: None, monkeypatch) -> None:
    hands, pens = [], []
    real_hand, real_pen = handwriting.statement_hand, annotation.pen_colour

    def spy_hand(ctx):  # type: ignore[no-untyped-def]
        hands.append(real_hand(ctx))
        return hands[-1]

    def spy_pen(ctx):  # type: ignore[no-untyped-def]
        pens.append(real_pen(ctx))
        return pens[-1]

    monkeypatch.setattr(handwriting, "statement_hand", spy_hand)
    monkeypatch.setattr(handwriting, "pen_colour", spy_pen)
    monkeypatch.setattr(annotation, "pen_colour", spy_pen)
    entry = next(iter(corpus_entries(DOC).values()))
    _render(entry)
    assert len(hands) == 2 and len(set(hands)) == 1
    assert len(pens) == 3 and len(set(pens)) == 1


# --- rows (review focus 2) -------------------------------------------------------------------------------


def test_every_band_holds_exactly_its_rows_and_every_linked_row_is_highlighted(
    every_item_forced: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = capture_bank_overlay(monkeypatch)
    for case_id, entry in corpus_entries(DOC).items():
        chosen = selected(entry, layouts_for(DOC)[entry["layout"]])
        _, geometry = _render(entry)
        annotated = [int(i) for i in chosen["DX_ANNOTATED_ROWS"].split("|")]
        bands = [m.box for m in runs[-1].result.drawn_for("DX_ANNOTATED_ROWS")]
        assert len(bands) == len(annotated), case_id
        paid = {
            int(key[len("TRANSACTION_AMOUNTS_PAID[") : -1]): _pixel_box(geometry, key)
            for key in geometry["boxes"]
            if key.startswith("TRANSACTION_AMOUNTS_PAID[")
        }
        for index, band in zip(annotated, bands, strict=True):
            inside = {
                i
                for i, box in paid.items()
                if band[0] <= box[0] and box[2] <= band[2] and band[1] <= (box[1] + box[3]) / 2 <= band[3]
            }
            assert inside == {index}, (case_id, index, band, inside)
        assert _linked(case_id, entry) <= set(annotated), case_id
        rows = _rows(entry)
        if "DX_ANNOTATION_TOTAL" in chosen:
            total = sum(Decimal(rows[i]["paid"]) for i in annotated)
            assert Decimal(chosen["DX_ANNOTATION_TOTAL"]) == total, case_id
        assert len(runs[-1].result.drawn_for("DX_ROW_MARKS")) == len(annotated), case_id


def test_a_row_with_no_paid_box_fails_fast_naming_the_case_and_row(monkeypatch: pytest.MonkeyPatch) -> None:
    from generators.distractors import providers

    monkeypatch.setattr(
        providers,
        "select_items",
        lambda config, doc_type, eligible, rng, *, where: ["bk_annotated_rows", "bk_row_marks"],
    )
    entry = next(iter(corpus_entries(DOC).values()))
    credit = next(i for i, row in enumerate(_rows(entry)) if row["paid"] is None)
    real = values._annotated_rows
    monkeypatch.setitem(values.RULES, "annotated_rows", lambda ctx, rule: f"{real(ctx, rule)}|{credit}")
    with pytest.raises(OverlayError) as caught:
        _render(entry)
    message = str(caught.value)
    assert_diagnostic_error(message)
    assert (
        entry["case_id"] in message
        and f"row {credit}" in message
        and f"TRANSACTION_AMOUNTS_PAID[{credit}]" in message
    )


# --- what the overlay records (review focus 4) -----------------------------------------------------------


def test_overlay_ink_is_recorded_and_the_highlighter_records_nothing(
    every_item_forced: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = capture_bank_overlay(monkeypatch)
    entry = next(iter(corpus_entries(DOC).values()))
    _, geometry = _render(entry)
    overlay_ink = geometry["ink"][runs[-1].before :]
    result = runs[-1].result
    strokes = [m.box for m in result.marks if m.kind in ("tick", "circle", "underline")]
    notes = [m.box for m in result.marks if m.kind == "handwriting"]
    assert [r.box for r in overlay_ink if r.mark] == strokes
    assert [r.box for r in overlay_ink if not r.mark] == notes
    assert all(r.role == "distractor" for r in overlay_ink)
    bands = [m.box for m in result.drawn_for("DX_ANNOTATED_ROWS")]
    assert bands and not {r.box for r in overlay_ink} & set(bands)


def test_validate_refuses_a_distractor_render_that_carries_no_distractor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ruling 5: validate itself (not only a local test) separates a distractor render from a plain one."""
    from generators.distractors import providers

    monkeypatch.setattr(
        providers, "select_items", lambda config, doc_type, eligible, rng, *, where: ["bk_annotated_rows"]
    )
    gt = dict(list(corpus_entries(DOC).items())[:1])
    check = DensityCheck(CONFIG.density[DOC], doc_type=DOC, config_path=DISTRACTORS_PATH)
    assert check_overflow(gt, layouts_for(DOC), render_bank_statement, measure=check) == []
    assert len(check.errors) == 1
    assert_diagnostic_error(check.errors[0])
    assert "no distractor ink" in check.errors[0] and str(_LAYOUT_FILE) in check.errors[0]


def test_the_corpus_bank_renders_all_carry_distractor_ink(monkeypatch: pytest.MonkeyPatch) -> None:
    check = DensityCheck(CONFIG.density[DOC], doc_type=DOC, config_path=DISTRACTORS_PATH)
    assert check_overflow(corpus_entries(DOC), layouts_for(DOC), render_bank_statement, measure=check) == []
    assert check.errors == []


# --- contrast (review focus 3; Task 1 Minor 1) -----------------------------------------------------------

SETTINGS = CONFIG.overlay.contrast
SITE = OverlaySite("CASE900", "fixture", Path("/x"), "fixture.overlay[0]", "highlight", DOC)


def _one_glyph_page(colour: tuple[int, int, int], opacity: float) -> tuple[Image.Image, Image.Image, tuple]:
    page = Image.new("RGB", (400, 120), "white")
    font = load_font(32, family="carlito")
    ImageDraw.Draw(page).text((40, 30), "1", font=font, fill="black")
    glyph = page.convert("L").point(lambda v: 255 if v < 255 else 0).getbbox()
    assert glyph is not None
    # The value's recorded box is as wide as a column cell: the glyph inks about 3% of it.
    box = (20, glyph[1], 220, glyph[3])
    multiplied = ImageChops.multiply(page, Image.new("RGB", page.size, colour))
    tinted = Image.composite(multiplied, page, Image.new("L", page.size, round(opacity * 255)))
    return page, tinted, box


def test_a_one_glyph_scored_value_under_a_light_band_passes() -> None:
    """Minor 1: the 5th percentile of a box that is 97% paper reads paper; the ink pixels read ink."""
    colour = CONFIG.overlay.highlighter.colours["yellow"]
    before, after, box = _one_glyph_page(colour, CONFIG.overlay.highlighter.opacity[1])
    luminance = np.asarray(after.convert("L").crop(box), dtype=float)
    assert np.percentile(luminance, SETTINGS.dark_percentile) > 200, "the fixture is mostly paper"
    assert contrast_ratio(after, box, SETTINGS) >= SETTINGS.min_ratio
    record = InkRecord("scored", box, False, "x", 10, False)
    check_contrast(before, after, [box], [record], SETTINGS, site=SITE)


def test_the_one_glyph_fixture_fails_under_the_whole_box_percentile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fixture can fail: with the old whole-box percentile the same box reads as no contrast."""
    from generators.overlay import verify

    def whole_box(image, box, settings):  # type: ignore[no-untyped-def]
        lum = verify._luminance(image, box)
        return (np.percentile(lum, settings.light_percentile) + 0.05) / (
            np.percentile(lum, settings.dark_percentile) + 0.05
        )

    monkeypatch.setattr(verify, "contrast_ratio", whole_box)
    before, after, box = _one_glyph_page(CONFIG.overlay.highlighter.colours["yellow"], 0.8)
    with pytest.raises(OverlayError):
        verify.check_contrast(
            before, after, [box], [InkRecord("scored", box, False, "x", 10, False)], SETTINGS, site=SITE
        )


def _with_overlay(monkeypatch: pytest.MonkeyPatch, **changes: Any) -> None:
    """Replace overlay settings for the loaded catalogue (the engine reads it per render)."""
    from dataclasses import replace

    from generators.overlay import engine

    config = load_distractor_config()
    overlay = config.overlay
    for name, value in changes.items():
        overlay = replace(overlay, **{name: value})
    patched = replace(config, overlay=overlay)
    monkeypatch.setattr(engine, "load_distractor_config", lambda: patched)


def test_a_dark_highlighter_fails_contrast(
    every_item_forced: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    dark = replace(CONFIG.overlay.highlighter, colours={"dark": (70, 70, 120)}, opacity=(0.9, 0.9))
    _with_overlay(monkeypatch, highlighter=dark)
    entry = next(iter(corpus_entries(DOC).values()))
    with pytest.raises(OverlayError) as caught:
        _render(entry)
    message = str(caught.value)
    assert_diagnostic_error(message)
    assert "min_ratio" in message and "scored ink" in message and "highlight block" in message


def test_two_adjacent_rows_are_multiplied_once_where_their_bands_meet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review focus 3: bands padded until they overlap darken the overlap no more than either band."""
    from dataclasses import replace

    from generators.distractors import providers

    entry = next(iter(corpus_entries(DOC).values()))
    rows = _rows(entry)
    pair = next(i for i in range(len(rows) - 1) if rows[i]["paid"] and rows[i + 1]["paid"])
    monkeypatch.setattr(
        providers, "select_items", lambda config, doc_type, eligible, rng, *, where: ["bk_annotated_rows"]
    )
    monkeypatch.setitem(values.RULES, "annotated_rows", lambda ctx, rule: f"{pair}|{pair + 1}")
    # Even ink, so a single band's weight is the same everywhere along both bands (uneven ink is tested on
    # its own): any darkening at the overlap would then be the double multiply this test exists to catch.
    wide = replace(
        CONFIG.overlay.highlighter,
        pad_px=30,
        end_jitter_px=0,
        opacity=(0.8, 0.8),
        edge_jitter_px=0,
        density_wobble=0.0,
        end_density=1.0,
    )
    _with_overlay(monkeypatch, highlighter=wide)
    runs = capture_bank_overlay(monkeypatch)
    image, _ = _render(entry)
    first, second = (m.box for m in runs[-1].result.marks)
    assert second[1] < first[3], "the padded bands overlap"
    x = first[0] + 60
    overlap = image.getpixel((x, (second[1] + first[3]) // 2))
    single = image.getpixel((x, first[1] + 2))
    assert overlap == single and overlap != (255, 255, 255)


# --- placement (review focus 1) ---------------------------------------------------------------------------


def test_a_tick_forced_onto_the_date_column_fails_naming_the_date_cell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from generators.distractors import providers

    monkeypatch.setattr(
        providers,
        "select_items",
        lambda config, doc_type, eligible, rng, *, where: ["bk_annotated_rows", "bk_row_marks"],
    )
    monkeypatch.setitem(
        values.RULES,
        "row_marks",
        lambda ctx, rule: "|".join("tick" for _ in ctx.selected["DX_ANNOTATED_ROWS"].split("|")),
    )
    real_geometry = annotation.lane_geometry

    def onto_the_dates(ctx, description_field, column_field):  # type: ignore[no-untyped-def]
        geometry = real_geometry(ctx, description_field, column_field)
        # The left margin lane now ends 120px inside the content box: over the date column.
        return annotation.LaneGeometry(
            geometry.content_left + 120,
            geometry.content_right,
            geometry.description_field,
            geometry.column,
            geometry.table_bottom,
        )

    monkeypatch.setattr(annotation, "lane_geometry", onto_the_dates)
    monkeypatch.setattr(
        annotation,
        "place",
        lambda size, lane, row, geometry, occupancy, ctx: (
            (
                geometry.content_left - size[0],
                round(row.centre_y - size[1] / 2),
                geometry.content_left,
                round(row.centre_y + size[1] / 2),
            ),
            "",
        ),
    )
    entry = corpus_entries(DOC)["CASE001"]
    with pytest.raises(OverlayError) as caught:
        _render(entry)
    message = str(caught.value)
    assert_diagnostic_error(message)
    date_cells = [
        record.box
        for record in _plain_ink(entry)
        if record.role == "text" and record.box[0] < 200 + 100 and "tick" in message
    ]
    assert "the tick for row" in message
    assert any(str(box) in message for box in date_cells), message


def _plain_ink(entry: dict) -> list[InkRecord]:
    geometry: dict = {}
    render_bank_statement(
        entry, layouts_for(DOC)[entry["layout"]], distractors=False, geometry_out=geometry
    )
    return geometry["ink"]


def test_the_issue_date_shares_the_statement_periods_row(every_item_forced: None, monkeypatch) -> None:
    """Ruling 5: the issue date's spacer (CBA, Westpac) or column (ANZ) puts it on the period's row."""
    runs = capture_bank_overlay(monkeypatch)
    for layout_id in (
        "cba_standard",
        "cba_date_grouped",
        "westpac_standard",
        "westpac_premium",
        "anz_standard",
        "anz_modern",
    ):
        entry = next(iter(carried(layout_id).values()))
        _, geometry = _render({**entry, "layout": layout_id}, layout_id)
        period = _pixel_box(geometry, "STATEMENT_DATE_RANGE")
        inline = geometry["ink"][: runs[-1].before]
        issued = [
            r.box
            for r in inline
            if r.role == "distractor" and not r.mark and r.box[1] < period[3] + 200 and r.box[0] > 900
        ]
        assert issued, layout_id
        centre = (issued[0][1] + issued[0][3]) / 2
        assert period[1] <= centre <= period[3], (layout_id, issued[0], period)


# --- schema (four-part refusals) -------------------------------------------------------------------------


def _layout_with(mutate) -> dict:  # type: ignore[no-untyped-def]
    layout = copy.deepcopy(layouts_for(DOC)["cba_standard"])
    layout["overlay"] = copy.deepcopy(layout["overlay"])
    mutate(layout["overlay"])
    return layout


def _refusal(layout: dict) -> str:
    with pytest.raises(LayoutSchemaError) as caught:
        validate_layout(
            layout,
            layout_id="cba_standard",
            layout_path=str(_LAYOUT_FILE),
            known_fields=KNOWN,
            doc_type="bank_statements",
        )
    message = str(caught.value)
    assert_diagnostic_error(message)
    return message


def test_the_shipped_bank_layouts_validate() -> None:
    for layout_id, layout in layouts_for(DOC).items():
        validate_layout(
            layout,
            layout_id=layout_id,
            layout_path=str(_LAYOUT_FILE),
            known_fields=KNOWN,
            doc_type="bank_statements",
        )
        assert [b["type"] for b in layout["overlay"]] == [
            "highlight",
            "ink_marks",
            "handwriting",
            "handwriting",
        ]


@pytest.mark.parametrize(
    ("index", "key", "value", "words"),
    [
        (1, "kinds", ["tick", "circle"], "misses ['underline']"),
        (1, "kinds", ["tick", "circle", "underline", "arrow"], "kinds"),
        (0, "rows", "ANNOTATED_ROWS", "not a DX_ key"),
        (1, "marks", "DX_MARGIN_NOTE", "but is gated on"),
        (2, "lanes", ["after_description", "top_margin"], "top_margin"),
        (1, "tick_lane", "after_description", "margin lane"),
        (1, "target_field", "TRANSACTION_DATES", "no table in the layout records"),
        (0, "row_fields", ["TRANSACTION_DESCRIPTIONS", "ACCOUNT_BALANCE"], "ACCOUNT_BALANCE"),
        (3, "column_field", "TOTAL_AMOUNT", "TOTAL_AMOUNT"),
        (0, "colour", "yellow", "unknown ['colour']"),
    ],
)
def test_a_malformed_annotation_block_is_refused(index: int, key: str, value: object, words: str) -> None:
    def mutate(blocks: list) -> None:
        blocks[index] = {**blocks[index], key: value}

    message = _refusal(_layout_with(mutate))
    assert words in message, message
    assert f"cba_standard.overlay[{index}]" in message and str(_LAYOUT_FILE) in message


# --- settings (four-part refusals) -----------------------------------------------------------------------


def _catalogue(tmp_path: Path, edit) -> Path:  # type: ignore[no-untyped-def]
    data = yaml.safe_load(DISTRACTORS_PATH.read_text())
    edit(data["overlay"])
    path = tmp_path / "distractors.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


@pytest.mark.parametrize(
    ("edit", "key"),
    [
        (lambda o: o.pop("highlighter"), "overlay.highlighter"),
        (lambda o: o["highlighter"].update(colours={"yellow": "FFF06A"}), "overlay.highlighter.colours"),
        (lambda o: o["highlighter"].update(opacity=[0.9, 0.5]), "overlay.highlighter.opacity"),
        (lambda o: o["highlighter"].update(glow=1), "overlay.highlighter"),
        (lambda o: o["ink"].pop("tick"), "overlay.ink.tick"),
        (lambda o: o["ink"].update(stroke_px=[3.5, 5]), "overlay.ink.stroke_px"),
        (lambda o: o["ink"]["circle"].update(gap_px=0), "overlay.ink.circle.gap_px"),
        (
            lambda o: o["handwriting"]["hands"].append({"family": "patrick_hand", "bold": True}),
            "overlay.handwriting.hands[6].bold",
        ),
        (
            lambda o: o["handwriting"]["hands"].append({"family": "comic", "bold": False}),
            "overlay.handwriting.hands[6].family",
        ),
        (
            lambda o: o["handwriting"]["hands"].append({"family": "kalam", "bold": False}),
            "overlay.handwriting.hands[6]",
        ),
        (
            lambda o: o["handwriting"]["glyph_jitter"].update(size_px=40),
            "overlay.handwriting.glyph_jitter.size_px",
        ),
        (
            lambda o: o["handwriting"]["glyph_jitter"].pop("slant_deg"),
            "overlay.handwriting.glyph_jitter.slant_deg",
        ),
    ],
)
def test_malformed_annotation_settings_are_refused(tmp_path: Path, edit, key: str) -> None:  # type: ignore[no-untyped-def]
    path = _catalogue(tmp_path, edit)
    with pytest.raises(DistractorConfigError) as caught:
        load_distractor_config(path)
    message = str(caught.value)
    assert_diagnostic_error(message)
    assert f"{path.resolve()} -> {key}" in message, message


# --- the seed's dry run and the invoice layout file (Ruling 3) -------------------------------------------


def test_invoice_layout_ids_refuse_a_malformed_layout_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    broken = tmp_path / "invoices.yml"
    broken.write_text("layouts: []\n")
    monkeypatch.setattr(values, "layout_file", lambda doc_type: broken)
    values._invoice_layout_ids.cache_clear()
    try:
        with pytest.raises(values.ValueRuleError) as caught:
            values._invoice_layout_ids()
    finally:
        values._invoice_layout_ids.cache_clear()
    assert_diagnostic_error(caught.value)
    assert f"{broken.resolve()} -> layouts" in str(caught.value)


def test_the_dry_run_links_are_the_link_seeders_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dry run's in-memory corpus is the committed one: the link seeder's function, receipts included."""
    import os

    from tests.test_seed_ground_truth_dry_run import _load_seed_module

    seed = _load_seed_module()
    previous, state = os.getcwd(), random.getstate()
    os.chdir(Path(__file__).resolve().parents[1])
    try:
        entries = seed.build_all_entries()
    finally:
        os.chdir(previous)
        random.setstate(state)
    linked, links = seed._linked_copies({f"{k}.yml": v for k, v in entries.items()})
    assert links == yaml.safe_load(LINKS_PATH.read_text())
    assert sum(1 for image in links if "_receipt_" in image) == 55
    for name in ("bank_statements", "receipts", "invoices"):
        assert linked[f"{name}.yml"] == yaml.safe_load(Path(f"ground_truth/{name}.yml").read_text()), name
    assert entries["receipts"] != linked["receipts.yml"], "the generated entries are left untouched"


# --- slow: every case -----------------------------------------------------------------------------------


def _check_render(case_id: str, entry: dict, layout_id: str, runs: list, chosen: dict) -> Image.Image:
    image, geometry = _render(entry, layout_id)
    again, _ = _render(entry, layout_id)
    assert image.tobytes() == again.tobytes(), (case_id, layout_id)
    result = runs[-1].result
    for key in OVERLAY_KEYS:
        if key in chosen:
            assert result.drawn_for(key), (case_id, layout_id, key)
    if "DX_MAIL_BARCODE" in chosen:
        assert decoded(image) == [chosen["DX_MAIL_BARCODE"]], (case_id, layout_id)
    return image


@pytest.mark.slow
def test_every_case_on_its_own_layout_draws_its_annotations(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = capture_bank_overlay(monkeypatch)
    annotated = 0
    for case_id, entry in corpus_entries(DOC).items():
        chosen = selected(entry, layouts_for(DOC)[entry["layout"]])
        annotated += "DX_ANNOTATED_ROWS" in chosen
        _check_render(case_id, entry, entry["layout"], runs, chosen)
    assert annotated >= 0.8 * len(corpus_entries(DOC))


@pytest.mark.slow
@pytest.mark.parametrize("layout_id", list(layouts_for(DOC)))
def test_every_carried_case_annotated_on_every_layout(
    every_item_forced: None, layout_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = capture_bank_overlay(monkeypatch)
    for case_id, entry in carried(layout_id).items():
        chosen = selected(entry, layouts_for(DOC)[layout_id])
        _check_render(case_id, entry, layout_id, runs, chosen)


@pytest.mark.slow
def test_no_overlay_pixel_meets_a_padded_scored_box_and_every_highlighted_value_is_legible(
    every_item_forced: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The roadmap exit criterion over every bank case (receipts carry no overlay; invoices' stamps are held
    by the invoice tests): pixels of every opaque layer against every padded scored box, and contrast."""
    runs = capture_bank_overlay(monkeypatch, keep_layers=True)
    clearance = CONFIG.overlay.clearance_px
    smallest_gap, smallest_ratio = 10**6, 10.0**6
    for case_id, entry in corpus_entries(DOC).items():
        image, geometry = _render(entry)
        alpha = np.asarray(runs[-1].image) > 0
        ys, xs = np.nonzero(alpha)
        bands = [m.box for m in runs[-1].result.drawn_for("DX_ANNOTATED_ROWS")]
        for record in geometry["ink"][: runs[-1].before]:
            if record.role != "scored":
                continue
            left, top, right, bottom = record.box
            padded = alpha[
                max(top - clearance, 0) : bottom + clearance, max(left - clearance, 0) : right + clearance
            ]
            assert not padded.any(), (case_id, record)
            gap = np.maximum(
                np.maximum(left - xs, xs - (right - 1)), np.maximum(top - ys, ys - (bottom - 1))
            ).min()
            smallest_gap = min(smallest_gap, int(gap))
            if any(b[0] < right and left < b[2] and b[1] < bottom and top < b[3] for b in bands):
                smallest_ratio = min(smallest_ratio, contrast_ratio(image, record.box, SETTINGS))
    assert smallest_gap > clearance and smallest_ratio >= SETTINGS.min_ratio
    print(f"smallest clearance {smallest_gap}px, smallest highlighted contrast {smallest_ratio:.2f}")
