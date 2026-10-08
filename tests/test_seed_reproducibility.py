"""Runs the shipped seed-reproducibility check as part of the suite.

The check itself lives in `scripts/check_seed_reproducibility.py` rather than
here, because `tests/` is gitignored and this property is one a consumer of the
repo needs: it is the only thing that notices when an edit to a seed script
stops the committed corpus being reproducible. This module is a thin wrapper so
the local suite still exercises it: the logic has one home.

The check reseeds inside a temporary copy of the tree, so the real
`ground_truth/` is never written.
"""

import importlib.util
from pathlib import Path

import pytest
import yaml

from tests.conftest import REPO_ROOT


def _load_check():
    """Import scripts/check_seed_reproducibility.py (scripts/ is not a package)."""
    path = REPO_ROOT / "scripts" / "check_seed_reproducibility.py"
    spec = importlib.util.spec_from_file_location("check_seed_reproducibility", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_scripts_reproduce_the_committed_ground_truth_byte_identically(capsys):
    """Both seed scripts, run in order in an isolated copy, regenerate the committed corpus exactly.

    The scripts run as subprocesses, beyond the conftest stand-in register, so the seed loads the real
    ABN register and fails fast without it: skipped where scripts/build_abn_register.py has not been run.
    """
    pools = yaml.safe_load((REPO_ROOT / "config" / "data_pools.yml").read_text())
    register_path = Path(pools["abn_register"]["path"]).expanduser()
    if not register_path.exists():
        pytest.skip(f"ABN register not built at {register_path}; run scripts/build_abn_register.py")
    check = _load_check()
    status = check.main()
    captured = capsys.readouterr()
    assert status == 0, f"seed reproducibility drifted:\n{captured.err}"
    assert "PASS" in captured.out
