"""Shared test configuration and helpers (local-only; tests/ is gitignored).

Puts the repository root on sys.path so `import generators...` works without
per-file boilerplate, and provides the four-element fail-fast diagnostic
assertion required by the project's engineering standards.
"""

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generators import abn_register  # noqa: E402

# The register loader before the session patch below, for the tests of the loader itself.
REAL_LOAD_ABN_REGISTER = abn_register.load_abn_register

# The draws the committed seed made that the real register holds, in draw order: the screen redrew
# each, and these four are the only ABNs the real register changes in the committed corpus. A register
# of exactly these reproduces the committed corpus without the real file;
# tests/test_abn_register.py::test_seed_register_hits_are_the_conftest_stand_in checks the list
# against the real register wherever it is built, and prints the new list after a reseed moves it.
SEED_REGISTER_HITS = ("58 737 631 165", "54 419 049 663", "27 507 527 354", "45 677 378 263")


def seed_register_stand_in() -> abn_register.AbnRegister:
    """A register holding only the committed seed's register hits."""
    return abn_register.AbnRegister.from_abns(int(abn.replace(" ", "")) for abn in SEED_REGISTER_HITS)


@pytest.fixture(scope="session", autouse=True)
def _stand_in_abn_register():
    """Screen generated ABNs against the committed seed's register hits for the whole session.

    The real register is a large file built per machine from the public extract
    (scripts/build_abn_register.py), so the suite must not depend on it. The
    stand-in rejects exactly the draws the real register rejected, so an
    in-memory seed reproduces the committed corpus, which is what the
    committed-corpus comparisons expect. Session scope, so module-scoped fixtures
    that seed the corpus are covered too.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(abn_register, "load_abn_register", lambda pools, pools_path: seed_register_stand_in())
        yield


def pytest_configure(config: pytest.Config) -> None:
    """Register the markers this suite uses, so none is an unknown-marker warning."""
    config.addinivalue_line(
        "markers",
        "slow: forced-render tests that draw every case on every layout (minutes each); a task gate "
        "runs `pytest tests/ -m 'not slow'` plus the slow tests of the layouts it touches, and the full "
        "suite runs them all",
    )


def assert_diagnostic_error(
    error: BaseException | str, path: Path | None = None, token: str | None = None
) -> None:
    """Assert a fail-fast diagnostic carries all four required elements.

    Two conventions are in use. Labelled blocks (content_engine, layout_budgets,
    common, the DSL) carry literal "What:", "Where:", "Expected:" and "Recover:"
    lines (each label at the start of a line, leading whitespace allowed): call
    with the error alone. Config-style prose (exporters/config.py)
    names the resolved file path and the offending key, with inline "Example:"
    and "Remediation:" sections: call with the error, the path passed to the
    function under test, and the key or value the message must name.

    Args:
        error: The raised exception, or its message.
        path: Config path given to the function under test (config style only).
        token: Key or value the message must name (config style only).

    Raises:
        AssertionError: If any of the four elements is absent.
        ValueError: If exactly one of `path` and `token` is given.
    """
    if (path is None) != (token is None):
        raise ValueError("assert_diagnostic_error: pass both `path` and `token`, or neither")
    # str(KeyError("msg")) is repr("msg"): a quote before the first label and literal \n
    message = str(error.args[0]) if isinstance(error, KeyError) and error.args else str(error)
    low = message.lower()
    if path is None:
        for element in ("what", "where", "expected", "recover"):
            assert re.search(rf"(?im)^\s*{element}:", message), f"missing {element.upper()} in: {message}"
        return
    resolved = str(path.resolve())
    assert token is not None and token in message, f"missing WHAT ({token!r}) in: {message}"
    assert resolved in message, f"missing WHERE ({resolved}) in: {message}"
    assert "example:" in low, f"missing EXPECTED (Example:) in: {message}"
    assert "remediation:" in low, f"missing RECOVER (Remediation:) in: {message}"


@pytest.fixture
def drawn_keys(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Collect every DX_ key printed by a block whose drawing recorded distractor ink.

    See `tests.distractor_helpers.record_drawn_keys`; clear the set between renders.
    """
    from tests.distractor_helpers import record_drawn_keys

    return record_drawn_keys(monkeypatch)


@pytest.fixture
def every_item_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Select every listed distractor item on every document, instead of the seeded selection."""
    from tests.distractor_helpers import force_every_item

    force_every_item(monkeypatch)


@pytest.fixture
def synthetic_layouts_skip_label_sites(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the label-site check for a hand-built layout named `layout_under_test`, and only for it.

    The DSL unit tests validate small synthetic bodies under a receipt or invoice document type to test
    one rule each; those bodies carry no label sites, which the layout schema rightly refuses on a real
    layout. Real layouts are still checked in full (tests/labels/test_schema_sites.py covers every rule).
    """
    from generators.layout_dsl import schema

    real = schema._validate_label_sites

    def checked(layout: dict, *, layout_id: str, layout_path: str, doc_type: str) -> None:
        if layout_id != "layout_under_test":
            real(layout, layout_id=layout_id, layout_path=layout_path, doc_type=doc_type)

    monkeypatch.setattr(schema, "_validate_label_sites", checked)


@pytest.fixture
def synthetic_invoice_links(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every synthetic invoice entry a transaction link, as the seed gives every corpus invoice one.

    The links file is read through `generators.distractors.values._load_links`; this returns the real
    file's links plus `tests.invoice_fixtures.synthetic_links()`, so a stamp date drawn for a synthetic
    entry reads its own link rather than failing for want of one.
    """
    from generators.distractors import values
    from tests.invoice_fixtures import synthetic_links

    real = values._load_links

    def merged(path: Path) -> dict:
        return {**real(path), **synthetic_links()}

    monkeypatch.setattr(values, "_load_links", merged)


@pytest.fixture(scope="session")
def export(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """ONE full eval-set export of the real corpus, shared by every test that reads it.

    Session-scoped because the export renders the whole corpus (about 45 s). It is
    shared READ-ONLY: tests must never write into, or delete from, these directories.
    A test that needs to mutate an export (force, refusal) builds its own under
    its own tmp_path.

    Returns:
        The `export_eval_set` summary plus `out`, the parent directory.
    """
    from tests.eval_export_tools import export_into

    return export_into(tmp_path_factory.mktemp("shared_eval_export"))


@pytest.fixture(scope="session")
def derived(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """ONE generate + derive of the real corpus, shared by tests that read derived outputs.

    Session-scoped because `generate` renders every page (slow). It is shared
    READ-ONLY: tests must never write into, or delete from, the returned
    directory. A test that needs to mutate derived files copies what it needs
    into its own tmp_path.

    Returns:
        The scratch `derived/` directory (geometry.jsonl, cord.jsonl, docile.jsonl, ...).
    """
    from tests.derived_tools import derive_into

    root = tmp_path_factory.mktemp("shared_derive")
    derive_into(root)
    return root / "derived"
