"""The field-provider registry is a leaf module, importable alone and in any order."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT, assert_diagnostic_error

REGISTRY_PATH = REPO_ROOT / "generators" / "layout_dsl" / "provider_registry.py"
DISTRACTOR_PROVIDERS = "{'receipt_distractors', 'invoice_distractors', 'bank_distractors'}"


def _imported_modules(path: str) -> set[str]:
    tree = ast.parse((REPO_ROOT / path).read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_the_registry_imports_nothing_from_the_project() -> None:
    """A leaf: no generators module but the import-free key modules, so it can never sit in a cycle."""
    imported = _imported_modules("generators/layout_dsl/provider_registry.py")
    project = {m for m in imported if m.startswith("generators")}
    assert project <= {"generators.distractors.keys", "generators.labels.keys"}, imported
    for leaf in ("generators/distractors/keys.py", "generators/labels/keys.py"):
        assert not {m for m in _imported_modules(leaf) if m.startswith("generators")}, leaf


def test_field_providers_has_no_deferred_bottom_of_file_import() -> None:
    tree = ast.parse((REPO_ROOT / "generators/layout_dsl/field_providers.py").read_text())
    last_def = max(
        n.lineno for n in tree.body if isinstance(n, ast.FunctionDef | ast.ClassDef | ast.Assign)
    )
    late = [
        n.lineno for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom) and n.lineno > last_def
    ]
    assert not late, late


def test_distractor_providers_import_the_registry_not_field_providers() -> None:
    imported = _imported_modules("generators/distractors/providers.py")
    assert "generators.layout_dsl.provider_registry" in imported
    assert "generators.layout_dsl.field_providers" not in imported


@pytest.mark.parametrize(
    "first",
    [
        "generators.layout_dsl.provider_registry",
        "generators.distractors.providers",
        "generators.layout_dsl.field_providers",
        "generators.layout_dsl.schema",
        "generators.pipeline",
    ],
)
def test_any_import_order_registers_every_provider(first: str) -> None:
    code = (
        f"import {first}; "
        "import generators.layout_dsl.provider_registry as r; "
        "names = set(r.field_provider_names()); "
        f"assert {DISTRACTOR_PROVIDERS} | {{'receipt_pos', 'computed_totals'}} <= names, names"
    )
    subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, check=True)


def test_every_public_registry_name_is_still_importable_from_field_providers() -> None:
    from generators.layout_dsl import field_providers as fp
    from generators.layout_dsl import provider_registry as reg

    for name in (
        "FieldProvider",
        "FieldProviderError",
        "field_provider",
        "get_field_provider",
        "field_provider_names",
        "field_provider_emits",
        "field_provider_param_keys",
        "is_distractor_provider",
        "distractor_doc_type",
    ):
        assert getattr(fp, name) is getattr(reg, name), name


# --- registration diagnostics name the real registration site -------------------------------------

THIS_FILE = str(Path(__file__).resolve())
PROVIDERS_FILE = str(REPO_ROOT / "generators" / "distractors" / "providers.py")


@pytest.fixture
def scratch(monkeypatch: pytest.MonkeyPatch) -> None:
    from generators.layout_dsl import provider_registry as reg

    for attr in ("_REGISTRY", "_PARAM_KEYS", "_EMITS", "_DISTRACTOR"):
        monkeypatch.setattr(reg, attr, dict(getattr(reg, attr)))


def _where(err: pytest.ExceptionInfo) -> str:
    return next(line for line in str(err.value).splitlines() if line.strip().startswith("Where:"))


def _probe(entry: dict, params: dict) -> dict[str, str]:
    return {}


@pytest.mark.parametrize(
    ("name", "emits", "distractor"),
    [
        ("zz_scored", ("TOTAL_AMOUNT",), None),
        ("zz_dx_plain", ("DX_ZZ",), None),
        ("zz_plain_dx", ("ZZ_PLAIN",), "receipt"),
    ],
)
def test_a_registration_failure_names_the_decorated_functions_file(
    scratch: None, name: str, emits: tuple[str, ...], distractor: str | None
) -> None:
    from generators.layout_dsl.provider_registry import FieldProviderError, field_provider

    with pytest.raises(FieldProviderError) as err:
        if distractor is None:
            field_provider(name, emits=emits)(_probe)
        else:
            field_provider(name, emits=emits, distractor=distractor)(_dx_probe)
    where = _where(err)
    assert THIS_FILE in where and "field_providers.py" not in where


def test_a_duplicate_names_both_sites(scratch: None) -> None:
    from generators.layout_dsl.provider_registry import FieldProviderError, field_provider

    with pytest.raises(FieldProviderError) as err:
        field_provider("invoice_distractors", emits=("DX_ZZ",), distractor="invoice")(_dx_probe)
    where = _where(err)
    assert THIS_FILE in where and PROVIDERS_FILE in where


def test_the_distractor_providers_are_sited_in_their_own_module() -> None:
    from generators.layout_dsl.provider_registry import registration_site

    assert registration_site("receipt_distractors").startswith(PROVIDERS_FILE)
    assert "field_providers.py" in registration_site("receipt_pos")


def test_the_unknown_provider_advice_names_the_registry() -> None:
    from generators.layout_dsl.provider_registry import FieldProviderError, get_field_provider

    with pytest.raises(FieldProviderError) as err:
        get_field_provider("zz_nope")
    assert "field_providers.py" not in str(err.value)
    assert "provider_registry.py" in str(err.value)


def test_the_package_docstring_has_no_em_dash() -> None:
    import generators.layout_dsl as pkg

    assert "\u2014" not in (pkg.__doc__ or "")


# --- the document type is registration data -------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "doc_type"),
    [
        ("receipt_distractors", "receipt"),
        ("invoice_distractors", "invoice"),
        ("bank_distractors", "bank_statement"),
    ],
)
def test_each_distractor_provider_registers_its_document_type(name: str, doc_type: str) -> None:
    from generators.layout_dsl.provider_registry import (
        distractor_doc_type,
        distractor_provider_names,
        is_distractor_provider,
    )

    assert is_distractor_provider(name)
    assert distractor_doc_type(name) == doc_type
    assert distractor_provider_names(doc_type) == [name]


def test_an_ordinary_provider_has_no_document_type() -> None:
    from generators.layout_dsl.provider_registry import FieldProviderError, distractor_doc_type

    with pytest.raises(FieldProviderError) as err:
        distractor_doc_type("receipt_pos")
    assert_diagnostic_error(err.value)
    assert "receipt_pos" in str(err.value)


@pytest.mark.parametrize("bad", ["payslip", "receipts", "", True])
def test_a_distractor_document_type_outside_the_schema_is_refused(scratch: None, bad: object) -> None:
    from generators.layout_dsl.provider_registry import FieldProviderError, field_provider

    with pytest.raises(FieldProviderError) as err:
        field_provider("zz_dx", emits=("DX_ZZ",), distractor=bad)(_dx_probe)  # type: ignore[call-overload]
    assert_diagnostic_error(err.value)
    assert "extraction_schema.yml" in str(err.value) and "bank_statement" in str(err.value)


# --- typed providers: the two call shapes are checked at registration and by mypy ------------------


def _dx_probe(entry: dict, params: dict, *, items: list[str]) -> dict[str, str]:
    return {}


def _positional_items(entry: dict, params: dict, items: list[str]) -> dict[str, str]:
    return {}


def _kwargs_only(entry: dict, params: dict, **kwargs: object) -> dict[str, str]:
    return {}


@pytest.mark.parametrize(
    "func", [_probe, _positional_items, _kwargs_only], ids=["none", "positional", "kwargs"]
)
def test_a_distractor_provider_without_a_keyword_only_items_is_refused(scratch: None, func: object) -> None:
    from generators.layout_dsl.provider_registry import FieldProviderError, field_provider

    with pytest.raises(FieldProviderError) as err:
        field_provider("zz_dx_sig", emits=("DX_ZZ",), distractor="receipt")(func)  # type: ignore[arg-type]
    assert_diagnostic_error(err.value)
    message = str(err.value)
    assert "zz_dx_sig" in message and "*, items: list[str]" in message
    assert THIS_FILE in _where(err)


def test_an_ordinary_provider_declaring_items_is_refused(scratch: None) -> None:
    from generators.layout_dsl.provider_registry import FieldProviderError, field_provider

    with pytest.raises(FieldProviderError) as err:
        field_provider("zz_plain_sig", emits=("ZZ_PLAIN",))(_dx_probe)  # type: ignore[arg-type]
    assert_diagnostic_error(err.value)
    message = str(err.value)
    assert "zz_plain_sig" in message and "items" in message and "distractor=" in message
    assert THIS_FILE in _where(err)


def test_well_typed_providers_register(scratch: None) -> None:
    from generators.layout_dsl.provider_registry import (
        field_provider,
        get_distractor_provider,
        get_ordinary_provider,
    )

    field_provider("zz_ok_plain", emits=("ZZ_OK",))(_probe)
    field_provider("zz_ok_dx", emits=("DX_ZZ_OK",), distractor="receipt")(_dx_probe)
    assert get_ordinary_provider("zz_ok_plain") is _probe
    assert get_distractor_provider("zz_ok_dx") is _dx_probe


@pytest.mark.parametrize(
    ("getter", "name", "token"),
    [
        ("get_distractor_provider", "receipt_pos", "ordinary"),
        ("get_ordinary_provider", "receipt_distractors", "distractor"),
        ("get_distractor_provider", "zz_nope", "zz_nope"),
        ("get_ordinary_provider", "zz_nope", "zz_nope"),
    ],
)
def test_the_typed_lookups_refuse_the_other_kind(getter: str, name: str, token: str) -> None:
    from generators.layout_dsl import provider_registry as reg

    with pytest.raises(reg.FieldProviderError) as err:
        getattr(reg, getter)(name)
    assert_diagnostic_error(err.value)
    assert name in str(err.value) and token in str(err.value)


def test_the_protocols_are_re_exported_from_field_providers() -> None:
    from generators.layout_dsl import field_providers as fp
    from generators.layout_dsl import provider_registry as reg

    for name in ("FieldProvider", "DistractorProvider", "get_distractor_provider", "get_ordinary_provider"):
        assert getattr(fp, name) is getattr(reg, name), name


_MYPY_SNIPPET = """
from generators.layout_dsl.provider_registry import (
    field_provider,
    get_distractor_provider,
    get_ordinary_provider,
)


