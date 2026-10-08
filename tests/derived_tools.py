"""Run generate + derive into a scratch root and hash what derive writes."""

import hashlib
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DERIVED_SNAPSHOT_PATH = REPO_ROOT / "tests" / "fixtures" / "derived_snapshot.json"


def derive_into(root: Path) -> dict[str, str]:
    """Generate and derive under `root`; return normalised per-file hashes.

    Args:
        root: Scratch directory that receives `output/` and `derived/`.

    Returns:
        Map of each derived file's path (relative to the derived directory) to the
        SHA-256 of its bytes, with `str(root)` replaced by `<ROOT>` first.
    """
    config = yaml.safe_load((REPO_ROOT / "config" / "generation_config.yml").read_text())
    config["output_dir"] = str(root / "output")
    config["derived_dir"] = str(root / "derived")
    config_path = root / "generation_config.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    for command in ("generate", "derive"):
        try:
            subprocess.run(
                [sys.executable, "-m", "generators.pipeline", command, "--config", str(config_path)],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as err:
            raise RuntimeError(
                f"pipeline {command} failed (rc={err.returncode})\n"
                f"stdout: {err.stdout}\nstderr: {err.stderr}"
            ) from err
    derived = root / "derived"
    return {
        str(path.relative_to(derived)): hashlib.sha256(
            path.read_bytes().replace(str(root).encode(), b"<ROOT>")
        ).hexdigest()
        for path in sorted(derived.rglob("*"))
        if path.is_file()
    }
