"""The walker reports every block it draws onto the recorded page: its ink range and its cursor travel.

Stage two's translator reads a page's content from these reports, so each claim here is read back from a
real `render_body` onto a scratch receipt layout (see `dsl_helpers`).
"""

from PIL import ImageChops

from generators.exporters.geometry import BoxRecorder
from generators.layout_dsl.context import Region
from generators.layout_dsl.engine import DrawnBlock, render_body
from tests.layout_dsl.dsl_helpers import ENTRY, LAYOUT_ID, MARGIN, RECEIPTS_PATH, canvas, real_layout

# Invented values: none appear in ground_truth/.
BODY = [
    {"type": "text", "content": "Quillon Paper Goods"},
    {"type": "spacer", "height": 17},
    {"type": "text", "content": "Never drawn", "when": "ABSENT_FIELD"},
    {
        "type": "split",
        "children": [
            [{"type": "text", "content": "Left Quillon"}],
            [{"type": "text", "content": "Right Quillon", "align": "right"}],
        ],
    },
]


def draw(body: list, drawn_out: dict | None) -> tuple:
    """Render `body` with a recorder, handing `drawn_out` to the walker; return the image and recorder."""
    image, surface = canvas()
    layout = real_layout(body)
    recorder = BoxRecorder(image.width, image.height)
    render_body(
        layout,
        ENTRY,
        layout_id=LAYOUT_ID,
        layout_path=str(RECEIPTS_PATH),
        draw=surface,
        region=Region(x=MARGIN, width=int(layout["content_width"])),
        y=MARGIN,
        distractors=False,
        recorder=recorder,
        drawn_out=drawn_out,
    )
    return image, recorder


def test_every_drawn_block_is_reported_by_path_and_a_gated_one_is_not() -> None:
    drawn: dict[str, DrawnBlock] = {}
    draw(BODY, drawn)
    assert list(drawn) == [
        "body[0]",
        "body[1]",
        "body[3].children[0][0]",
        "body[3].children[1][0]",
        "body[3]",
    ]


def test_each_report_names_the_ink_its_block_drew() -> None:
    drawn: dict[str, DrawnBlock] = {}
    _, recorder = draw(BODY, drawn)
    ink = recorder.ink_records()
    texts = {path: [r.text for r in ink[d.ink_start : d.ink_end]] for path, d in drawn.items()}
    assert texts["body[0]"] == ["Quillon Paper Goods"]
    assert texts["body[1]"] == []
    assert texts["body[3].children[0][0]"] == ["Left Quillon"]
    assert texts["body[3]"] == ["Left Quillon", "Right Quillon"]


def test_each_report_names_the_cursor_travel_of_its_block() -> None:
    drawn: dict[str, DrawnBlock] = {}
    draw(BODY, drawn)
    spacer = drawn["body[1]"]
    assert spacer.y_end - spacer.y_start == 17
    assert drawn["body[3]"].y_start == spacer.y_end


def test_reporting_draws_the_same_pixels() -> None:
    plain, _ = draw(BODY, None)
    reported, _ = draw(BODY, {})
    assert ImageChops.difference(plain, reported).getbbox() is None