@field_provider("snippet_plain", emits=("SNIPPET",))
def plain(entry: dict, params: dict) -> dict[str, str]:
    return {}


@field_provider("snippet_dx", emits=("DX_SNIPPET",), distractor="receipt")
def dx(entry: dict, params: dict, *, items: list[str]) -> dict[str, str]:
    return {}


@field_provider("snippet_bad_dx", emits=("DX_BAD",), distractor="receipt")  # LINE_BAD_DX
def bad_dx(entry: dict, params: dict) -> dict[str, str]:
    return {}


@field_provider("snippet_bad_plain", emits=("BAD",))  # LINE_BAD_PLAIN
def bad_plain(entry: dict, params: dict, *, items: list[str]) -> dict[str, str]:
    return {}


get_ordinary_provider("x")({}, {})
get_distractor_provider("x")({}, {}, items=[])
get_ordinary_provider("x")({}, {}, items=[])  # LINE_PLAIN_WITH_ITEMS
get_distractor_provider("x")({}, {})  # LINE_DX_WITHOUT_ITEMS
"""


def test_mypy_checks_both_call_shapes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A wrong call or registration of either kind is a type error; the right ones are clean."""
    from mypy import api

    monkeypatch.chdir(REPO_ROOT)
    snippet = tmp_path / "snippet.py"
    snippet.write_text(_MYPY_SNIPPET)
    stdout, _, _ = api.run([str(snippet), "--ignore-missing-imports", "--cache-dir", str(tmp_path / "c")])
    # Only the snippet's own lines: an error mypy finds in an imported module is the gate's to report.
    flagged = {
        int(line.split(":")[1])
        for line in stdout.splitlines()
        if ": error:" in line and line.startswith(str(snippet))
    }
    # A decorator's error is reported on the decorator line itself.
    expected = {n for n, text in enumerate(_MYPY_SNIPPET.splitlines(), start=1) if "# LINE_" in text}
    assert flagged == expected, stdout
