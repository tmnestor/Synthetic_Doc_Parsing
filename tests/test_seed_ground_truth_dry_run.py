"""Tests for the rewired scripts/seed_ground_truth.py (local-only; tests/ is gitignored).

Nothing here writes to ground_truth/: entries are built in memory with
`build_all_entries()` and the only `main()` call uses `--dry-run` with the
output directory also redirected to `tmp_path`.
"""

import hashlib
import importlib.util
import random

import pytest

from generators.content_engine import build_engine
from tests.conftest import REPO_ROOT

GT_DIR = REPO_ROOT / "ground_truth"


def _load_seed_module():
    """Import scripts/seed_ground_truth.py (scripts/ is not a package)."""
    path = REPO_ROOT / "scripts" / "seed_ground_truth.py"
    spec = importlib.util.spec_from_file_location("seed_ground_truth_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def seed():
    return _load_seed_module()


@pytest.fixture(scope="module")
def pools() -> dict:
    return build_engine().pools


@pytest.fixture(scope="module")
def entries(seed) -> dict[str, dict]:
    """Every generated entry, in memory. The seeder reads layouts relative to the repo root."""
    import os

    previous = os.getcwd()
    random_state = random.getstate()
    os.chdir(REPO_ROOT)
    try:
        return seed.build_all_entries()
    finally:
        os.chdir(previous)
        random.setstate(random_state)


def test_generate_case_entities_produces_one_bundle_per_case(seed):
    """One {holder, location} bundle per requested case, deterministic for a seed."""
    engine = build_engine()
    bundles = seed._generate_case_entities(engine, random.Random(1), 5)
    assert len(bundles) == 5
    for bundle in bundles:
        assert set(bundle) == {"holder", "location"}
        assert bundle["holder"]["full_name"]
        assert bundle["location"] in engine.pools["locations"]
    assert bundles == seed._generate_case_entities(build_engine(), random.Random(1), 5)


def test_bank_and_invoice_share_holder_for_the_same_case(seed, entries):
    """PAYER_NAME must be identical across bank/invoice for a case (cross-case parity)."""
    holders = seed._generate_case_entities(build_engine(), random.Random(seed._SEED), seed._COUNT)
    for i, case_id in enumerate(sorted(entries["bank_statements"])):
        bank_payer = entries["bank_statements"][case_id]["fields"]["PAYER_NAME"]
        invoice_payer = entries["invoices"][case_id]["fields"]["PAYER_NAME"]
        assert bank_payer == invoice_payer, case_id
        if i < 3:
            assert bank_payer == holders[i]["holder"]["full_name"]


def test_receipt_supplier_never_matches_a_real_retailer(entries, pools):
    """No receipt supplier is a real retailer from the blocklist pools."""
    real = {r["name"].lower() for r in pools["retailers"]} | {
        n.lower() for n in pools["real_name_blocklist_extra"]
    }
    receipts = entries["receipts"]
    assert receipts
    for case_id, entry in receipts.items():
        assert entry["fields"]["SUPPLIER_NAME"].lower() not in real, case_id


def test_invoice_provider_never_matches_a_real_service_and_has_payer_address(entries, pools):
    """No invoice supplier is a real service firm, and every invoice carries a payer address."""
    real = {p["name"].lower() for p in pools["professional_services"]} | {
        n.lower() for n in pools["real_name_blocklist_extra"]
    }
    invoices = entries["invoices"]
    assert invoices
    for case_id, entry in invoices.items():
        assert entry["fields"]["SUPPLIER_NAME"].lower() not in real, case_id
        assert entry["fields"]["PAYER_ADDRESS"].strip(), case_id


def test_seeded_bank_supplier_matches_its_layout(entries, pools):
    """The bank must be derived from the layout, not drawn independently.

    So SUPPLIER_NAME is always the canonical bank name implied by the layout prefix.
    """
    names = {b["code"]: b["name"] for b in pools["banks"]}
    banks = entries["bank_statements"]
    assert banks
    for case_id, entry in banks.items():
        code = entry["layout"].split("_")[0]
        assert entry["fields"]["SUPPLIER_NAME"] == names[code], case_id


def test_dry_run_validates_without_writing_ground_truth(seed, monkeypatch, tmp_path, capsys):
    """--dry-run validates in memory and writes no YAML, here or in the real ground_truth/."""
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in GT_DIR.glob("*.yml")}
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(seed, "_GT_DIR", tmp_path)
    seed.main(dry_run=True)
    assert "NOT written" in capsys.readouterr().out
    assert not any(tmp_path.iterdir())
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in GT_DIR.glob("*.yml")}
    assert after == before
