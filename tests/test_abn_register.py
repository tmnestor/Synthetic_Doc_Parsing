"""Tests for generators/abn_register.py and the register screen in generate_abn()."""

import os
import random
import zipfile
from pathlib import Path

import numpy as np
import pytest
import yaml

from generators import abn_register
from generators.abn_register import AbnRegister, abns_in_extract, build_from_zips
from generators.common import generate_abn, validate_abn
from tests.conftest import REAL_LOAD_ABN_REGISTER as load_abn_register
from tests.conftest import ACN_REGISTER_HITS, REPO_ROOT, SEED_REGISTER_HITS, assert_diagnostic_error
from tests.test_seed_ground_truth_dry_run import _load_seed_module

POOLS_PATH = REPO_ROOT / "config" / "data_pools.yml"
GT_DIR = REPO_ROOT / "ground_truth"

# Invented ABNs that pass the checksum; none is quoted from the register.
_INVENTED = ("18 123 456 789", "10 000 000 089", "34 987 654 321")


def _record(abn: str, status: str = "ACT") -> str:
    """One extract line in the shape the bulk extract writes: one <ABR> record per line."""
    return (
        f'<ABR recordLastUpdatedDate="20200101" replaced="N"><ABN status="{status}" '
        f'ABNStatusFromDate="20000101">{abn}</ABN><EntityType><EntityTypeInd>PRV</EntityTypeInd>'
        "</EntityType></ABR>\n"
    )


def _extract(abns: list[str], record_count: int | None = None) -> list[str]:
    """The lines of a small extract file, its header declaring `record_count` records."""
    count = len(abns) if record_count is None else record_count
    header = (
        '<?xml version="1.0"?><Transfer error="none"><TransferInfo><FileSequenceNumber>1'
        f"</FileSequenceNumber><RecordCount>{count}</RecordCount></TransferInfo>\n"
    )
    return [
        header,
        *(_record(a, s) for a, s in zip(abns, ["ACT", "CAN"] * len(abns), strict=False)),
        "</Transfer>\n",
    ]


def test_extract_yields_every_record_abn_current_and_cancelled():
    lines = _extract(["18123456789", "34987654321"])
    assert list(abns_in_extract(lines, "x.xml")) == [18123456789, 34987654321]


def test_extract_fails_fast_when_record_count_disagrees():
    """A truncated or reformatted file must not pass as a smaller register."""
    lines = _extract(["18123456789"], record_count=2)
    with pytest.raises(ValueError) as err:
        list(abns_in_extract(lines, "x.xml"))
    assert_diagnostic_error(err.value)
    assert "x.xml" in str(err.value)


def test_extract_fails_fast_without_a_record_count():
    lines = [_record("18123456789")]
    with pytest.raises(ValueError) as err:
        list(abns_in_extract(lines, "x.xml"))
    assert_diagnostic_error(err.value)


def test_build_from_zips_reads_every_member(tmp_path: Path):
    path = tmp_path / "part.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a.xml", "".join(_extract(["34987654321", "18123456789"])))
        zf.writestr("b.xml", "".join(_extract(["18123456789"])))
    register = build_from_zips([path])
    assert len(register) == 2
    assert "18 123 456 789" in register and "34987654321" in register


def test_register_membership_accepts_spaced_and_bare_forms():
    register = AbnRegister.from_abns([18123456789])
    assert "18 123 456 789" in register
    assert "18123456789" in register
    assert "34 987 654 321" not in register


def test_register_round_trips_through_save_and_load(tmp_path: Path):
    path = tmp_path / "abns.npy"
    AbnRegister.from_abns([34987654321, 18123456789, 18123456789]).save(path)
    loaded = AbnRegister.load(path, POOLS_PATH)
    assert len(loaded) == 2
    assert "34 987 654 321" in loaded


def test_load_fails_fast_on_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError) as err:
        AbnRegister.load(tmp_path / "absent.npy", POOLS_PATH)
    assert_diagnostic_error(err.value)
    assert "build_abn_register.py" in str(err.value)


def test_load_fails_fast_on_unsorted_array(tmp_path: Path):
    path = tmp_path / "abns.npy"
    np.save(path, np.array([34987654321, 18123456789], dtype=np.uint64))
    with pytest.raises(ValueError) as err:
        AbnRegister.load(path, POOLS_PATH)
    assert_diagnostic_error(err.value)


def test_load_fails_fast_on_wrong_dtype(tmp_path: Path):
    path = tmp_path / "abns.npy"
    np.save(path, np.array([1.5, 2.5]))
    with pytest.raises(ValueError) as err:
        AbnRegister.load(path, POOLS_PATH)
    assert_diagnostic_error(err.value)


def test_load_abn_register_expands_home_in_the_configured_path(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    AbnRegister.from_abns([18123456789]).save(tmp_path / "abns.npy")
    register = load_abn_register({"abn_register": {"path": "~/abns.npy"}}, POOLS_PATH)
    assert "18 123 456 789" in register


def test_load_abn_register_names_the_yaml_key_when_the_file_is_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError) as err:
        load_abn_register({"abn_register": {"path": str(tmp_path / "absent.npy")}}, POOLS_PATH)
    assert_diagnostic_error(err.value)
    assert "abn_register.path" in str(err.value)


