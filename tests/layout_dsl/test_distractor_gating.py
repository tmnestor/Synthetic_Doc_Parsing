"""Distractor providers run only when a render asks for distractors."""

import pytest

from generators.distractors.validator import DistractorValueError
from generators.layout_dsl import field_providers as fp
from generators.layout_dsl import provider_registry as registry
from generators.layout_dsl.field_providers import FieldProviderError, apply_field_providers
from tests.conftest import assert_diagnostic_error

ENTRY = {"case_id": "CASE999", "fields": {"INVOICE_DATE": "01/02/2031"}}


@pytest.fixture
def scratch_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register test providers without leaking into the real registry."""
    for attr in ("_REGISTRY", "_PARAM_KEYS", "_EMITS", "_DISTRACTOR"):
        monkeypatch.setattr(registry, attr, dict(getattr(registry, attr)))


def _layout(*names: str) -> dict:
    return {"field_providers": [{"name": name, "params": {}} for name in names], "distractor_items": []}


def test_a_distractor_provider_runs_only_when_asked(
    scratch_registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    @fp.field_provider("dx_probe", emits=("DX_STORE_NUMBER",), distractor="receipt")
    def _probe(entry: dict, params: dict, *, items: list[str]) -> dict[str, str]:
        return {"DX_STORE_NUMBER": "Store 9046"}

    plain = apply_field_providers(_layout("dx_probe"), ENTRY, distractors=False)
    loaded = apply_field_providers(_layout("dx_probe"), ENTRY, distractors=True)
    assert "DX_STORE_NUMBER" not in plain["fields"]
    assert loaded["fields"]["DX_STORE_NUMBER"] == "Store 9046"
    assert fp.is_distractor_provider("dx_probe") is True
    assert fp.is_distractor_provider("receipt_pos") is False


def test_ordinary_providers_run_in_both_modes(scratch_registry: None) -> None:
    @fp.field_provider("plain_probe", emits=("PLAIN_PROBE",))
    def _probe(entry: dict, params: dict) -> dict[str, str]:
        return {"PLAIN_PROBE": "x"}

    for mode in (False, True):
        merged = apply_field_providers(_layout("plain_probe"), ENTRY, distractors=mode)
        assert merged["fields"]["PLAIN_PROBE"] == "x"


def test_a_distractor_provider_must_emit_only_dx_keys(scratch_registry: None) -> None:
    with pytest.raises(FieldProviderError) as err:
        fp.field_provider("dx_bad", emits=("STORE_NUMBER",), distractor="receipt")(
            lambda e, p, *, items: {}
        )
    assert_diagnostic_error(err.value)


def test_an_ordinary_provider_may_not_emit_dx_keys(scratch_registry: None) -> None:
    with pytest.raises(FieldProviderError) as err:
        fp.field_provider("sneaky", emits=("DX_X",))(lambda e, p: {})
    assert_diagnostic_error(err.value)


def test_renderers_require_the_flag() -> None:
    from generators.bank_statement import render_bank_statement, render_via_dsl
    from generators.invoice import render_invoice
    from generators.receipt import render_receipt

    for renderer in (render_receipt, render_invoice, render_bank_statement):
        with pytest.raises(TypeError):
            renderer({}, {})  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        render_via_dsl({}, {}, "x")  # type: ignore[call-arg]


def test_apply_field_providers_requires_the_flag() -> None:
    with pytest.raises(TypeError):
        apply_field_providers({"field_providers": []}, ENTRY)  # type: ignore[call-arg]


# --- the value validator runs over distractor output --------------------------------------------

RECEIPT = {
    "case_id": "CASE999",
    "layout": "receipt_thermal_80mm",
    "fields": {"INVOICE_DATE": "01/02/2031", "TOTAL_AMOUNT": "8645.19", "SUPPLIER_NAME": "Thornquist"},
}


def _colliding(entry: dict, params: dict, *, items: list[str]) -> dict[str, str]:
    return {"DX_STORE_NUMBER": f"Store {entry['fields']['TOTAL_AMOUNT']}"}


def test_a_colliding_distractor_value_fails_the_render(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry._REGISTRY, "receipt_distractors", _colliding)
    with pytest.raises(DistractorValueError) as err:
        apply_field_providers(_layout("receipt_distractors"), RECEIPT, distractors=True)
    assert_diagnostic_error(err.value)
    for token in ("CASE999", "rc_store_number", "DX_STORE_NUMBER", "Store 8645.19", "TOTAL_AMOUNT"):
        assert token in str(err.value), token


def test_a_plain_render_never_calls_the_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def spy(*args: object, **kwargs: object) -> None:
        calls.append("called")

    monkeypatch.setattr(fp, "check_distractor_values", spy)
    monkeypatch.setitem(registry._REGISTRY, "receipt_distractors", _colliding)
    layout = _layout("receipt_distractors", "receipt_pos")
    merged = apply_field_providers(layout, RECEIPT, distractors=False)
    assert calls == []
    assert not any(key.startswith("DX_") for key in merged["fields"])
    apply_field_providers(layout, RECEIPT, distractors=True)
    assert calls == ["called"]


def test_a_distractor_provider_without_a_document_type_cannot_register(scratch_registry: None) -> None:
    with pytest.raises(FieldProviderError) as err:
        fp.field_provider("dx_orphan", emits=("DX_STORE_NUMBER",), distractor="payslip")(
            lambda e, p, *, items: {}
        )
    assert_diagnostic_error(err.value)
    assert "dx_orphan" in str(err.value) and "payslip" in str(err.value)


def test_the_screen_uses_the_registered_document_type(scratch_registry: None) -> None:
    """A provider is screened against the document type it registered, and only that one."""

    @fp.field_provider("dx_inv_probe", emits=("DX_DUE_DATE",), distractor="invoice")
    def _probe(entry: dict, params: dict, *, items: list[str]) -> dict[str, str]:
        return {"DX_DUE_DATE": "09/02/2031"}

    merged = apply_field_providers(_layout("dx_inv_probe"), ENTRY, distractors=True)
    assert merged["fields"]["DX_DUE_DATE"] == "09/02/2031"

    @fp.field_provider("dx_rc_probe", emits=("DX_DUE_DATE",), distractor="receipt")
    def _wrong(entry: dict, params: dict, *, items: list[str]) -> dict[str, str]:
        return {"DX_DUE_DATE": "09/02/2031"}

    with pytest.raises(DistractorValueError) as err:
        apply_field_providers(_layout("dx_rc_probe"), ENTRY, distractors=True)
    assert_diagnostic_error(err.value)
    assert "no receipt item" in str(err.value)
