# Distractor Overhaul, Stage 2: Configuration, Providers and Value Validation

Execute task by task, in order. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce every distractor value a page could draw (deterministic per case, declared in YAML, screened against the real-name blocklist and against every scored value) and gate it behind an explicit render flag, without yet drawing anything.

**Architecture:** A required `distractors` keyword on every renderer reaches `apply_field_providers`, which runs providers registered with `distractor=True` only when it is set; those providers may emit only `DX_*` keys, so in a plain render every `DX_*` field is absent and any `when: DX_*` block is skipped. `config/distractors.yml` declares the catalogue (item id, document types, kind, target or repeated field, emitted keys, value rule) and selection counts; `generators/distractors/` holds the loader, a value-rule library, the three providers and the value validator. Layouts gain a required `distractor_items:` list, empty for now, cross-checked against the `DX_*` fields their blocks bind. No pixels change in this stage.

**Tech Stack:** Python 3.12, PyYAML, the existing layout DSL and content engine, pytest, conda env `synthetic`.

**Spec:** `docs/distractor_overhaul_design.md` sections 5 (catalogue), 8 (configuration and providers) and 9 (validation). Parent plans: `docs/distractor_overhaul_plan.md` and `docs/distractor_overhaul_stage1_plan.md`; read both "outcome" sections, whose carried constraints this plan implements.

## Global Constraints

- Conda env `synthetic`; run every tool as `conda run -n synthetic <command>` from the repository root.
- YAML is the single source of truth: every distractor tunable (catalogue, counts, offsets, ranges, retry budget, validator settings, text pools) lives in `config/distractors.yml` or `config/data_pools.yml`; Python holds no defaults, and every key is required.
- Every fail-fast error names What, Where (absolute file path and dotted key), Expected and Recover, on lines starting with those labels; tests assert this with `assert_diagnostic_error`.
- Config paths are resolved against the repository root, never the working directory.
- Determinism: every random draw comes from `random.Random` seeded with an integer derived from `sha256(f"{case_id}:distractor:{doc_type}")`; never the module-global `random`.
- Distractor providers emit only keys starting `DX_`; no other provider may emit a `DX_` key; no `DX_` key may collide with a scored column.
- Invented content only: every generated name, domain, handle and sentence is screened against the real-name blocklist (`retailers`, `professional_services`, `real_name_blocklist_extra` in `config/data_pools.yml`); invented example values in tests are checked absent from `ground_truth/`.
- Values bound to barcodes are unpadded, even-length, all-digit strings of at most 16 digits (Stage 1 outcome).
- The plain pixel and derived snapshots must not change: `conda run -n synthetic pytest tests/test_pixel_snapshot.py tests/test_derived_snapshot.py -q` passes with no re-bless after every task.
- Line length 108; Python 3.12 type hints; `pathlib.Path`; Google-style docstrings; `raise ... from err` or `from None` in `except` blocks.
- `tests/` is gitignored and local-only. Tracked changes are committed on `main` after the gate: `pytest tests/` (must collect), `ruff check --fix --ignore ARG001,ARG002,F841 *.py`, `ruff format --check generators scripts linking tests *.py`, `mypy . --ignore-missing-imports`. Commit messages carry no trailers.
- Tracked files contain no tooling references and no real business names; prose uses British spelling and no em-dashes.

## Review Focus

1. **A near-miss that happens to equal or contain a scored value** (a "You saved" amount equal to a line total; a due date equal to the invoice date when offsets include 0): redrawn up to the YAML retry budget, then a four-part failure naming case, item and value; never written out. Pinned in Tasks 4 and 5.
2. **A plain render that changes**: with `distractors=False` every page is byte-identical to the Stage 0 snapshot and no `DX_*` key exists in the entry fields. Pinned in Task 1.
3. **A layout whose blocks bind a `DX_*` field it does not list in `distractor_items`, or lists an item it never draws**: refused at validate time. Pinned in Task 2.
4. **An invented name, domain or sentence matching a real business**: refused by the blocklist screen, with a test that a deliberately blocklisted pool entry fails. Pinned in Task 3.
5. **A bank statement whose linked transactions cannot be found among its rows** (ground truth and links out of step): a four-part failure naming the case and link, never a silently shorter annotation set. Pinned in Task 4.

---

### Task 1: The render flag and distractor-provider gating

**Files:**
- Modify: `generators/layout_dsl/field_providers.py` (`field_provider`, `apply_field_providers`)
- Modify: `generators/layout_dsl/engine.py` (`render_body` gains `distractors: bool`)
- Modify: `generators/receipt.py`, `generators/invoice.py`, `generators/bank_statement.py` (each renderer gains a required keyword `distractors: bool`)
- Modify: `generators/pipeline.py` (`generate` passes `distractors=False`), `generators/eval_set.py` (passes `distractors=False`; Stage 7 adds the distractor half)
- Modify (local tests): every test or helper that calls a renderer, `render_body` or `apply_field_providers` (find them with `command grep -rnE "render_(receipt|invoice|bank_statement|via_dsl)|render_body|apply_field_providers|_RENDERERS|renderer\(" tests`); `tests/snapshot_tools.py` passes `distractors=False`
- Test: `tests/layout_dsl/test_distractor_gating.py`

**Interfaces:**
- Produces: `field_provider(name, *, params=frozenset(), emits, distractor: bool = False)`. The `distractor` argument is a code-level registration attribute, not configuration, so it may default. `apply_field_providers(layout: dict, entry: dict, *, distractors: bool) -> dict` (keyword required, no default). `is_distractor_provider(name: str) -> bool`. Renderers: `render_receipt(entry, layout, *, distractors: bool, geometry_out: dict | None = None)` and the same for `render_invoice`, `render_bank_statement`, `render_via_dsl`.

- [ ] **Step 1: Write the failing tests**