def test_pools_declare_the_register_path():
    pools = yaml.safe_load(POOLS_PATH.read_text())
    assert isinstance(pools["abn_register"]["path"], str)


def test_generate_abn_redraws_a_registered_abn():
    """The first draw for this seed is registered, so the screen must move past it."""
    random.seed(7)
    first = generate_abn(registered=AbnRegister.from_abns([]))
    random.seed(7)
    screened = generate_abn(registered=AbnRegister.from_abns([int(first.replace(" ", ""))]))
    assert screened != first
    assert validate_abn(screened)


def test_generate_abn_draws_are_unchanged_by_an_empty_register():
    """An unregistered first draw is kept, so screening does not move the committed corpus."""
    random.seed(11)
    plain = [generate_abn(registered=AbnRegister.from_abns([])) for _ in range(5)]
    random.seed(11)
    unrelated = AbnRegister.from_abns([18123456789])
    assert [generate_abn(registered=unrelated) for _ in range(5)] == plain


def test_generate_abn_redraw_leaves_later_draws_untouched():
    """A redraw takes no numbers from the shared generator, so only the registered ABN moves."""
    random.seed(3)
    plain = [generate_abn(registered=AbnRegister.from_abns([])) for _ in range(4)]
    random.seed(3)
    hit = AbnRegister.from_abns([int(plain[1].replace(" ", ""))])
    screened = [generate_abn(registered=hit) for _ in range(4)]
    assert screened[1] != plain[1]
    assert [screened[0], *screened[2:]] == [plain[0], *plain[2:]]


def test_generate_abn_redraw_is_reproducible():
    draws = []
    for _ in range(2):
        random.seed(5)
        first = generate_abn(registered=AbnRegister.from_abns([]))
        random.seed(5)
        draws.append(generate_abn(registered=AbnRegister.from_abns([int(first.replace(" ", ""))])))
    assert draws[0] == draws[1]


def test_generate_abn_fails_fast_when_every_draw_is_registered():
    class _Everything:
        def __contains__(self, abn: object) -> bool:
            return True

    with pytest.raises(RuntimeError) as err:
        generate_abn(registered=_Everything())
    assert_diagnostic_error(err.value)


def test_conftest_supplies_the_seed_register_hits_by_default():
    register = abn_register.load_abn_register({}, POOLS_PATH)
    assert len(register) == len(SEED_REGISTER_HITS) + len(ACN_REGISTER_HITS) == 5
    assert all(abn in register for abn in (*SEED_REGISTER_HITS, *ACN_REGISTER_HITS))


def _real_register_or_skip() -> AbnRegister:
    """The real register, or a skip where scripts/build_abn_register.py has not been run."""
    pools = yaml.safe_load(POOLS_PATH.read_text())
    path = Path(pools["abn_register"]["path"]).expanduser()
    if not path.exists():
        pytest.skip(f"ABN register not built at {path}; run scripts/build_abn_register.py")
    return load_abn_register(pools, POOLS_PATH)


def test_seed_register_hits_are_the_conftest_stand_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """The draws the real register rejects in a seed are exactly the conftest stand-in, in order.

    The stand-in is what lets the suite reproduce the committed corpus without the real file, so it must
    move with every reseed that moves a hit. Skipped where the register has not been built.
    """
    real = _real_register_or_skip()
    hits: list[str] = []

    class _Recording:
        def __contains__(self, abn: object) -> bool:
            found = abn in real
            if found:
                hits.append(str(abn))
            return found

    monkeypatch.setattr(abn_register, "load_abn_register", lambda pools, pools_path: _Recording())
    seed = _load_seed_module()
    previous, state = os.getcwd(), random.getstate()
    os.chdir(REPO_ROOT)
    try:
        seed.build_all_entries()
    finally:
        os.chdir(previous)
        random.setstate(state)
    assert tuple(hits) == SEED_REGISTER_HITS, f"set tests/conftest.py SEED_REGISTER_HITS = {tuple(hits)}"


def test_committed_corpus_prints_no_registered_abn():
    """Every ABN in ground_truth/ is absent from the real register.

    Needs the register file built by scripts/build_abn_register.py, so it is
    skipped where that file has not been built.
    """
    pools = yaml.safe_load(POOLS_PATH.read_text())
    register = _real_register_or_skip()
    hits = []
    for name in ("receipts.yml", "invoices.yml", "bank_statements.yml"):
        for case_id, entry in yaml.safe_load((GT_DIR / name).read_text()).items():
            abn = entry["fields"].get("BUSINESS_ABN")
            if abn and abn in register:
                hits.append(f"{name}:{case_id} {abn}")
    assert not hits, f"registered ABNs printed in the corpus: {hits}"
