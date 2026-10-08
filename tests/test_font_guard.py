"""Guard: fit measurement must use a vendored face, never a system font.

`load_font` no longer has a system fallback at all, so the family registry is
the first line of defence and these tests cover the second: a caller who builds
an `ImageFont` itself and hands it to measurement.
"""

import pytest
from PIL import ImageFont

from generators.common import (
    FONT_FAMILIES,
    FontFamilyError,
    FontSourceError,
    assert_bundled_font,
    font_path,
    font_source_path,
    load_font,
    vendored_weights,
)
from tests.conftest import assert_diagnostic_error


@pytest.mark.parametrize("family", sorted(FONT_FAMILIES))
def test_every_vendored_family_loads_and_passes_guard(family: str) -> None:
    """Each registered family loads in every weight it ships, from fonts/, and passes the guard."""
    for bold in vendored_weights(family):
        font = load_font(20, family=family, bold=bold)
        assert_bundled_font(font)
        source = font_source_path(font)
        assert source == font_path(family, bold=bold)
        assert source is not None
        assert source.is_file()
    assert load_font(20, family=family) is load_font(20, family=family), "loads are cached"


def test_system_font_fails_guard() -> None:
    """A face the caller built itself, even from a vendored file, is refused by the guard."""
    outsider = ImageFont.truetype(str(font_path("carlito")), 20)
    with pytest.raises(FontSourceError) as err:
        assert_bundled_font(outsider)
    assert_diagnostic_error(err.value)

    with pytest.raises(FontSourceError):
        assert_bundled_font(ImageFont.load_default())


def test_unknown_family_fails_fast_with_a_diagnostic() -> None:
    """The fallback is gone, so a typo'd family must fail rather than degrade."""
    with pytest.raises(FontFamilyError) as err:
        load_font(20, family="carlto")
    assert_diagnostic_error(err.value)
    assert "carlto" in str(err.value)
    assert "carlito" in str(err.value), "the message must list the valid families"


from PIL import Image, ImageDraw  # noqa: E402

from tests.conftest import REPO_ROOT  # noqa: E402


def test_kalam_is_vendored_with_its_licence() -> None:
    """The handwriting face ships in fonts/ with its OFL licence, both weights."""
    assert FONT_FAMILIES["kalam"] == ("Kalam-Regular.ttf", "Kalam-Bold.ttf")
    for bold in (False, True):
        path = font_path("kalam", bold=bold)
        assert path.parent == REPO_ROOT / "fonts" and path.is_file()
    assert "SIL Open Font License" in (REPO_ROOT / "fonts" / "LICENSE-Kalam.txt").read_text()


def test_kalam_draws_ink() -> None:
    """The face loads through the vendored-font path and renders visible text."""
    image = Image.new("RGB", (200, 60), "white")
    ImageDraw.Draw(image).text((5, 5), "WRE 50% work", font=load_font(24, family="kalam"), fill="black")
    assert min(image.convert("L").tobytes()) < 128


# The handwriting faces vendored beside Kalam (Decision 3, revised): each file, its licence and its weights.
HANDWRITING = {
    "caveat": ("LICENSE-Caveat.txt", ("Caveat-Regular.ttf", "Caveat-Bold.ttf")),
    "indie_flower": ("LICENSE-IndieFlower.txt", ("IndieFlower-Regular.ttf", None)),
    "patrick_hand": ("LICENSE-PatrickHand.txt", ("PatrickHand-Regular.ttf", None)),
}


@pytest.mark.parametrize("family", sorted(HANDWRITING))
def test_each_handwriting_face_is_vendored_with_its_licence(family: str) -> None:
    licence, files = HANDWRITING[family]
    assert FONT_FAMILIES[family] == files
    text = (REPO_ROOT / "fonts" / licence).read_text()
    assert "SIL Open Font License" in text and "Version 1.1" in text
    for bold in vendored_weights(family):
        assert font_path(family, bold=bold).is_file()
    provenance = (REPO_ROOT / "fonts" / "SOURCES-handwriting.txt").read_text()
    assert all(name in provenance for name in (licence, *[f for f in files if f]))


def test_every_font_file_in_fonts_is_registered_and_licensed() -> None:
    """No stray face: every TTF in fonts/ is a registered weight, and every family's licence is present."""
    registered = {name for files in FONT_FAMILIES.values() for name in files if name}
    on_disk = {path.name for path in (REPO_ROOT / "fonts").glob("*.ttf")}
    assert on_disk == registered
    licences = {path.name for path in (REPO_ROOT / "fonts").glob("LICENSE-*.txt")}
    stems = {"Carlito", "Liberation", "Kalam", "Caveat", "IndieFlower", "PatrickHand"}
    assert licences == {f"LICENSE-{stem}.txt" for stem in stems}


@pytest.mark.parametrize("family", ["indie_flower", "patrick_hand"])
def test_a_bold_weight_the_face_does_not_ship_fails_fast(family: str) -> None:
    assert vendored_weights(family) == (False,)
    with pytest.raises(FontFamilyError) as err:
        load_font(30, family=family, bold=True)
    assert_diagnostic_error(err.value)
    assert family in str(err.value) and "bold: false" in str(err.value)
