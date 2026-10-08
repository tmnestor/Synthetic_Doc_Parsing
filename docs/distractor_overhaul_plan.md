# Distractor Overhaul: Implementation Plan

Execute task by task, in order. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make receipts, invoices and bank statements realistically hard to extract from, by adding unscored distractors and label variation, and ship the result as dated, standalone, documented snapshots.

**Architecture:** Distractors are drawn in two layers behind render flags: inline blocks in each layout's DSL body, and an overlay drawn after the body using the recorded field geometry. Three flag settings produce three clean halves (plain, distractor, label-varied) from identical ground truth; the degraded half is built from the distractor receipts.

**Tech Stack:** Python 3.12, Pillow 12.2.0, PyYAML, Typer, OpenCV, Augraphy, `segno`, `python-barcode`, pytest with pytest-cov, conda env `synthetic`.

**Spec:** `docs/distractor_overhaul_design.md` (commit 8aaf605). Read it before any task; this plan argues from it.

## Global Constraints

- Conda env `synthetic`; run every tool as `conda run -n synthetic <command>` from the repository root.
- Pillow stays pinned at `12.2.0`: font metrics, and therefore every pixel hash, depend on it.
- Line length 108; Python 3.12 type hints (`X | Y`); `pathlib.Path` for every path; Google-style docstrings.
- YAML is the single source of truth: no Python defaults for configuration, every key required, missing keys fail fast.
- Every fail-fast error names what is wrong, where to fix it (file and dotted key), what valid looks like, and how to recover. Tests assert this with `assert_diagnostic_error`.
- In `except` blocks always `raise ... from err` or `from None`.
- `tests/` is gitignored: tests are local-only and are never committed. Test-only work has no commit step.
- Tracked files contain no tooling references, no real business names, and no value copied from the ground truth (invent example values and grep `ground_truth/` to prove absence).
- Prose uses British spelling and no em-dashes.
- Work happens on `main`; each stage merges on its own.
- All generated content is written under `../evaluation_data/`, never inside the repository: every snapshot, and the working directories `output/` and `derived/` (Task 0.10). Test scratch output goes to pytest's `tmp_path`.
- Quality gate before every commit: `pytest tests/` passes **and collects tests** (exit code 5, "no tests ran", is a failure), `ruff check --fix --ignore ARG001,ARG002,F841 *.py`, `ruff format .`, `mypy . --ignore-missing-imports`.

## Review Focus

1. **A ground-truth entry whose layout is missing.** `generators/pipeline.py:297` skips it with a yellow warning instead of failing, so anything built from `generate` output can silently shrink. The snapshot harness renders directly and must raise; Task 0.2 pins exactly 165 pages, 55 per type.
2. **A baseline re-blessed on the wrong Pillow.** A different FreeType bakes different metrics into every hash. Task 0.2's regenerate script refuses unless the installed Pillow equals the `environment.yml` pin, and a test proves the refusal.
3. **Running from a directory other than the repository root.** Renderers may resolve fonts or config relative to the working directory. Task 0.2 renders one page per type after `monkeypatch.chdir(tmp_path)`; a failure is a finding to report, not something to paper over.
4. **Derived outputs that embed absolute paths.** Hashes would differ per machine and per temporary directory. Task 0.3 derives into two different directories and asserts identical normalised hashes.
5. **A suite that collects nothing, or silently loses modules again.** Task 0.9 adds an integrity test: every live module in `tests/RECOVERED_INVENTORY.md` exists as a file or is explicitly marked dropped with a reason.

---

## Roadmap

One plan per stage. Stage 0 is written in full below. Each later stage's plan is written when that stage starts, against the code that exists by then, because each depends on interfaces the previous stage fixes.

| Stage | Delivers | Exit criterion |
| --- | --- | --- |
| 0 | Rebuilt local test suite; fresh pixel and derived baselines from HEAD; `output/` and `derived/` moved under `../evaluation_data/` | Suite green, coverage of `generators`, `linking`, `scripts` at least 80% |
| 1 | `segno`, `python-barcode`, Caveat/Kalam fonts; `barcode`, `qrcode`, `logo` primitives | Decode round-trip tests pass; plain snapshot unchanged |
| 2 | `config/distractors.yml`; `receipt_distractors`, `invoice_distractors`, `bank_distractors`; value validator | Every validator rule has a failing fixture; plain snapshot unchanged |
| 3 | Receipt distractors on all six layouts | Distractor renders pass density bounds; plain snapshot unchanged |
| 4 | Invoice rendering fixes; expanded catalogue on four layouts; four industry layouts; reseed | Fixes have regression tests; snapshot re-blessed once, deliberately |
| 5 | Overlay: PAID stamp, bank annotations, geometry and contrast checks | No ink or stamp box meets a scored box across 165 cases |
| 6 | Label variation: `label_styles` provider, `{LABEL_*}` bindings, unambiguity validator, sidecar | Each unambiguity rule has a failing fixture |
| 7 | Snapshot export into `../evaluation_data/` (YAML key `eval_set.output_root`, `--out` removed): four halves, `MANIFEST.json`, README, tools | One flipped byte fails `verify_snapshot.py`; dirty tree refused (recorded instead since 9 October 2026); missing root fails fast |

"Plain snapshot unchanged" means `tests/test_pixel_snapshot.py` passes with no re-bless. History before Stage 0 is not preserved: no external team has used the corpus, so Stage 0 captures fresh baselines from HEAD rather than reconstructing old ones.

---

## Stage 0: Rebuild the local test suite

**Background.** On 6 October 2026 `tests/` held no test sources: only fixtures and bytecode. The bytecode yielded every test name and docstring (939 tests, 68 modules), now in `tests/RECOVERED_INVENTORY.md`. Seven modules are marked obsolete there (their code is gone, or Task 0.2 replaces them). The docstrings are the specification for rebuilding each test; bodies were not recoverable.