```python
"""Distractor providers run only when a render asks for distractors."""

import pytest

from generators.layout_dsl import field_providers as fp
from generators.layout_dsl.field_providers import FieldProviderError, apply_field_providers
from tests.conftest import assert_diagnostic_error

ENTRY = {"case_id": "CASE999", "fields": {"INVOICE_DATE": "01/02/2031"}}


@pytest.fixture
def scratch_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register test providers without leaking into the real registry."""
    for attr in ("_REGISTRY", "_PARAM_KEYS", "_EMITS", "_DISTRACTOR"):
        monkeypatch.setattr(fp, attr, dict(getattr(fp, attr)))


def _layout(*names: str) -> dict:
    return {"field_providers": [{"name": name, "params": {}} for name in names]}


def test_a_distractor_provider_runs_only_when_asked(scratch_registry: None) -> None:
    @fp.field_provider("dx_probe", emits=("DX_PROBE",), distractor=True)
    def _probe(entry: dict, params: dict) -> dict[str, str]:
        return {"DX_PROBE": "2031-0001"}

    plain = apply_field_providers(_layout("dx_probe"), ENTRY, distractors=False)
    loaded = apply_field_providers(_layout("dx_probe"), ENTRY, distractors=True)
    assert "DX_PROBE" not in plain["fields"]
    assert loaded["fields"]["DX_PROBE"] == "2031-0001"


def test_ordinary_providers_run_in_both_modes(scratch_registry: None) -> None:
    @fp.field_provider("plain_probe", emits=("PLAIN_PROBE",))
    def _probe(entry: dict, params: dict) -> dict[str, str]:
        return {"PLAIN_PROBE": "x"}

    for mode in (False, True):
        assert apply_field_providers(_layout("plain_probe"), ENTRY, distractors=mode)["fields"]["PLAIN_PROBE"] == "x"


def test_a_distractor_provider_must_emit_only_dx_keys(scratch_registry: None) -> None:
    with pytest.raises(FieldProviderError) as err:
        fp.field_provider("dx_bad", emits=("STORE_NUMBER",), distractor=True)(lambda e, p: {})
    assert_diagnostic_error(err.value)


def test_an_ordinary_provider_may_not_emit_dx_keys(scratch_registry: None) -> None:
    with pytest.raises(FieldProviderError) as err:
        fp.field_provider("sneaky", emits=("DX_X",))(lambda e, p: {})
    assert_diagnostic_error(err.value)


def test_renderers_require_the_flag() -> None:
    from generators.receipt import render_receipt

    with pytest.raises(TypeError):
        render_receipt({}, {})  # type: ignore[call-arg]
```

Check `CASE999` and `01/02/2031` are absent from `ground_truth/` with `command grep -rnF`.

- [ ] **Step 2: Run them to verify they fail**

Run: `conda run -n synthetic pytest tests/layout_dsl/test_distractor_gating.py -q`
Expected: FAIL (`unexpected keyword argument 'distractor'` / `'distractors'`).

- [ ] **Step 3: Implement the registration attribute**

In `field_providers.py`, add a module-level `_DISTRACTOR: dict[str, bool] = {}` beside `_REGISTRY`, a `distractor: bool = False` keyword on `field_provider`, and inside `decorate`, before registering:

```python
        dx_keys = [key for key in emits if key.startswith("DX_")]
        if distractor and len(dx_keys) != len(emits):
            offending = sorted(set(emits) - set(dx_keys))
            raise FieldProviderError(
                "Cannot register field provider.\n"
                f"  What:     distractor provider '{name}' emits non-DX keys {offending}.\n"
                "  Where:    generators/, "
                f"@field_provider('{name}', distractor=True, emits=...)\n"
                "  Expected: every emitted key starts with DX_, so a plain render can drop them all.\n"
                f"  Recover:  rename {offending} with a DX_ prefix."
            )
        if not distractor and dx_keys:
            raise FieldProviderError(
                "Cannot register field provider.\n"
                f"  What:     ordinary provider '{name}' emits DX_ keys {sorted(dx_keys)}.\n"
                "  Where:    generators/, "
                f"@field_provider('{name}', emits=...)\n"
                "  Expected: DX_ keys only from providers registered with distractor=True.\n"
                f"  Recover:  register '{name}' with distractor=True, or rename the keys."
            )
```

then `_DISTRACTOR[name] = distractor` with the other registrations, and:

```python
def is_distractor_provider(name: str) -> bool:
    """Return whether a registered provider emits distractor values only."""
    return _DISTRACTOR[name]
```

- [ ] **Step 4: Thread the flag**

`apply_field_providers(layout, entry, *, distractors: bool)`: in the loop, `if is_distractor_provider(name) and not distractors: continue` before calling the provider. Update the docstring. `render_body(..., distractors: bool)` passes it through; each renderer takes `distractors: bool` as a required keyword and passes it to `render_body` (`render_via_dsl` too). `pipeline.generate` and `eval_set` pass `distractors=False` explicitly, with a one-line comment that the distractor half arrives in Stage 7. Update every local test and helper the grep finds to pass `distractors=False` (or `True` where a test needs it); none may rely on a default.

- [ ] **Step 5: Run the tests and the snapshots**

Run: `conda run -n synthetic pytest tests/layout_dsl/test_distractor_gating.py tests/test_pixel_snapshot.py tests/test_derived_snapshot.py -q`, then the full suite.
Expected: all pass, snapshots unchanged.

- [ ] **Step 6: Gate and commit**

Commit the tracked files with `✨ feat: gate distractor providers behind an explicit render flag`.

### Task 2: `config/distractors.yml`, its loader, and `distractor_items`

**Files:**
- Create: `config/distractors.yml`, `generators/distractors/__init__.py`, `generators/distractors/config.py`
- Modify: `config/layouts/receipts.yml`, `config/layouts/invoices.yml`, `config/layouts/bank_statements.yml` (every layout gains `distractor_items: []`), `generators/layout_dsl/schema.py` (`distractor_items` required; cross-check)
- Test: `tests/distractors/__init__.py`, `tests/distractors/test_config.py`, `tests/layout_dsl/test_distractor_items.py`

