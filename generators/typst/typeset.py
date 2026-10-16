"""Typeset one case: translate it, compile it, read it back and rasterise it.

`typeset_case` is the one call that turns a ground-truth entry and its layout into a native PDF, its PNG
at the configured resolution and the `geometry` stage one's checks read (`page_geometry`).
"""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from generators.typst.compile import compile_page, rasterise
from generators.typst.config import TypesetConfig, layout_page
from generators.typst.readback import page_geometry
from generators.typst.translate import Translation, translate_page


@dataclass(frozen=True)
class TypesetResult:
    """One typeset page.

    Attributes:
        pdf: The PDF's bytes.
        image: The PDF rasterised at `TypesetConfig.raster_dpi`, as an RGB image.
        geometry: The page read back in stage one's shape (`page_geometry`), in the image's pixels.
        translation: The translation the page was compiled from.
    """

    pdf: bytes
    image: Image.Image
    geometry: dict
    translation: Translation


def typeset_case(
    entry: dict,
    layout: dict,
    *,
    layout_id: str,
    layout_path: Path,
    doc_type: str,
    distractors: bool,
    config: TypesetConfig,
    workdir: Path,
) -> TypesetResult:
    """Typeset one case, plain or with distractors (and so its overlays), as a one-page PDF.

    Args:
        entry: The ground-truth entry, carrying its `case_id`.
        layout: The entry's resolved layout.
        layout_id: The layout's id.
        layout_path: The layout YAML holding it.
        doc_type: The extraction-schema document type the layout draws (`invoice`).
        distractors: Whether the page draws its distractors and overlays, as stage one's distractor render.
        config: The typesetting configuration.
        workdir: A directory for the page's source, PDF and overlay images.

    Returns:
        The PDF, its PNG and its geometry.

    Raises:
        TranslateError: The layout uses something the translator does not typeset.
        TypstError: Typst failed or warned, or the page broke onto a second page.
        ReadbackError: The compiled page's records do not match its translation.
    """
    page = layout_page(layout, layout_path=str(layout_path), layout_id=layout_id)
    translation = translate_page(
        layout,
        entry,
        layout_id=layout_id,
        layout_path=layout_path,
        doc_type=doc_type,
        page=page,
        distractors=distractors,
        config=config,
        overlay_dir=workdir / "overlays",
    )
    compiled = compile_page(translation.source, workdir=workdir, config=config)
    geometry = page_geometry(compiled, dpi=config.raster_dpi, translation=translation)
    image = rasterise(compiled.pdf, dpi=config.raster_dpi)
    return TypesetResult(pdf=compiled.pdf, image=image, geometry=geometry, translation=translation)