**Rules for every rebuild task (0.4 to 0.8).**

- Work through the task's inventory sections line by line. For each line: read the docstring, read the code it names, write the test, run it, tick the line.
- If the behaviour no longer exists in the code, do not resurrect it: change the line to `- [x] ~~name~~ dropped: <reason>`.
- If a rebuilt test fails against current code, stop. Either the expectation is stale (adjust it and note why on the inventory line) or it is a real bug: report it to Tod and do not fix it inside Stage 0.
- Fixture files the old tests used still exist under `tests/fixtures/`; reuse them only after confirming they match current behaviour.

### Task 0.1: Test scaffolding and the diagnostic helper

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/__init__.py`, `tests/layout_dsl/__init__.py`, `tests/exporters/__init__.py`, `tests/degradation/__init__.py` (all empty; needed because `test_config.py` and `test_geometry.py` names recur across directories)
- Test: `tests/test_conftest.py`

**Interfaces:**
- Produces: `assert_diagnostic_error(error: BaseException | str, path: Path | None = None, token: str | None = None) -> None`, importable as `from tests.conftest import assert_diagnostic_error`.

- [ ] **Step 1: Write the failing test**

```python
"""The diagnostic helper must fail when any element is missing, or it proves nothing."""

from pathlib import Path

import pytest

from tests.conftest import assert_diagnostic_error

COMPLETE = "What: x is missing\nWhere: a.yml -> k\nExpected: k: 1\nRecover: add k"


def test_complete_labelled_message_passes() -> None:
    assert_diagnostic_error(COMPLETE)
    assert_diagnostic_error(ValueError(COMPLETE))


@pytest.mark.parametrize("label", ["What:", "Where:", "Expected:", "Recover:"])
def test_each_missing_label_fails(label: str) -> None:
    with pytest.raises(AssertionError):
        assert_diagnostic_error(COMPLETE.replace(label, "Nope:"))


def test_config_style_requires_path_token_example_and_remediation(tmp_path: Path) -> None:
    cfg = tmp_path / "export_config.yml"
    good = f"Missing key 'mode' in {cfg.resolve()}. Example: mode: strict. Remediation: add it."
    assert_diagnostic_error(ValueError(good), cfg, "mode")
    with pytest.raises(AssertionError):
        assert_diagnostic_error(ValueError(good.replace(str(cfg.resolve()), "somewhere")), cfg, "mode")
    with pytest.raises(AssertionError):
        assert_diagnostic_error(ValueError(good.replace("Remediation:", "Fix:")), cfg, "mode")


def test_path_without_token_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="both `path` and `token`"):
        assert_diagnostic_error(COMPLETE, tmp_path, None)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `conda run -n synthetic pytest tests/test_conftest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tests.conftest'` (or an ImportError for `assert_diagnostic_error`).

- [ ] **Step 3: Write the implementation**

```python
"""Shared test configuration and helpers (local-only; tests/ is gitignored).

Puts the repository root on sys.path so `import generators...` works without
per-file boilerplate, and provides the four-element fail-fast diagnostic
assertion required by the project's engineering standards.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def assert_diagnostic_error(
    error: BaseException | str, path: Path | None = None, token: str | None = None
) -> None:
    """Assert a fail-fast diagnostic carries all four required elements.

    Two conventions are in use. Labelled blocks (content_engine, layout_budgets,
    common, the DSL) carry literal "What:", "Where:", "Expected:" and "Recover:"
    lines: call with the error alone. Config-style prose (exporters/config.py)
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
    message = str(error)
    low = message.lower()
    if path is None:
        assert "what:" in low, f"missing WHAT in: {message}"
        assert "where:" in low, f"missing WHERE in: {message}"
        assert "expected:" in low, f"missing EXPECTED in: {message}"
        assert "recover:" in low, f"missing RECOVER in: {message}"
        return
    resolved = str(path.resolve())
    assert token is not None and token in message, f"missing WHAT ({token!r}) in: {message}"
    assert resolved in message, f"missing WHERE ({resolved}) in: {message}"
    assert "example:" in low, f"missing EXPECTED (Example:) in: {message}"
    assert "remediation:" in low, f"missing RECOVER (Remediation:) in: {message}"
```

Before relying on the config-style branch, open `generators/exporters/config.py` and confirm its messages really say "Example:" and "Remediation:". If they use other words, change the two asserts and the test to match the code, not the reverse.

- [ ] **Step 4: Run it to verify it passes**

Run: `conda run -n synthetic pytest tests/test_conftest.py -v`
Expected: 7 passed.

### Task 0.2: Fresh three-type pixel snapshot

Replaces the seven old per-type fixtures with one baseline captured from HEAD. This is the guard every later stage relies on.

**Files:**
- Create: `tests/snapshot_tools.py`, `tests/regenerate_pixel_snapshot.py`, `tests/test_pixel_snapshot.py`
- Create (by running the script): `tests/fixtures/pixel_snapshot.json`
- Delete: `tests/fixtures/{receipt,invoice,bank}_baseline_hashes.json`, `receipt_legacy_snapshot.json`, `invoice_legacy_snapshot.json`, `bank_dsl_pixel_hashes.json`, `bank_legacy_dsl_identity_snapshot.json`, `tests/fixtures/bank_dsl_pixel_reference/`