**Interfaces:**
- Produces: `DISTRACTORS_PATH: Path` (repository-root resolved), `load_distractor_config(path: Path = DISTRACTORS_PATH) -> DistractorConfig`, frozen dataclasses `DistractorConfig(selection: dict[str, tuple[int, int]], max_redraws: int, validator: ValidatorSettings, items: dict[str, Item])`, `ValidatorSettings(ignore_fields: frozenset[str], min_scored_length: int)`, `Item(id: str, doc_types: frozenset[str], kind: str, target: str | None, field: str | None, emits: tuple[str, ...], rule: dict, requires: tuple[str, ...])`, `DistractorConfigError(ValueError)`, `ITEM_KINDS = ("clutter", "near_miss", "repeat")`, `RULE_TYPES` (Task 3 fills the rule table; this task validates `rule.type` against it).

- [ ] **Step 1: Write `config/distractors.yml`**

Every key is required. Item ids are prefixed by document type (`rc_`, `in_`, `bk_`). `doc_types` use the extraction schema's keys (`receipt`, `invoice`, `bank_statement`). Write exactly this catalogue (spec section 5); the comments state why each near-miss targets its field:

```yaml
# Distractor catalogue and selection. Read by generators/distractors/config.py.
# Every key is required; there are no Python defaults.
selection:
  receipt: {count: [3, 6]}
  invoice: {count: [8, 14]}
  bank_statement: {count: [2, 4]}
# Redraws allowed when a drawn value collides with a scored value.
max_redraws: 25
validator:
  # Scored fields whose values are too short or generic to screen for (quantities,
  # booleans, the document type).
  ignore_fields: [DOCUMENT_TYPE, IS_GST_INCLUDED, LINE_ITEM_QUANTITIES]
  # Scored values shorter than this are not used in the contains-check.
  min_scored_length: 4
items:
  # --- receipts -------------------------------------------------------------
  rc_store_number:    {doc_types: [receipt], kind: clutter, emits: [DX_STORE_NUMBER], requires: [], rule: {type: digits, length: 4, prefix: "Store "}}
  rc_merchant_id:     {doc_types: [receipt], kind: clutter, emits: [DX_MERCHANT_ID], requires: [], rule: {type: digits, length: 15, prefix: "MID "}}
  rc_acn:             {doc_types: [receipt], kind: near_miss, target: BUSINESS_ABN, emits: [DX_ACN], requires: [], rule: {type: acn}}
  rc_phone:           {doc_types: [receipt], kind: clutter, emits: [DX_PHONE], requires: [], rule: {type: phone, kind: landline}}
  rc_website:         {doc_types: [receipt], kind: clutter, emits: [DX_WEBSITE], requires: [], rule: {type: website}}
  rc_transaction_id:  {doc_types: [receipt], kind: clutter, emits: [DX_TRANSACTION_ID], requires: [], rule: {type: digits, length: 16, prefix: ""}}
  rc_member_number:   {doc_types: [receipt], kind: clutter, emits: [DX_MEMBER_NUMBER], requires: [], rule: {type: digits, length: 10, prefix: ""}}
  rc_points_balance:  {doc_types: [receipt], kind: clutter, emits: [DX_POINTS_BALANCE], requires: [rc_member_number], rule: {type: digits, length: 4, prefix: ""}}
  # A saving is a money figure printed in the totals region, beside TOTAL_AMOUNT.
  rc_you_saved:       {doc_types: [receipt], kind: near_miss, target: TOTAL_AMOUNT, emits: [DX_YOU_SAVED], requires: [], rule: {type: amount, min: "1.00", max: "25.00"}}
  rc_item_count:      {doc_types: [receipt], kind: clutter, emits: [DX_ITEM_COUNT], requires: [], rule: {type: item_count}}
  rc_returns_by:      {doc_types: [receipt], kind: near_miss, target: INVOICE_DATE, emits: [DX_RETURNS_BY], requires: [], rule: {type: date_offset, from: INVOICE_DATE, days: [14, 30, 60]}}
  rc_survey_code:     {doc_types: [receipt], kind: clutter, emits: [DX_SURVEY_CODE], requires: [], rule: {type: digits, length: 8, prefix: ""}}
  rc_survey_url:      {doc_types: [receipt], kind: clutter, emits: [DX_SURVEY_URL], requires: [rc_survey_code], rule: {type: survey_url}}
  rc_returns_policy:  {doc_types: [receipt], kind: clutter, emits: [DX_RETURNS_POLICY], requires: [], rule: {type: pool_text, pool: returns_policy}}
  # --- invoices -------------------------------------------------------------
  in_invoice_number:  {doc_types: [invoice], kind: clutter, emits: [DX_INVOICE_NUMBER], requires: [], rule: {type: digits, length: 6, prefix: "INV-"}}
  in_po_number:       {doc_types: [invoice], kind: clutter, emits: [DX_PO_NUMBER], requires: [], rule: {type: digits, length: 6, prefix: "PO-"}}
  in_customer_number: {doc_types: [invoice], kind: clutter, emits: [DX_CUSTOMER_NUMBER], requires: [], rule: {type: digits, length: 6, prefix: ""}}
  in_acn:             {doc_types: [invoice], kind: near_miss, target: BUSINESS_ABN, emits: [DX_ACN], requires: [], rule: {type: acn}}
  in_licence:         {doc_types: [invoice], kind: clutter, emits: [DX_LICENCE], requires: [], rule: {type: digits, length: 6, prefix: "Lic. No. "}}
  in_accreditation:   {doc_types: [invoice], kind: clutter, emits: [DX_ACCREDITATION], requires: [], rule: {type: pool_text, pool: accreditation}}
  in_tagline:         {doc_types: [invoice], kind: clutter, emits: [DX_TAGLINE], requires: [], rule: {type: pool_text, pool: tagline}}
  in_phone:           {doc_types: [invoice], kind: clutter, emits: [DX_PHONE], requires: [], rule: {type: phone, kind: landline}}
  in_mobile:          {doc_types: [invoice], kind: clutter, emits: [DX_MOBILE], requires: [], rule: {type: phone, kind: mobile}}
  in_freecall:        {doc_types: [invoice], kind: clutter, emits: [DX_FREECALL], requires: [], rule: {type: phone, kind: freecall}}
  in_email:           {doc_types: [invoice], kind: clutter, emits: [DX_EMAIL], requires: [], rule: {type: email, pool: email_local}}
  in_website:         {doc_types: [invoice], kind: clutter, emits: [DX_WEBSITE], requires: [], rule: {type: website}}
  in_social:          {doc_types: [invoice], kind: clutter, emits: [DX_SOCIAL], requires: [], rule: {type: social}}
  # A delivery address printed beside the Bill To block.
  in_ship_to:         {doc_types: [invoice], kind: near_miss, target: PAYER_ADDRESS, emits: [DX_SHIP_TO], requires: [], rule: {type: address}}
  # A remittance PO Box and a branch address, both printed in the letterhead region.
  in_remit_po_box:    {doc_types: [invoice], kind: near_miss, target: BUSINESS_ADDRESS, emits: [DX_REMIT_PO_BOX], requires: [], rule: {type: po_box}}
  in_branch_address:  {doc_types: [invoice], kind: near_miss, target: BUSINESS_ADDRESS, emits: [DX_BRANCH_ADDRESS], requires: [], rule: {type: address}}
  in_due_date:        {doc_types: [invoice], kind: near_miss, target: INVOICE_DATE, emits: [DX_DUE_DATE], requires: [], rule: {type: date_offset, from: INVOICE_DATE, days: [7, 14, 30]}}
  in_supply_date:     {doc_types: [invoice], kind: near_miss, target: INVOICE_DATE, emits: [DX_SUPPLY_DATE], requires: [], rule: {type: date_offset, from: INVOICE_DATE, days: [-7, -3, -1]}}
  in_services:        {doc_types: [invoice], kind: clutter, emits: [DX_SERVICES], requires: [], rule: {type: pool_text, pool: services}}
  in_promo_price:     {doc_types: [invoice], kind: near_miss, target: LINE_ITEM_PRICES, emits: [DX_PROMO_PRICE], requires: [], rule: {type: amount, min: "19.00", max: "499.00"}}
  in_contact_person:  {doc_types: [invoice], kind: near_miss, target: PAYER_NAME, emits: [DX_CONTACT_PERSON], requires: [], rule: {type: person}}
  in_job_reference:   {doc_types: [invoice], kind: clutter, emits: [DX_JOB_REFERENCE], requires: [], rule: {type: digits, length: 5, prefix: "JOB-"}}
  in_amount_paid:     {doc_types: [invoice], kind: repeat, field: TOTAL_AMOUNT, emits: [DX_AMOUNT_PAID], requires: [], rule: {type: repeat}}
  in_balance_due:     {doc_types: [invoice], kind: near_miss, target: TOTAL_AMOUNT, emits: [DX_BALANCE_DUE], requires: [in_amount_paid], rule: {type: fixed, value: "0.00"}}
  in_previous_balance: {doc_types: [invoice], kind: near_miss, target: TOTAL_AMOUNT, emits: [DX_PREVIOUS_BALANCE], requires: [], rule: {type: amount, min: "50.00", max: "4000.00"}}
  in_bsb:             {doc_types: [invoice], kind: clutter, emits: [DX_BSB], requires: [], rule: {type: bsb}}
  in_account_number:  {doc_types: [invoice], kind: clutter, emits: [DX_ACCOUNT_NUMBER], requires: [in_bsb], rule: {type: digits, length: 8, prefix: ""}}
  in_bpay_biller:     {doc_types: [invoice], kind: clutter, emits: [DX_BPAY_BILLER], requires: [], rule: {type: digits, length: 5, prefix: ""}}
  in_bpay_reference:  {doc_types: [invoice], kind: clutter, emits: [DX_BPAY_REFERENCE], requires: [in_bpay_biller], rule: {type: digits, length: 10, prefix: ""}}
  in_payid:           {doc_types: [invoice], kind: clutter, emits: [DX_PAYID], requires: [], rule: {type: email, pool: payid_local}}
  in_surcharge_note:  {doc_types: [invoice], kind: clutter, emits: [DX_SURCHARGE_NOTE], requires: [], rule: {type: pool_text, pool: surcharge_note}}
  in_terms:           {doc_types: [invoice], kind: clutter, emits: [DX_TERMS], requires: [], rule: {type: pool_text, pool: terms}}
  in_late_fee:        {doc_types: [invoice], kind: clutter, emits: [DX_LATE_FEE], requires: [], rule: {type: pool_text, pool: late_fee}}
  in_warranty:        {doc_types: [invoice], kind: clutter, emits: [DX_WARRANTY], requires: [], rule: {type: pool_text, pool: warranty}}
  in_remittance_total: {doc_types: [invoice], kind: repeat, field: TOTAL_AMOUNT, emits: [DX_REMITTANCE_TOTAL], requires: [in_invoice_number], rule: {type: repeat}}
  in_thank_you:       {doc_types: [invoice], kind: clutter, emits: [DX_THANK_YOU], requires: [], rule: {type: pool_text, pool: thank_you}}
  # --- bank statements ------------------------------------------------------
  bk_issued_date:     {doc_types: [bank_statement], kind: near_miss, target: STATEMENT_DATE_RANGE, emits: [DX_ISSUED_DATE], requires: [], rule: {type: range_end_offset, days: [1, 2, 3]}}
  bk_mail_barcode:    {doc_types: [bank_statement], kind: clutter, emits: [DX_MAIL_BARCODE], requires: [], rule: {type: digits, length: 16, prefix: ""}}
  bk_promo_message:   {doc_types: [bank_statement], kind: clutter, emits: [DX_PROMO_MESSAGE], requires: [], rule: {type: pool_text, pool: bank_promo}}
  bk_annotated_rows:  {doc_types: [bank_statement], kind: clutter, emits: [DX_ANNOTATED_ROWS], requires: [], rule: {type: annotated_rows, extra: [1, 3]}}
  # A taxpayer's handwritten sum of the claimed rows: a correct total, so a near-miss
  # for every individual transaction amount.
  bk_annotation_total: {doc_types: [bank_statement], kind: near_miss, target: TRANSACTION_AMOUNTS_PAID, emits: [DX_ANNOTATION_TOTAL], requires: [bk_annotated_rows], rule: {type: annotation_total}}
  bk_margin_note:     {doc_types: [bank_statement], kind: clutter, emits: [DX_MARGIN_NOTE], requires: [bk_annotated_rows], rule: {type: pool_text, pool: margin_note}}
```

