"""Ensure Pillow is pinned so FreeType font metrics are reproducible.

fit_text() measurement depends on Pillow's FreeType version; an unpinned Pillow
could shift getbbox() widths and change rendered pixels / fit decisions.
"""

import re

import PIL
import yaml

from tests.conftest import REPO_ROOT


def _pip_pins() -> list[str]:
    """Return every pip requirement string declared in environment.yml."""
    env = yaml.safe_load((REPO_ROOT / "environment.yml").read_text())
    pins: list[str] = []
    for dep in env["dependencies"]:
        if isinstance(dep, dict):
            pins.extend(dep.get("pip", []))
        else:
            pins.append(str(dep))
    return pins


def test_pillow_is_pinned():
    """Pillow is pinned exactly in environment.yml and the installed version matches the pin."""
    pillow = [p for p in _pip_pins() if re.match(r"(?i)pillow\b", p)]
    assert len(pillow) == 1, f"expected exactly one pillow requirement, got {pillow}"
    match = re.fullmatch(r"(?i)pillow==(\d+(?:\.\d+)+)", pillow[0].strip())
    assert match, f"pillow must be pinned with '==', got {pillow[0]!r}"
    assert PIL.__version__ == match.group(1), (
        f"installed Pillow {PIL.__version__} differs from the environment.yml pin {match.group(1)}"
    )


from importlib.metadata import version  # noqa: E402

import pytest  # noqa: E402

ENV = (REPO_ROOT / "environment.yml").read_text()


@pytest.mark.parametrize("dist", ["segno", "python-barcode", "zxing-cpp"])
def test_code_libraries_are_pinned_and_installed_at_the_pin(dist: str) -> None:
    """Barcode and QR module patterns must not drift between machines."""
    pins = re.findall(rf"^\s*-\s*{re.escape(dist)}==([\w.]+)\s*$", ENV, re.MULTILINE)
    assert len(pins) == 1, f"environment.yml must pin {dist} exactly once with =="
    assert version(dist) == pins[0]


# Augraphy's dependency block, pinned on 6 October 2026 because degraded pixels depend on
# it, plus the three packages a `--prune` once removed.
AUGRAPHY_STACK = [
    "opencv-python-headless",
    "numba",
    "llvmlite",
    "scikit-image",
    "scipy",
    "scikit-learn",
    "lazy_loader",
    "imageio",
    "tifffile",
    "networkx",
    "joblib",
    "threadpoolctl",
    "narwhals",
    "requests",
    "urllib3",
    "idna",
    "certifi",
    "charset-normalizer",
    "python-dateutil",
    "six",
    "typing_extensions",
]


@pytest.mark.parametrize("dist", AUGRAPHY_STACK)
def test_augraphy_stack_is_pinned_and_installed_at_the_pin(dist: str) -> None:
    """Every package under the degradation tiers is pinned exactly and installed at that pin."""
    pins = re.findall(rf"^\s*-\s*{re.escape(dist)}==([\w.]+)\s*$", ENV, re.MULTILINE)
    assert len(pins) == 1, f"environment.yml must pin {dist} exactly once with =="
    assert version(dist) == pins[0], f"{dist}: installed {version(dist)}, pinned {pins[0]}"


POST_INSTALL = (REPO_ROOT / "scripts" / "post_install.sh").read_text()


def test_augraphy_is_not_installed_from_environment_yml() -> None:
    """Listing augraphy in environment.yml would pull in the GUI opencv and break `import cv2`."""
    listed = re.findall(r"^\s*-\s*augraphy\b", ENV, re.MULTILINE)
    assert not listed, "augraphy must be installed by scripts/post_install.sh, not environment.yml"


def test_augraphy_is_pinned_once_in_post_install_and_installed_at_the_pin() -> None:
    """The post-install script holds the single augraphy pin, and the environment matches it."""
    pins = re.findall(r'^AUGRAPHY_VERSION="([\w.]+)"$', POST_INSTALL, re.MULTILINE)
    assert len(pins) == 1, "scripts/post_install.sh must set AUGRAPHY_VERSION exactly once"
    assert version("augraphy") == pins[0], f"augraphy: installed {version('augraphy')}, pinned {pins[0]}"


def test_post_install_reads_the_headless_pin_from_environment_yml() -> None:
    """The headless OpenCV version is declared once, in environment.yml."""
    assert "opencv-python-headless==" in ENV
    assert not re.search(r'HEADLESS_VERSION="[\d.]+"', POST_INSTALL), "do not repeat the pin in the script"


def test_no_gui_opencv_is_installed() -> None:
    """The full GUI build would displace the headless cv2 and need libGL."""
    from importlib.metadata import PackageNotFoundError

    with pytest.raises(PackageNotFoundError):
        version("opencv-python")


def test_the_pin_check_can_fail() -> None:
    """A drifted pin is reported, so the parametrised check is not vacuous."""
    (current,) = re.findall(r"^\s*-\s*numba==([\w.]+)\s*$", ENV, re.MULTILINE)
    drifted = ENV.replace(f"numba=={current}", "numba==0.0.1")
    (pin,) = re.findall(r"^\s*-\s*numba==([\w.]+)\s*$", drifted, re.MULTILINE)
    assert version("numba") != pin