**Interfaces:**
- Consumes: `generators.loader.load_ground_truth(path) -> dict[case_id, entry]`, `load_layout_registry(path) -> dict[layout_id, layout]`, `render_receipt / render_invoice / render_bank_statement(entry, layout, *, geometry_out: dict | None) -> Image`. `geometry_out` is filled with `{"width": int, "height": int, "boxes": {field: [x0, y0, x1, y1] | ...}}`. Entries carry no `case_id`; inject it as `pipeline.generate` does.
- Produces: `digest(image) -> str`, `render_corpus() -> dict[str, dict]` (key `"{case_id}_{layout}"`, value `{"type", "hash", "size", "boxes"}`), `compare(pinned, current) -> list[str]`, `SNAPSHOT_PATH`, `EXPECTED_PER_TYPE = 55`.

- [ ] **Step 1: Write `tests/snapshot_tools.py`**

```python
"""Render the whole corpus and reduce each page to a comparable record."""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from generators.bank_statement import render_bank_statement
from generators.invoice import render_invoice
from generators.loader import load_ground_truth, load_layout_registry
from generators.receipt import render_receipt

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = REPO_ROOT / "tests" / "fixtures" / "pixel_snapshot.json"
EXPECTED_PER_TYPE = 55
DOC_TYPES: dict[str, tuple[str, Callable[..., Image.Image]]] = {
    "receipt": ("receipts", render_receipt),
    "invoice": ("invoices", render_invoice),
    "bank_statement": ("bank_statements", render_bank_statement),
}


def digest(image: Image.Image) -> str:
    """Hash a page's RGB pixels; PNG encoding is deliberately excluded."""
    return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()


def render_corpus(doc_types: tuple[str, ...] = tuple(DOC_TYPES)) -> dict[str, dict]:
    """Render every entry of the given types straight from ground truth.

    Renders directly rather than through `pipeline generate`, which skips an
    entry whose layout is missing; here a missing layout raises KeyError.
    """
    records: dict[str, dict] = {}
    for doc_type in doc_types:
        stem, render = DOC_TYPES[doc_type]
        entries = load_ground_truth(REPO_ROOT / "ground_truth" / f"{stem}.yml")
        layouts = load_layout_registry(REPO_ROOT / "config" / "layouts" / f"{stem}.yml")
        for case_id, raw in entries.items():
            entry = {**raw, "case_id": str(case_id)}
            geometry: dict = {}
            image = render(entry, layouts[entry["layout"]], geometry_out=geometry)
            records[f"{case_id}_{entry['layout']}"] = {
                "type": doc_type,
                "hash": digest(image),
                "size": [image.width, image.height],
                "boxes": json.loads(json.dumps(geometry["boxes"])),
            }
    return records


def compare(pinned: dict[str, dict], current: dict[str, dict]) -> list[str]:
    """List every difference between a pinned and a current snapshot."""
    problems: list[str] = []
    for key in sorted(set(pinned) | set(current)):
        if key not in current:
            problems.append(f"{key}: pinned but no longer rendered")
        elif key not in pinned:
            problems.append(f"{key}: rendered but not pinned")
        else:
            problems += [
                f"{key}: {part} differs"
                for part in ("hash", "size", "boxes")
                if pinned[key][part] != current[key][part]
            ]
    return problems


def load_snapshot() -> dict[str, dict]:
    """Read the pinned snapshot."""
    return json.loads(SNAPSHOT_PATH.read_text())
```

- [ ] **Step 2: Write `tests/test_pixel_snapshot.py`**

```python
"""Permanent regression guard: every page renders exactly as pinned.

Re-bless only after an intended rendering change:

    conda run -n synthetic python tests/regenerate_pixel_snapshot.py --confirm
"""

import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from PIL import Image

from tests.snapshot_tools import (
    DOC_TYPES,
    EXPECTED_PER_TYPE,
    REPO_ROOT,
    SNAPSHOT_PATH,
    compare,
    digest,
    load_snapshot,
    render_corpus,
)


@pytest.fixture(scope="module")
def current() -> dict[str, dict]:
    return render_corpus()


def test_snapshot_pins_the_whole_corpus() -> None:
    counts = Counter(record["type"] for record in load_snapshot().values())
    assert counts == {doc_type: EXPECTED_PER_TYPE for doc_type in DOC_TYPES}


def test_every_page_matches_the_snapshot(current: dict[str, dict]) -> None:
    problems = compare(load_snapshot(), current)
    assert not problems, "\n".join(problems)


def test_one_changed_pixel_changes_the_digest() -> None:
    image = Image.new("RGB", (4, 4), "white")
    before = digest(image)
    image.putpixel((2, 2), (254, 255, 255))
    assert digest(image) != before


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda s: s["A"].update(hash="0"), "A: hash differs"),
        (lambda s: s["A"].update(size=[1, 1]), "A: size differs"),
        (lambda s: s["A"].update(boxes={}), "A: boxes differs"),
        (lambda s: s.pop("A"), "A: rendered but not pinned"),
        (lambda s: s.update(B=dict(s["A"])), "B: pinned but no longer rendered"),
    ],
)
def test_compare_reports_each_kind_of_difference(mutate, expected: str) -> None:
    current = {"A": {"type": "receipt", "hash": "h", "size": [2, 2], "boxes": {"X": [0, 0, 1, 1]}}}
    pinned = {key: dict(value) for key, value in current.items()}
    mutate(pinned)
    assert expected in compare(pinned, current)


def test_rendering_does_not_depend_on_the_working_directory(
    current: dict[str, dict], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    elsewhere = render_corpus(("receipt",))
    assert not compare({k: v for k, v in current.items() if v["type"] == "receipt"}, elsewhere)


def test_regenerate_without_confirm_writes_nothing() -> None:
    before = SNAPSHOT_PATH.stat().st_mtime_ns
    result = subprocess.run(
        [sys.executable, "tests/regenerate_pixel_snapshot.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "dry run" in result.stdout.lower()
    assert SNAPSHOT_PATH.stat().st_mtime_ns == before


def test_regenerate_refuses_an_unpinned_pillow() -> None:
    result = subprocess.run(
        [sys.executable, "tests/regenerate_pixel_snapshot.py", "--confirm", "--pretend-pillow", "0.0.0"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "pillow" in (result.stdout + result.stderr).lower()
```