Before writing, confirm the scored column names (`BUSINESS_ABN`, `TOTAL_AMOUNT`, `INVOICE_DATE`, `PAYER_ADDRESS`, `BUSINESS_ADDRESS`, `PAYER_NAME`, `LINE_ITEM_PRICES`, `STATEMENT_DATE_RANGE`, `TRANSACTION_AMOUNTS_PAID`) against `config/extraction_schema.yml`, and the document-type keys against its `document_fields:`; use the real spellings.

- [ ] **Step 2: Write the failing loader tests** (`tests/distractors/test_config.py`)

```python
"""The distractor catalogue loads only when every key is present and coherent."""

from pathlib import Path

import pytest
import yaml

from generators.distractors.config import DISTRACTORS_PATH, DistractorConfigError, load_distractor_config
from tests.conftest import assert_diagnostic_error

SHIPPED = yaml.safe_load(DISTRACTORS_PATH.read_text())


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "distractors.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def test_the_shipped_catalogue_loads() -> None:
    config = load_distractor_config()
    assert {"receipt", "invoice", "bank_statement"} == set(config.selection)
    assert all(item.emits and all(key.startswith("DX_") for key in item.emits) for item in config.items.values())


@pytest.mark.parametrize("key", ["selection", "max_redraws", "validator", "items"])
def test_a_missing_top_level_key_is_a_diagnostic(tmp_path: Path, key: str) -> None:
    data = {k: v for k, v in SHIPPED.items() if k != key}
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(_write(tmp_path, data))
    assert_diagnostic_error(err.value)
    assert key in str(err.value)


@pytest.mark.parametrize(
    ("item_patch", "token"),
    [
        ({"kind": "decoy"}, "kind"),
        ({"kind": "near_miss", "target": None}, "target"),
        ({"kind": "near_miss", "target": "NOT_A_FIELD"}, "NOT_A_FIELD"),
        ({"emits": ["STORE"]}, "DX_"),
        ({"requires": ["rc_missing"]}, "rc_missing"),
        ({"rule": {"type": "telepathy"}}, "telepathy"),
        ({"doc_types": ["passport"]}, "passport"),
    ],
)
def test_an_incoherent_item_is_a_diagnostic(tmp_path: Path, item_patch: dict, token: str) -> None:
    data = yaml.safe_load(DISTRACTORS_PATH.read_text())
    data["items"]["rc_store_number"].update(item_patch)
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(_write(tmp_path, data))
    assert_diagnostic_error(err.value)
    assert token in str(err.value)


def test_a_selection_range_must_be_ordered(tmp_path: Path) -> None:
    data = yaml.safe_load(DISTRACTORS_PATH.read_text())
    data["selection"]["receipt"]["count"] = [6, 3]
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(_write(tmp_path, data))
    assert_diagnostic_error(err.value)


def test_two_items_may_not_emit_the_same_key_for_one_document_type(tmp_path: Path) -> None:
    data = yaml.safe_load(DISTRACTORS_PATH.read_text())
    data["items"]["rc_merchant_id"]["emits"] = ["DX_STORE_NUMBER"]
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(_write(tmp_path, data))
    assert_diagnostic_error(err.value)
```

