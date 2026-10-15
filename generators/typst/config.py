"""Load and validate config/typeset_config.yml, and read a layout's physical page.

Every key is required and the YAML is the single source of truth: this module holds no default for any of
them. A missing or invalid key fails fast with a four-part diagnostic (What, Where, Expected, Recover).
`layout_page` turns a layout's required `page:` key into a `PageSpec`.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from generators.distractors.paths import REPO_ROOT
from generators.layout_dsl.schema import PAGE_SIZES_MM, validate_page

TYPESET_CONFIG_PATH = REPO_ROOT / "config" / "typeset_config.yml"
_KEYS = ("typst_version", "font_dir", "creation_timestamp", "raster_dpi", "template")
_EXAMPLE = (
    'typst_version: "0.15.1"\n'
    "font_dir: fonts\n"
    "creation_timestamp: 1767225600\n"
    "raster_dpi: 300\n"
    "template: generators/typst/template.typ"
)
_TYPST_VERSION = re.compile(r"^typst (\d+\.\d+\.\d+)\b")


class TypesetConfigError(RuntimeError):
    """Raised when config/typeset_config.yml is missing, unparseable or invalid."""


@dataclass(frozen=True)
class TypesetConfig:
    """The validated typesetting configuration.

    Attributes:
        typst_version: The Typst release the PDFs are compiled with, equal to the installed one.
        font_dir: Directory (relative to the repository root) of the `.ttf` files Typst may use.
        creation_timestamp: Seconds since the Unix epoch stamped on every PDF as its creation date.
        raster_dpi: Resolution at which a PDF page is rasterised.
        template: The Typst template (relative to the repository root) every layout is typeset with.
    """

    typst_version: str
    font_dir: str
    creation_timestamp: int
    raster_dpi: int
    template: str


@dataclass(frozen=True)
class PageSpec:
    """A layout's physical page.

    Attributes:
        width_mm: Page width in millimetres.
        height_mm: Page height in millimetres; `None` for a receipt roll, whose height follows its content.
    """

    width_mm: float
    height_mm: float | None


def _fail(what: str, *, path: Path, key: str, expected: str, recover: str) -> TypesetConfigError:
    """Build a four-part fail-fast diagnostic for the typeset config."""
    return TypesetConfigError(
        "Invalid typeset config.\n"
        f"  What:     {what}\n"
        f"  Where:    {path.resolve()} -> {key}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def _installed_typst_version(path: Path) -> str:
    """Return the version `typst --version` reports, or raise a diagnostic if it cannot be read."""
    try:
        done = subprocess.run(["typst", "--version"], capture_output=True, text=True, check=False)
    except FileNotFoundError as err:
        raise _fail(
            what="the `typst` executable was not found on PATH.",
            path=path,
            key="typst_version",
            expected="Typst installed from conda-forge at the version pinned in environment.yml.",
            recover="run `conda env update -n synthetic -f environment.yml --prune` and activate the env.",
        ) from err
    found = _TYPST_VERSION.match(done.stdout.strip())
    if done.returncode != 0 or found is None:
        raise _fail(
            what=f"`typst --version` printed {done.stdout.strip()!r} (exit {done.returncode}), which is "
            "not 'typst X.Y.Z'.",
            path=path,
            key="typst_version",
            expected="output beginning 'typst 0.15.1'.",
            recover="reinstall Typst from conda-forge: `conda env update -f environment.yml --prune`.",
        )
    return found.group(1)


def _check_types(data: dict, *, path: Path) -> None:
    """Check each key is present and of the right type, and the integers are in range."""
    for key in _KEYS:
        if key not in data:
            raise _fail(
                what=f"required key '{key}' is missing.",
                path=path,
                key=key,
                expected=f"the key present, e.g.\n{_EXAMPLE}",
                recover=f"add '{key}' to {path.name} at the top level.",
            )
    for key in ("typst_version", "font_dir", "template"):
        if not isinstance(data[key], str) or not data[key]:
            raise _fail(
                what=f"'{key}' is {data[key]!r}, not a non-empty string.",
                path=path,
                key=key,
                expected=f"a non-empty string, e.g.\n{_EXAMPLE}",
                recover=f"set '{key}' in {path.name} to a non-empty string.",
            )
    timestamp = data["creation_timestamp"]
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
        raise _fail(
            what=f"'creation_timestamp' is {timestamp!r}, not a non-negative integer of Unix seconds.",
            path=path,
            key="creation_timestamp",
            expected="Unix seconds, e.g. creation_timestamp: 1767225600 (1 January 2026, 00.00 UTC).",
            recover=f"set 'creation_timestamp' in {path.name} to an integer number of seconds.",
        )
    dpi = data["raster_dpi"]
    if isinstance(dpi, bool) or not isinstance(dpi, int) or dpi <= 0:
        raise _fail(
            what=f"'raster_dpi' is {dpi!r}, not a positive integer.",
            path=path,
            key="raster_dpi",
            expected="a positive integer of dots per inch, e.g. raster_dpi: 300.",
            recover=f"set 'raster_dpi' in {path.name} to a positive integer.",
        )


def load_typeset_config(path: Path) -> TypesetConfig:
    """Load and validate the typesetting configuration.

    Args:
        path: The YAML file, normally `TYPESET_CONFIG_PATH`.

    Returns:
        The validated, frozen configuration.

    Raises:
        TypesetConfigError: If the file is missing or unparseable, a key is missing, unknown or mistyped,
            `typst_version` differs from the installed Typst, or `font_dir` holds no `.ttf`.
    """
    if not path.is_file():
        raise _fail(
            what="the typeset config file does not exist.",
            path=path,
            key="(file)",
            expected=f"a YAML file with these keys, e.g.\n{_EXAMPLE}",
            recover="restore config/typeset_config.yml from version control, or pass the right path.",
        )
    try:
        data: Any = yaml.safe_load(path.read_text())
    except yaml.YAMLError as err:
        raise _fail(
            what=f"the file is not valid YAML: {err}",
            path=path,
            key="(file)",
            expected=f"a YAML mapping, e.g.\n{_EXAMPLE}",
            recover="fix the YAML syntax error named above.",
        ) from err
    if not isinstance(data, dict):
        raise _fail(
            what=f"the top level is {type(data).__name__}, not a mapping.",
            path=path,
            key="(top level)",
            expected=f"a YAML mapping, e.g.\n{_EXAMPLE}",
            recover=f"rewrite {path.name} as a mapping of the five keys.",
        )
    unknown = sorted(set(data) - set(_KEYS))
    if unknown:
        raise _fail(
            what=f"unknown key(s) {unknown} that nothing reads.",
            path=path,
            key=unknown[0],
            expected=f"only {list(_KEYS)}.",
            recover=f"remove {unknown} from {path.name}.",
        )
    _check_types(data, path=path)

    installed = _installed_typst_version(path)
    if data["typst_version"] != installed:
        raise _fail(
            what=f"'typst_version' is {data['typst_version']!r} but `typst --version` reports {installed!r}.",
            path=path,
            key="typst_version",
            expected=f"the installed version, e.g. typst_version: {installed!r}, matching environment.yml.",
            recover="make environment.yml, this key and the installed Typst agree, then re-run "
            "`conda env update -f environment.yml --prune`.",
        )
    font_dir = REPO_ROOT / data["font_dir"]
    if not font_dir.is_dir() or not any(font_dir.glob("*.ttf")):
        raise _fail(
            what=f"'font_dir' {data['font_dir']!r} ({font_dir}) does not exist or holds no .ttf file.",
            path=path,
            key="font_dir",
            expected="a directory under the repository root holding .ttf files, e.g. font_dir: fonts",
            recover=f"point 'font_dir' in {path.name} at the repository's fonts directory.",
        )
    return TypesetConfig(
        typst_version=data["typst_version"],
        font_dir=data["font_dir"],
        creation_timestamp=data["creation_timestamp"],
        raster_dpi=data["raster_dpi"],
        template=data["template"],
    )


def layout_page(layout: dict, *, layout_path: str, layout_id: str) -> PageSpec:
    """Return a layout's physical page, validating its `page:` key.

    Args:
        layout: The resolved layout dict.
        layout_path: Path to the layout YAML, for diagnostics.
        layout_id: The layout's id.

    Returns:
        The sheet's size for `size`, or the roll's width and no height for `width_mm`.

    Raises:
        LayoutSchemaError: If `page:` is absent or malformed.
    """
    kind, value = validate_page(layout, layout_path=layout_path, layout_id=layout_id)
    if kind == "size":
        width, height = PAGE_SIZES_MM[str(value)]
        return PageSpec(width_mm=width, height_mm=height)
    return PageSpec(width_mm=float(value), height_mm=None)