- [ ] **Step 3: Run it to verify it fails**

Run: `conda run -n synthetic pytest tests/test_pixel_snapshot.py -v`
Expected: FAIL. `FileNotFoundError` for `pixel_snapshot.json`, and the regenerate tests fail because the script does not exist. The pure-function tests (`digest`, `compare`) should already pass.

- [ ] **Step 4: Write `tests/regenerate_pixel_snapshot.py`**

```python
"""Re-capture tests/fixtures/pixel_snapshot.json. Dry run unless --confirm.

Refuses to write unless the installed Pillow equals the environment.yml pin,
because font metrics, and so every hash, depend on it.
"""

import argparse
import json
import re
import sys
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.snapshot_tools import REPO_ROOT, SNAPSHOT_PATH, compare, render_corpus  # noqa: E402


def pinned_pillow() -> str:
    """Read the Pillow pin from environment.yml."""
    match = re.search(r"pillow==([\d.]+)", (REPO_ROOT / "environment.yml").read_text())
    if match is None:
        sys.exit("environment.yml has no 'pillow==X.Y.Z' pin; add one before capturing a snapshot.")
    return match.group(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="write the snapshot")
    parser.add_argument("--pretend-pillow", help=argparse.SUPPRESS)
    args = parser.parse_args()

    installed = args.pretend_pillow or version("pillow")
    if args.confirm and installed != pinned_pillow():
        sys.exit(f"Installed Pillow {installed} differs from the pin {pinned_pillow()}; refusing to write.")

    current = render_corpus()
    old = json.loads(SNAPSHOT_PATH.read_text()) if SNAPSHOT_PATH.exists() else {}
    changes = compare(old, current)
    print(f"{len(current)} pages rendered; {len(changes)} differences from the pinned snapshot.")
    for line in changes[:20]:
        print(f"  {line}")
    if not args.confirm:
        print("Dry run: nothing written. Pass --confirm to write.")
        return
    SNAPSHOT_PATH.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
    print(f"Wrote {SNAPSHOT_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Capture the baseline from HEAD**

Run: `conda run -n synthetic python tests/regenerate_pixel_snapshot.py --confirm`
Expected: `165 pages rendered; 165 differences ...` (everything is new), then `Wrote .../pixel_snapshot.json`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `conda run -n synthetic pytest tests/test_pixel_snapshot.py -v`
Expected: all pass. If `test_rendering_does_not_depend_on_the_working_directory` fails, report it as a finding (Review Focus 3) and mark it `xfail(strict=True)` with the reason, so the suite records the defect instead of hiding it.

- [ ] **Step 7: Delete the superseded fixtures**

Run: `\rm -rf tests/fixtures/receipt_baseline_hashes.json tests/fixtures/invoice_baseline_hashes.json tests/fixtures/bank_baseline_hashes.json tests/fixtures/receipt_legacy_snapshot.json tests/fixtures/invoice_legacy_snapshot.json tests/fixtures/bank_dsl_pixel_hashes.json tests/fixtures/bank_legacy_dsl_identity_snapshot.json tests/fixtures/bank_dsl_pixel_reference`
(The leading backslash bypasses the interactive `rm -i` alias.) Then rerun the suite: nothing may reference them.

### Task 0.3: Fresh derived-output snapshot

**Files:**
- Create: `tests/derived_tools.py`, `tests/regenerate_derived_snapshot.py`, `tests/test_derived_snapshot.py`
- Create (by running the script): `tests/fixtures/derived_snapshot.json`
- Delete: `tests/fixtures/derived_baseline.json`

**Interfaces:**
- Consumes: `python -m generators.pipeline generate --config <path>` and `derive --config <path>`; `config/generation_config.yml` keys `output_dir` and `derived_dir`.
- Produces: `derive_into(root: Path) -> dict[str, str]` mapping each derived file's path (relative to the derived directory) to the SHA-256 of its bytes, with `str(root)` replaced by `<ROOT>` first.

- [ ] **Step 1: Write `tests/derived_tools.py`**

```python
"""Run generate + derive into a scratch root and hash what derive writes."""

import hashlib
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DERIVED_SNAPSHOT_PATH = REPO_ROOT / "tests" / "fixtures" / "derived_snapshot.json"


def derive_into(root: Path) -> dict[str, str]:
    """Generate and derive under `root`; return normalised per-file hashes."""
    config = yaml.safe_load((REPO_ROOT / "config" / "generation_config.yml").read_text())
    config["output_dir"] = str(root / "output")
    config["derived_dir"] = str(root / "derived")
    config_path = root / "generation_config.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    for command in ("generate", "derive"):
        subprocess.run(
            [sys.executable, "-m", "generators.pipeline", command, "--config", str(config_path)],
            cwd=REPO_ROOT, check=True, capture_output=True, text=True,
        )
    derived = root / "derived"
    return {
        str(path.relative_to(derived)): hashlib.sha256(
            path.read_bytes().replace(str(root).encode(), b"<ROOT>")
        ).hexdigest()
        for path in sorted(derived.rglob("*"))
        if path.is_file()
    }
```

Before Step 2, confirm `generate` accepts `--config` (`python -m generators.pipeline generate --help`). If it does not, write the scratch config to the path `generate` reads and adjust `derive_into` accordingly.

- [ ] **Step 2: Write `tests/test_derived_snapshot.py`**

```python
"""Pins every file `derive` writes, so a change to any derived view is deliberate."""