- [ ] **Step 3: Run them to verify they fail**, then **implement `generators/distractors/config.py`**: `DISTRACTORS_PATH = Path(__file__).resolve().parents[2] / "config" / "distractors.yml"`; frozen dataclasses as in Interfaces; `load_distractor_config` that checks, in order, with a four-part `DistractorConfigError` naming the absolute path and dotted key for each: the four top-level keys; each `selection.<type>.count` is two non-negative ints `[min, max]` with `min <= max`; `max_redraws` a positive int; `validator.ignore_fields` a list of scored columns and `validator.min_scored_length` a positive int; per item: required keys `doc_types`, `kind`, `emits`, `requires`, `rule` (and `target` for `near_miss`, `field` for `repeat`); `doc_types` within the extraction schema's document types; `kind` in `ITEM_KINDS`; `target`/`field` a scored column of every listed document type (read the column lists from `config/extraction_schema.yml` through the existing projection helper in `generators/exporters/eval_projection.py`); `emits` non-empty and all `DX_`; `requires` naming existing items that share at least one document type; `rule.type` in `RULE_TYPES` (import from `generators/distractors/values.py`, which Task 3 creates; in this task create that module with only `RULE_TYPES: frozenset[str]` listing every rule type the catalogue uses); no two items for the same document type emitting the same key. Run the tests.

- [ ] **Step 4: Layouts gain `distractor_items`**

Write the failing tests (`tests/layout_dsl/test_distractor_items.py`), reusing `make_layout`, `reject_body` and `check_layout` from `tests/layout_dsl/test_schema.py`:

```python
"""A layout lists exactly the distractor items its blocks draw."""

import pytest

from tests.layout_dsl.test_schema import check_layout, make_layout, reject_body  # adapt to the real helpers


def test_every_shipped_layout_declares_its_items() -> None:
    from generators.loader import load_layout_registry
    from tests.conftest import REPO_ROOT

    for stem in ("receipts", "invoices", "bank_statements"):
        for layout_id, layout in load_layout_registry(REPO_ROOT / "config" / "layouts" / f"{stem}.yml").items():
            assert layout["distractor_items"] == [], layout_id


def test_a_block_binding_an_unlisted_dx_field_is_refused() -> None:
    body = [{"type": "text", "content": "{DX_STORE_NUMBER}", "when": "DX_STORE_NUMBER"}]
    reject_body(body, distractor_items=[], token="DX_STORE_NUMBER")


def test_a_listed_item_that_no_block_draws_is_refused() -> None:
    reject_body([], distractor_items=["rc_store_number"], token="rc_store_number")


def test_a_listed_item_for_another_document_type_is_refused() -> None:
    body = [{"type": "text", "content": "{DX_DUE_DATE}", "when": "DX_DUE_DATE"}]
    reject_body(body, distractor_items=["in_due_date"], token="in_due_date")  # on a receipt layout


def test_a_block_drawing_a_dx_field_without_gating_on_it_is_refused() -> None:
    body = [{"type": "text", "content": "Store {DX_STORE_NUMBER}"}]
    reject_body(body, distractor_items=["rc_store_number"], token="when")


def test_a_correctly_listed_and_gated_item_passes() -> None:
    body = [{"type": "text", "content": "{DX_STORE_NUMBER}", "when": "DX_STORE_NUMBER"}]
    check_layout(make_layout(body=body, distractor_items=["rc_store_number"]))
```