import json
from pathlib import Path

import pytest

from tests.derived_tools import DERIVED_SNAPSHOT_PATH, derive_into


@pytest.fixture(scope="module")
def derived(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    return derive_into(tmp_path_factory.mktemp("first"))


def test_derived_outputs_match_the_snapshot(derived: dict[str, str]) -> None:
    pinned = json.loads(DERIVED_SNAPSHOT_PATH.read_text())
    assert derived == pinned


def test_derived_outputs_do_not_depend_on_where_they_are_written(
    derived: dict[str, str], tmp_path_factory: pytest.TempPathFactory
) -> None:
    assert derive_into(tmp_path_factory.mktemp("second")) == derived


def test_the_snapshot_covers_the_core_artefacts() -> None:
    names = set(json.loads(DERIVED_SNAPSHOT_PATH.read_text()))
    assert {"ground_truth.csv", "ground_truth.jsonl", "geometry.jsonl"} <= {Path(n).name for n in names}
```

- [ ] **Step 3: Run it to verify it fails**

Run: `conda run -n synthetic pytest tests/test_derived_snapshot.py -v`
Expected: FAIL with `FileNotFoundError` for `derived_snapshot.json`.

- [ ] **Step 4: Write `tests/regenerate_derived_snapshot.py`**

```python
"""Re-capture tests/fixtures/derived_snapshot.json. Dry run unless --confirm."""

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.derived_tools import DERIVED_SNAPSHOT_PATH, derive_into  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="write the snapshot")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as scratch:
        current = derive_into(Path(scratch))
    print(f"{len(current)} derived files hashed.")
    if not args.confirm:
        print("Dry run: nothing written. Pass --confirm to write.")
        return
    DERIVED_SNAPSHOT_PATH.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
    print(f"Wrote {DERIVED_SNAPSHOT_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Capture, run, clean up**

Run: `conda run -n synthetic python tests/regenerate_derived_snapshot.py --confirm`, then `conda run -n synthetic pytest tests/test_derived_snapshot.py -v`.
Expected: 3 passed. If the location-independence test fails, a derived file embeds a path that `<ROOT>` normalisation misses: report it (Review Focus 4) rather than widening the normalisation blindly.
Then: `\rm -f tests/fixtures/derived_baseline.json`.

### Task 0.4: Layout DSL unit tests

**Inventory sections:** `layout_dsl/test_binding`, `test_context`, `test_defaults`, `test_engine`, `test_field_providers`, `test_primitives_container`, `test_primitives_table`, `test_primitives_text`, `test_providers`, `test_schema` (about 380 tests). Stages 1, 2, 3 and 6 all extend this package, so it is rebuilt first.

**Files:** `tests/layout_dsl/test_<module>.py`, one per inventory section.

- [ ] **Step 1: Start `tests/layout_dsl/test_field_providers.py` with the pattern every rebuilt test follows**

```python
"""Field providers derive off-ground-truth values deterministically per case."""

import pytest

from generators.layout_dsl.field_providers import (
    FieldProviderError,
    field_provider_emits,
    get_field_provider,
    receipt_pos,
)
from tests.conftest import assert_diagnostic_error

# Invented values: CASE999 and this date appear nowhere in ground_truth/.
ENTRY = {"case_id": "CASE999", "fields": {"INVOICE_DATE": "01/02/2031"}}


def test_receipt_pos_is_deterministic_per_case() -> None:
    assert receipt_pos(ENTRY, {}) == receipt_pos(ENTRY, {})


def test_receipt_pos_varies_with_the_case() -> None:
    other = {**ENTRY, "case_id": "CASE998"}
    assert receipt_pos(ENTRY, {}) != receipt_pos(other, {})


def test_receipt_pos_emits_only_declared_keys() -> None:
    assert set(receipt_pos(ENTRY, {})) <= set(field_provider_emits("receipt_pos"))


def test_unknown_provider_is_a_four_part_diagnostic() -> None:
    with pytest.raises(FieldProviderError) as exc_info:
        get_field_provider("no_such_provider")
    assert_diagnostic_error(exc_info.value)
```

Run: `conda run -n synthetic pytest tests/layout_dsl/test_field_providers.py -v`. These four should pass against current code; if `FieldProviderError`'s message lacks a label, that is a Stage 0 finding, not a test to weaken. Confirm `ENTRY`'s values are absent: `command grep -rnF "CASE999" ground_truth/; command grep -rnF "01/02/2031" ground_truth/` must print nothing.

- [ ] **Step 2: Rebuild the remaining inventory lines for `test_field_providers`**, following the rules at the top of Stage 0. Tick each line.
- [ ] **Step 3: Rebuild `test_engine`, `test_schema`, `test_defaults`, `test_context`, `test_binding`** in that order (the engine and schema are what Stage 1 extends).
- [ ] **Step 4: Rebuild `test_primitives_text`, `test_primitives_table`, `test_primitives_container`, `test_providers`.**
- [ ] **Step 5: Run the group and record coverage**

Run: `conda run -n synthetic pytest tests/layout_dsl -q --cov=generators.layout_dsl --cov-report=term-missing`
Expected: all pass; note the package's coverage figure in the inventory file's header for Task 0.9.

### Task 0.5: Renderer, fit and geometry tests

**Inventory sections:** `test_bank_fit`, `test_invoice_fit`, `test_receipt_fit`, `test_fit_text`, `test_fitted_helpers`, `test_font_guard`, `test_layout_budgets`, `test_overflow_backstop`, `test_bank_supplier_header`, `test_bank_dsl_validation`, `test_loader_anchors`, `test_generate_validates_layouts`, `test_geometry_capture`, `test_task10b_geometry` (rename to `test_geometry_layouts.py`: the old name described a task, not behaviour), `test_payment_block`.

- [ ] **Step 1: Rebuild `test_geometry_capture` first.** Its four docstrings define the opt-in contract Stage 5's overlay depends on (`geometry_out=None` renders identically to omitting it; every layout populates width, height and boxes; `tax_invoice_mixed` records only its first table).
- [ ] **Step 2: Rebuild the fit and budget modules**, then `test_payment_block`, then the rest.
- [ ] **Step 3: Run the group:** `conda run -n synthetic pytest tests -q -k "fit or budget or overflow or geometry or payment or supplier or loader or validates or font"`. Expected: all pass.

### Task 0.6: Corpus, content and environment tests

**Inventory sections:** `test_content_engine`, `test_data_pools_core`, `test_blocklist_reachability`, `test_ground_truth_schema`, `test_schema_invariants`, `test_transaction_dates`, `test_seed_ground_truth_dry_run`, `test_seed_reproducibility`, `test_environment_pins`.

- [ ] **Step 1: Rebuild `test_environment_pins` and `test_seed_reproducibility` first.** The second wraps the tracked `scripts/check_seed_reproducibility.py`; Stage 4's reseed depends on it.
- [ ] **Step 2: Rebuild `test_blocklist_reachability` and `test_data_pools_core`.** Stage 2 adds invented business names and domains that must pass the same blocklist.
- [ ] **Step 3: Rebuild the rest; run the group; all pass.**

### Task 0.7: Export, derive and linking tests

**Inventory sections:** `test_eval_set`, `test_eval_set_variants`, `test_eval_format`, `test_printed_unit_price_projection`, `test_debit_only_projection`, `test_generate_extraction_gt`, `test_derive_cord`, `test_derive_docile`, `test_derive_native`, `test_cord_corpus`, and every `exporters/` section.

- [ ] **Step 1: Rebuild `test_eval_set` and `test_eval_format` first.** Stage 7 restructures this export, and these define its current contract (generic filenames, self-contained halves, tier suffixes). `test_eval_format` read a dataset under `~/Desktop/evaluation_data/`; if that path is absent, rebuild it against `tests/fixtures/eval_format_baseline.json` alone.
- [ ] **Step 2: Rebuild the projection and derive modules, then `exporters/`.**
- [ ] **Step 3: Run the group; all pass.**

### Task 0.8: Degradation tests

**Inventory sections:** `degradation/test_tiers`, `degradation/test_camera`, `degradation/test_augment`.

- [ ] **Step 1: Start `tests/degradation/test_tiers.py` with the fail-fast check**

```python
"""Severity tiers come only from YAML, and a missing block fails with a diagnostic."""

from pathlib import Path

import pytest
import yaml

from generators.degradation import TierConfigError, load_tiers
from tests.conftest import REPO_ROOT, assert_diagnostic_error

CONFIG = REPO_ROOT / "config" / "generation_config.yml"


def test_the_shipped_config_declares_three_tiers() -> None:
    assert [tier.name for tier in load_tiers(CONFIG)] == ["light", "moderate", "heavy"]


def test_a_missing_degradation_block_is_a_four_part_diagnostic(tmp_path: Path) -> None:
    config = yaml.safe_load(CONFIG.read_text())
    del config["receipt_degradation"]
    broken = tmp_path / "generation_config.yml"
    broken.write_text(yaml.safe_dump(config))
    with pytest.raises(TierConfigError) as exc_info:
        load_tiers(broken)
    assert_diagnostic_error(exc_info.value)
```

Run it; both should pass against current code. If `Tier` exposes the name under another attribute, read `generators/degradation/tiers.py` and use that.

- [ ] **Step 2: Rebuild the remaining lines of the three sections; run the group; all pass.**

### Task 0.9: Coverage gate and suite integrity

**Files:**
- Create: `tests/test_suite_integrity.py`

- [ ] **Step 1: Write the integrity test**

```python
"""Every live inventory module exists, or is explicitly dropped with a reason."""

import re
import sys

import pytest

from tests.conftest import REPO_ROOT

INVENTORY = REPO_ROOT / "tests" / "RECOVERED_INVENTORY.md"


def test_every_live_inventory_module_exists() -> None:
    missing = []
    for header in re.findall(r"^## `(tests/[^`]+\.py)`(.*)$", INVENTORY.read_text(), re.MULTILINE):
        path, status = header
        if "OBSOLETE" in status or "DROPPED" in status:
            continue
        if not (REPO_ROOT / path).exists():
            missing.append(path)
    assert not missing, f"inventory modules with no test file: {missing}"


def test_no_live_inventory_line_is_left_unticked() -> None:
    unticked = []
    for block in INVENTORY.read_text().split("\n## ")[1:]:
        header, *lines = block.splitlines()
        if "OBSOLETE" in header:
            continue
        unticked += [f"{header}: {line}" for line in lines if line.startswith("- [ ] `")]
    assert not unticked, f"{len(unticked)} live lines unticked, first: {unticked[:3]}"


def test_the_integrity_check_can_fail(tmp_path, monkeypatch) -> None:
    fake = tmp_path / "RECOVERED_INVENTORY.md"
    fake.write_text("# x\n\n## `tests/test_nowhere.py`\n\n- [ ] `test_a`\n")
    monkeypatch.setattr(sys.modules[__name__], "INVENTORY", fake)
    with pytest.raises(AssertionError):
        test_every_live_inventory_module_exists()
    with pytest.raises(AssertionError):
        test_no_live_inventory_line_is_left_unticked()
```

A renamed module (for example `test_task10b_geometry` to `test_geometry_layouts`) gets its inventory header edited to the new path, so the check follows the rename.

- [ ] **Step 2: Run the full suite with coverage**

Run: `conda run -n synthetic pytest tests/ -q --cov=generators --cov=linking --cov=scripts --cov-report=term-missing --cov-fail-under=80`
Expected: all pass, total coverage at least 80%. If it is below, add tests to the least-covered modules that Stages 1 to 7 will touch, starting with `generators/layout_dsl/` and `generators/eval_set.py`, until it passes.

- [ ] **Step 3: Run the full quality gate**

Run: `conda run -n synthetic ruff check --fix --ignore ARG001,ARG002,F841 *.py && conda run -n synthetic ruff format . && conda run -n synthetic mypy . --ignore-missing-imports`
Expected: clean. Stage 0 changes only `tests/`, which is gitignored, so there is nothing to commit; record the coverage figure and the date in the inventory header.

### Task 0.10: Move `output/` and `derived/` into `../evaluation_data/`

The working directories resolve against the current working directory today (`generators/pipeline.py:180` and `:241` wrap the YAML value in a bare `Path`), and two diagnostics hard-code `derived/geometry.jsonl` as their WHERE. Both are fixed here, before any stage adds output.

**Files:**
- Modify: `generators/loader.py:106-139` (`load_generation_config`)
- Modify: `config/generation_config.yml` (`output_dir`, `derived_dir`)
- Modify: `generators/derive_outputs.py:205-240` (two messages naming `derived/geometry.jsonl`)
- Modify: `generators/exporters/docile.py:120-130` (one message)
- Modify: `README.md`, `docs/bounding_box_capture.md`, `docs/generator_capability_overview.md`, `docs/ten_minute_tour.md`, `docs/pipeline_backend_comparison.md` (path mentions; find them with `git grep -nE "(^|[^_a-z])(output|derived)/"`)
- Test: `tests/test_working_dirs.py`

**Interfaces:**
- Produces: `load_generation_config(path)` returns `output_dir` and `derived_dir` as absolute path strings, resolved against the repository root, never inside it. Every existing caller keeps working because it already wraps them in `Path(...)`.

- [ ] **Step 1: Write the failing test**

```python
"""Working directories resolve outside the repository, whatever the cwd."""

from pathlib import Path

import pytest
import yaml

from generators.loader import load_generation_config
from tests.conftest import REPO_ROOT, assert_diagnostic_error

SHIPPED = REPO_ROOT / "config" / "generation_config.yml"
EVALUATION_DATA = (REPO_ROOT.parent / "evaluation_data").resolve()


def _config_with(tmp_path: Path, **overrides: str) -> Path:
    data = yaml.safe_load(SHIPPED.read_text())
    data.update(overrides)
    path = tmp_path / "generation_config.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def test_shipped_config_puts_working_dirs_in_evaluation_data() -> None:
    cfg = load_generation_config(SHIPPED)
    assert Path(cfg["output_dir"]) == EVALUATION_DATA / "output"
    assert Path(cfg["derived_dir"]) == EVALUATION_DATA / "derived"


def test_resolution_does_not_depend_on_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert Path(load_generation_config(SHIPPED)["output_dir"]) == EVALUATION_DATA / "output"


@pytest.mark.parametrize("key", ["output_dir", "derived_dir"])
def test_a_directory_inside_the_repository_is_refused(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError) as exc_info:
        load_generation_config(_config_with(tmp_path, **{key: "output"}))
    assert_diagnostic_error(exc_info.value)
    assert key in str(exc_info.value)


def test_a_directory_whose_parent_is_missing_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as exc_info:
        load_generation_config(_config_with(tmp_path, output_dir=str(tmp_path / "absent" / "output")))
    assert_diagnostic_error(exc_info.value)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `conda run -n synthetic pytest tests/test_working_dirs.py -v`
Expected: FAIL. The shipped config still says `output`, and nothing refuses an in-repository path.

- [ ] **Step 3: Implement resolution in `generators/loader.py`**

Add below the imports:

```python
REPO_ROOT = Path(__file__).resolve().parent.parent
WORKING_DIR_KEYS = ("output_dir", "derived_dir")
```

Add the helper above `load_generation_config`:

```python
def _resolve_working_dir(value: str, key: str, config_path: Path) -> str:
    """Resolve a working directory against the repository root, outside it.

    Args:
        value: The YAML value, relative to the repository root or absolute.
        key: The config key, for diagnostics.
        config_path: The config file, for diagnostics.

    Returns:
        The absolute path as a string.

    Raises:
        ValueError: If the path lies inside the repository, or its parent is missing.
    """
    resolved = (REPO_ROOT / value).resolve()
    where = f"  Where:    {config_path.resolve()} -> {key}\n"
    expected = f"  Expected: {key}: ../evaluation_data/{Path(value).name or key}\n"
    if resolved.is_relative_to(REPO_ROOT):
        raise ValueError(
            "Invalid working directory.\n"
            f"  What:     '{key}' resolves inside the repository ({resolved}); generated "
            "content must live outside it.\n"
            f"{where}{expected}"
            f"  Recover:  point '{key}' under ../evaluation_data/ in {config_path.name}."
        )
    if not resolved.parent.is_dir():
        raise ValueError(
            "Invalid working directory.\n"
            f"  What:     the parent of '{key}' does not exist: {resolved.parent}.\n"
            f"{where}{expected}"
            f"  Recover:  create {resolved.parent}, or correct '{key}'."
        )
    return str(resolved)
```

In `load_generation_config`, replace `return data` with:

```python
    for key in WORKING_DIR_KEYS:
        data[key] = _resolve_working_dir(str(data[key]), key, path)
    return data
```

- [ ] **Step 4: Point the YAML at `../evaluation_data`**

In `config/generation_config.yml`, set `output_dir: ../evaluation_data/output` and `derived_dir: ../evaluation_data/derived`, with a comment saying they are resolved against the repository root and must stay outside it.

- [ ] **Step 5: Make the three geometry diagnostics name the real path.** In `derive_outputs.py` use the `geometry_path` argument the function already receives, resolved; in `docile.py`, say "the geometry.jsonl in the configured derived_dir" if the path is not in scope. Rerun the tests that cover those messages from Task 0.7.

- [ ] **Step 6: Run the tests, then regenerate into the new location**

Run: `conda run -n synthetic pytest tests/test_working_dirs.py tests/test_derived_snapshot.py -v`, then `conda run -n synthetic python -m generators.pipeline generate && conda run -n synthetic python -m generators.pipeline derive`.
Expected: tests pass; `../evaluation_data/output/clean/` holds 165 PNGs and `../evaluation_data/derived/` the derived files. The derived snapshot must still pass unchanged: `derive_into` passes absolute scratch paths, so this task cannot alter derived contents. If it fails, a derived file embeds the working-directory path, and that must be reported.

- [ ] **Step 7: Retire the in-repository copies (ask Tod first)**

They are gitignored and regenerable, but deleting is irreversible. After Tod confirms: `\rm -rf output derived`. Keep the `output/` and `derived/` lines in `.gitignore`, so an old config can never get them committed.

- [ ] **Step 8: Update documentation and commit**

Update the path mentions in the files listed above. Then run the full quality gate and commit `generators/loader.py`, `generators/derive_outputs.py`, `generators/exporters/docile.py`, `config/generation_config.yml`, `README.md` and the edited docs with the message `♻️ refactor: write output/ and derived/ under ../evaluation_data`.

**Stage 0 is complete when** the suite is green, collects every live module, coverage is at least 80%, and `tests/fixtures/pixel_snapshot.json` and `derived_snapshot.json` hold the HEAD baselines that Stages 1 to 7 measure against.

## Stage 0 outcome (6 October 2026)

Complete. The local suite holds 1053 tests: 1042 pass and 11 are strict expected failures, each pinning a production defect listed below beside a passing sibling that covers the working behaviour. No warnings. Coverage of `generators`, `linking` and `scripts` is about 86%. Baselines captured from HEAD: `pixel_snapshot.json` (165 pages), `derived_snapshot.json` (7 files), `eval_format_baseline.json` (three-type export shape). Tracked commits: 83e8324 (wrap-first fit documented), c624353 (working directories moved under `../evaluation_data/`), c1bc874 (documentation tidy).

### Constraints carried into later stages

Each later stage plan must account for the items under its heading.

**Stage 4 (invoice layouts and reseed)**
- `tests/layout_dsl/test_defaults.py` asserts the current layout counts (8 bank, 18 in all); four new invoice layouts change them.
- `tests/test_transaction_dates.py::test_difficulty_distribution_is_preserved` asserts the 52/36/22 link split; a reseed that changes it must update the test deliberately.
- Missing-layout handling: `render_corpus` raises (pinned), but `pipeline generate` still skips such an entry with a warning.

**Stage 5 (overlay)**
- Recorded text boxes are not tight ink boxes. A box's top is the draw y, above the ink top by the font bbox offset. A `SUPPLIER_NAME` slot is the block's line advance (48 px on invoices), with the ink 26 to 30 px above its bottom. A line-item slot is the fitted font size (28 px), and descender ink extends 3 px below its bottom on every invoice layout. Overlay clearance must therefore pad every scored box by at least 4 px (`DESCENDER_ALLOWANCE` in the geometry tests).

**Stage 7 (snapshot export)**
- The heavy degradation tier is not reproducible: Augraphy `Folding` gives different output for the same seed even though the pipeline seeds `random`, NumPy and OpenCV; the probable cause is numba's per-thread RNG in a parallel loop inside Augraphy's `warp_fold`, which Python-level seeding cannot reach. A manifest cannot pin heavy-tier receipts until this is resolved.
- `scripts/check_extractions_match_gt.py`, which ships in the snapshot `tools/`, has no tests yet; nor do `linking/transaction_matcher.py` and `scripts/seed_transaction_links.py`.
- The `eval-set` command prints the clean image count on its `Degraded:` line (`generators/pipeline.py:366`).
- The README's rectify example still points at `output/degraded/`, which `generate` no longer writes.

### Production defects pinned by expected failures (not fixed in Stage 0)

| Defect | Location |
| --- | --- |
| Config read relative to the working directory at import, so a run from outside the repository fails | `generators/layout_dsl/field_providers.py:43`; same class in `scripts/seed_ground_truth.py`, `generators/schema.py`, `generators/pipeline.py:185-190` |
| Diagnostics not in the What/Where/Expected/Recover form | `generators/layout_dsl/providers.py:53-58, 79-87, 331-335`; `scripts/generate_extraction_gt.py:61` ("How to fix:") |
| Unmapped degradation parameter raises a bare `KeyError` | `generators/degradation/augment.py:111` |
| Heavy tier not deterministic (see Stage 7) | Augraphy `Folding` via `generators/degradation/augment.py` |
| `eval-set` prints the wrong degraded count | `generators/pipeline.py:366` |

### Other observations

- `BindingError` (`generators/layout_dsl/binding.py:51-57`) has no WHERE element.
- `fictional_business` draws its ABN from the module-global RNG rather than the generator it is given; reproducibility relies on the seed scripts seeding the global RNG.
- The Augraphy fold width is no longer configurable from YAML, so fold visibility is untested.
- The worked example in `docs/GroundTruth_Export_Spec.md` no longer matches the live CASE001.