Adapt the helper calls to the real signatures in `test_schema.py` (they may need a `distractor_items` parameter added; add it to the helpers without changing their existing behaviour). Then in `schema.py`: `distractor_items` becomes a required layout key; add it to `_ENGINE_LAYOUT_KEYS` (or the set that admits engine-read keys); validate it is a list of catalogue item ids whose `doc_types` include the layout's document type (the registry already knows each layout's type through its file; thread the document type the same way the existing per-type checks do); collect every `DX_*` name referenced in the body (via `_validate_references`' parser, through `referenced_fields`); refuse (a) a referenced `DX_*` key emitted by no listed item, (b) a listed item none of whose emitted keys is referenced, and (c) a block referencing a `DX_*` key in `content`, `caption`, `value` or `label` that is not gated by `when:` on that same key (the Stage 1 gating rule). Finally add `distractor_items: []` to every layout in the three layout files (the receipt layouts share a defaults anchor, but `distractor_items` is a top-level layout key, so add it to each layout), with a comment on the first saying Stage 3 onwards fills it.

- [ ] **Step 5: Run the tests and the snapshots**, then the full suite. Expected: all pass, snapshots unchanged.

- [ ] **Step 6: Gate and commit** `config/distractors.yml`, `generators/distractors/__init__.py`, `generators/distractors/config.py`, `generators/distractors/values.py`, the three layout files and `schema.py` with `✨ feat: declare the distractor catalogue and per-layout distractor items`.

### Task 3: Value rules and text pools

**Files:**
- Modify: `generators/distractors/values.py`, `config/data_pools.yml` (new `distractor_text:` and `phone_area_codes:` sections), `generators/content_engine.py` only if a reusable screening helper must be exposed (prefer calling the existing `ContentEngine` screen)
- Test: `tests/distractors/test_values.py`

**Interfaces:**
- Consumes: `ContentEngine` (`build_engine()`, `.address(rng)`, `.location(rng)`, `.person(rng)`), its real-name screen.
- Produces: `RULES: dict[str, Callable[[RuleContext, dict], str]]` with `RULE_TYPES = frozenset(RULES)`; `RuleContext(rng: random.Random, fields: dict[str, str], engine: ContentEngine, pools: dict, case_id: str, rows: list[dict] | None, selected: dict[str, str])` (frozen dataclass; `rows` is the parsed bank transaction rows for bank statements, `selected` holds values already drawn for this document so dependent rules can read them); `ValueRuleError(ValueError)`; `ean_safe_digits` is not needed (barcodes take plain even-length digits).

Rule semantics (every parameter from the rule mapping; nothing defaulted):

| `type` | Parameters | Output |
| --- | --- | --- |
| `digits` | `length`, `prefix` | `prefix` + `length` digits, first digit non-zero |
| `acn` | none | 9 digits with a valid ACN check digit, formatted `NNN NNN NNN` |
| `phone` | `kind` (`landline`, `mobile`, `freecall`) | `(0A) NNNN NNNN` using the area code for the state in the business address (from `phone_area_codes`), `04NN NNN NNN`, or `1300 NNN NNN` |
| `email` | `pool` | `<local>@<slug>.com.au`, local part from the pool |
| `website` | none | `www.<slug>.com.au` |
| `social` | none | `@<slug>` |
| `survey_url` | none | `https://survey.<slug>.com.au/<DX_SURVEY_CODE>` (reads `selected`) |
| `amount` | `min`, `max` | a two-decimal amount string in the range |
| `fixed` | `value` | the value verbatim |
| `repeat` | none | the item's `field` value verbatim |
| `item_count` | none | the number of entries in `LINE_ITEM_DESCRIPTIONS` |
| `date_offset` | `from`, `days` | the `from` date (DD/MM/YYYY) plus a drawn offset, in the same format |
| `range_end_offset` | `days` | the end date of `STATEMENT_DATE_RANGE` plus a drawn offset |
| `address` | none | `engine.address(rng)` |
| `po_box` | none | `PO Box NNNN, <suburb> <state> <postcode>` from `engine.location(rng)` |
| `person` | none | `engine.person(rng)["full_name"]` |
| `pool_text` | `pool` | a draw from `distractor_text.<pool>` |
| `annotated_rows` | `extra` (`[min, max]`) | pipe-joined 0-based row indices: every row linked to this statement in `ground_truth/transaction_links.yml`, plus a drawn number of other debit rows, sorted |
| `annotation_total` | none | the two-decimal sum of the paid amounts of the rows in `DX_ANNOTATED_ROWS` (reads `selected`) |

`<slug>` is the supplier name lower-cased with non-alphanumerics removed and trailing company words (`pty`, `ltd`, `co`) dropped; a slug that, after screening, matches a real business name raises `ValueRuleError`.

- [ ] **Step 1: Add the pools.** Under `distractor_text:` in `config/data_pools.yml`, add the pools the catalogue names (`returns_policy`, `accreditation`, `tagline`, `email_local`, `payid_local`, `services`, `surcharge_note`, `terms`, `late_fee`, `warranty`, `thank_you`, `bank_promo`, `margin_note`), each a list of at least five invented entries in Australian business register and British spelling, no real organisations, products or brands, and no em-dashes. Examples of the register (write your own, do not copy these verbatim): returns policy "Please retain this receipt as proof of purchase for returns within 30 days."; terms "Payment is due within 14 days of the invoice date."; margin notes "WRE", "D5 car", "50% work", "home office". Add `phone_area_codes: {NSW: "02", ACT: "02", VIC: "03", TAS: "03", QLD: "07", SA: "08", WA: "08", NT: "08"}`.

- [ ] **Step 2: Write the failing tests** (`tests/distractors/test_values.py`), one or more per rule, each checking the documented output shape and determinism under a fixed seed, plus:

```python
def test_acn_check_digit_is_valid() -> None:
    """ACN weights 8,7,6,5,4,3,2,1 over the first 8 digits; check = (10 - sum % 10) % 10."""
    value = RULES["acn"](ctx(seed=1), {"type": "acn"})
    digits = [int(ch) for ch in value.replace(" ", "")]
    weighted = sum(d * w for d, w in zip(digits[:8], range(8, 0, -1), strict=True))
    assert len(digits) == 9 and digits[8] == (10 - weighted % 10) % 10


def test_annotated_rows_include_every_linked_row() -> None:
    """Every row a receipt or invoice links to is annotated; extras are other debits."""
    # Build ctx for a real bank case from ground_truth/, and check that each linked
    # (bank_date, bank_amount, bank_description) row index appears in the output.


def test_a_link_missing_from_the_statement_rows_is_a_diagnostic() -> None:
    """Ground truth and links out of step fail fast, naming case and link (Review Focus 5)."""


def test_a_blocklisted_pool_entry_is_refused(monkeypatch) -> None:
    """A pool sentence naming a real business (taken from the blocklist itself) is refused (Review Focus 4)."""


def test_rules_never_use_the_global_random(monkeypatch) -> None:
    """Patch random.random/randint/choice to raise; every rule still works from ctx.rng."""
```

Write the bodies of the last four in full against the real data (no placeholders): build `RuleContext` from a real bank entry and its links for the annotated-rows tests (find a case with at least one link), and for the blocklist test insert a name read from the blocklist into a pool copy.

- [ ] **Step 3: Implement `values.py`**, keeping each rule a small pure function of `(ctx, rule)`. The links file is resolved against the repository root (`Path(__file__).resolve().parents[2] / "ground_truth" / "transaction_links.yml"`), not the working directory. Run the tests until they pass.

- [ ] **Step 4: Gate and commit** `generators/distractors/values.py` and `config/data_pools.yml` (and `content_engine.py` if touched) with `✨ feat: add deterministic distractor value rules and text pools`.

### Task 4: The three distractor providers

**Files:**
- Create: `generators/distractors/providers.py`
- Modify: `generators/layout_dsl/field_providers.py` only to import `generators.distractors.providers` where the other registrations are triggered, so the providers register at import
- Test: `tests/distractors/test_providers.py`

**Interfaces:**
- Consumes: Task 1 (`field_provider(..., distractor=True)`), Task 2 (`load_distractor_config`), Task 3 (`RULES`, `RuleContext`).
- Produces: registered providers `receipt_distractors`, `invoice_distractors`, `bank_distractors`, each with `distractor=True`, `emits` = the union of the catalogue's `emits` for its document type, and params `frozenset({"items"})`; `case_seed(case_id: str, doc_type: str) -> int`; `select_items(config, doc_type, eligible: list[str], rng) -> list[str]`.

Behaviour: the provider reads the layout's `distractor_items` through its `params` (`{"items": [...]}`), so the layout's `field_providers:` entry for it is `{name: receipt_distractors, params: {items: *the same list*}}` (Stage 3 adds these entries to layouts; in this stage only tests use them). Seed `random.Random(case_seed(case_id, doc_type))`. Choose `k` uniformly in `[min, min(max, len(eligible))]` from `selection`, sample `k` eligible items, add each sampled item's `requires` (transitively, if eligible; an unmet requirement on a non-eligible item drops the dependant), and draw values in dependency order, recording them in `ctx.selected`. For each `near_miss` or `clutter` value, call the Task 5 value check; on a collision, redraw with the same rng up to `max_redraws` times, then raise a four-part error naming case, item, value and colliding field (Review Focus 1). Rules whose output does not depend on the rng (`fixed`, `repeat`, `item_count`, `annotation_total`) cannot be redrawn: when one of those collides (for example a fixed `0.00` balance on an invoice whose GST is `0.00`), deselect that item and every item that requires it, for this case only, and record the deselection in a module-level debug log line; this is the only permitted deselection, and a test pins it. Emit only the selected items' keys.

- [ ] **Step 1: Write the failing tests**, covering: determinism (same case, same output, twice); different cases differ; the emitted keys are exactly the selected items' `emits`; `k` stays within the configured range and within the eligible count; a `requires` dependency is always present with its dependant; the bank provider's `DX_ANNOTATION_TOTAL` equals the sum of the rows in `DX_ANNOTATED_ROWS`; a forced collision (monkeypatch a rule to return a scored value every time) fails after exactly `max_redraws` redraws with a four-part diagnostic; a provider given an item for another document type fails fast. Run every provider over every case of its type in the corpus with all eligible items (a loop over the 55 entries) and assert no failure and no collision: this is the corpus-wide proof the catalogue and rules are compatible.

- [ ] **Step 2: Implement**, run the tests, run the snapshots (no layout lists the providers yet, so pixels cannot change).

- [ ] **Step 3: Gate and commit** with `✨ feat: add receipt, invoice and bank distractor providers`.

### Task 5: The value validator, wired into rendering and `validate`

**Files:**
- Create: `generators/distractors/validator.py`
- Modify: `generators/layout_dsl/field_providers.py` (`apply_field_providers` runs the validator over distractor output when `distractors=True`), `generators/pipeline.py` (`validate` also computes and checks every entry's distractor values with all eligible items)
- Test: `tests/distractors/test_validator.py`

**Interfaces:**
- Produces: `scored_values(fields: dict[str, str], doc_type: str, settings: ValidatorSettings) -> dict[str, list[str]]` (pipe lists split, `NOT_FOUND` and ignored fields dropped, values shorter than `min_scored_length` dropped); `collision(item: Item, value: str, scored: dict[str, list[str]]) -> str | None` (returns the colliding field name or `None`); `check_distractor_values(values: dict[str, str], fields: dict, doc_type: str, config: DistractorConfig) -> None` raising `DistractorValueError` (four-part).

Rules (spec section 9, item 2):
- `near_miss`: the value must not equal its target's value (or any element of a pipe-list target), compared after normalising (strip, case-fold, and for ABN-like values remove spaces); and must not contain any scored value of any field as a substring under the same normalisation.
- `repeat`: the value must equal its field's value exactly.
- `clutter`: the value must not contain any scored value.

- [ ] **Step 1: Write the failing tests**: one deliberately failing fixture per rule (near-miss equal to its target; near-miss containing another field's value; repeat that drifted; clutter containing a scored value), each asserting the four-part diagnostic and that it names the item and field; normalisation cases (`"12 345 678 901"` vs `"12345678901"`; case); ignored fields and short values are not screened (a quantity `"2"` inside a clutter value is allowed); and a test that `apply_field_providers(..., distractors=True)` raises when a monkeypatched provider emits a colliding value, while `distractors=False` never calls the validator. Then a `pipeline validate` test (subprocess or Typer `CliRunner`) that passes on the shipped corpus and fails when a rule is monkeypatched to collide.

- [ ] **Step 2: Implement**; have the Task 4 providers call `collision` for their redraw loop (one source of truth); run the tests, the snapshots and the full suite.

- [ ] **Step 3: Documentation.** Add a short section to `README.md` (where the pipeline commands are described) explaining `config/distractors.yml`, the `distractors` render flag, and that `validate` checks every entry's distractor values. British spelling, no em-dashes.

- [ ] **Step 4: Gate and commit** with `✨ feat: validate distractor values against every scored value`.

**Stage 2 is complete when** every case of every document type yields distractor values that pass the validator, plain renders are byte-identical, layouts declare `distractor_items: []` with the cross-check in force, and `pipeline validate` checks all of it.

## Stage 2 outcome (6 October 2026)

Complete. Commits 0554066 to be257ea. The local suite holds 1546 tests: 1535 pass and the 11 strict expected failures are unchanged from Stage 0. The plain pixel and derived snapshots are unchanged. `pipeline validate` passes, drawing every catalogue item for all 165 entries and rendering each entry both plain and with distractors.

### How the shipped code differs from this plan's text

Later stages must use the shipped behaviour, not the code samples above.

- **One items list.** Distractor providers take no `items` param: `apply_field_providers` passes the layout's own `distractor_items` to them as a keyword. A layout's `field_providers:` entry is `{name: receipt_distractors, params: {}}`, so the shared YAML anchors need no per-layout copy. `validate` refuses a non-empty `distractor_items` without the document type's provider, a provider of another type, and fewer items than `selection.<type>.count[0]`.
- **Per-item seeds.** The selection is drawn from `sha256(f"{case_id}:distractor:{doc_type}")`; each item's value from its own `sha256(f"{case_id}:distractor:{doc_type}:{item_id}")`. A value is therefore identical whether drawn alone, in any selection, or in `validate`'s all-items draw, and stays stable when a layout's list changes.
- **Registration carries the document type.** `field_provider(..., distractor="receipt")` (singular schema key); `distractor_doc_type(name)` and `distractor_provider_names()` live in the leaf module `generators/layout_dsl/provider_registry.py`, which `field_providers.py` re-exports.
- **Repeats fail fast.** A repeat value is compared with its raw ground-truth field and a mismatch raises; deselection is limited to the `fixed`, `item_count` and `annotation_total` rules.
- **Redrawability and parameters are declared beside the rules.** `values.py` declares, per rule, whether a collision is redrawn, deselects, or fails at once (rules derived from the supplier slug fail at once naming `SUPPLIER_NAME`), and each rule's required parameters, which the loader checks at startup.
- **The value screen matches at token edges.** Equality uses raw values; the contains-check uses scored values of at least `min_scored_length` characters, matches a number spaced or unspaced, and never inside a longer number (`2.00` is not found in `12.00`).
- **Validation extras.** A `DX_` key unknown to a layout gets its own diagnostic (naming `distractor_items` and the provider); `field: DX_*` is refused until distractor geometry exists; a `DX_` placeholder in a string the renderer never interpolates is refused.

### Constraints carried into later stages

**Stage 3 (receipt distractors)**
- Decide how a block that binds two `DX_` keys is gated (the QR survey code binds `DX_SURVEY_URL` in `content` and `DX_SURVEY_CODE` in `caption`; BPAY biller and reference). Options: gate on the dependant key whose item `requires` the other, or allow a list in `when:`.
- A `when:` reference alone counts as drawing an item, so a `DX_` key may act as a selection flag (Ruling 6); state the intended use in the Stage 3 plan.
- Barcodes bound to a `DX_` key must map to a `digits` rule with an empty prefix and an even length of at most 16 (`rc_transaction_id` and `bk_mail_barcode` qualify; `rc_store_number` does not); check this at validate time.
- Add an end-to-end test that renders every layout and case with distractors on and asserts the selected `DX_` values are drawn, keeping the plain render byte-identical; consider a `--distractors` switch on `generate` for visual review before Stage 7.
- Follow-ups from the final review: typed provider Protocols (ordinary and distractor) instead of the widened `FieldProvider` type; unify `generators/eval_set.py`'s copy of the field aliases with `FIELD_ALIASES` in `generators/exporters/eval_projection.py`; render the distractor mode in the overflow check only for layouts that carry a distractor provider.

**Future rules**
- The screen does not normalise thousands separators; a rule that prints commas in amounts must normalise them before screening.

**Scheduled**
- Paths still resolved against the working directory (`provider_registry._FIELD_DEFINITIONS_PATH`, and the Stage 0 list) remain pinned by the strict expected failure; fix them together.
- The catalogue loads at import because providers declare their emits from it; consider lazy emits so commands that never render distractors do not depend on `config/distractors.yml`.
