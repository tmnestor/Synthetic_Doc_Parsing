"""Shared fixtures for the parser-run scripts: a tiny snapshot, and a stand-in for `conda`.

The snapshot holds one half's parsing file and its images, laid out as `eval-set` writes them. The fake
`conda` records every call it is given, answers `printenv` from its own environment and, for a parser
command, writes one `<stem>.md` per input image
into the output directory the command names, so the runner is exercised without any model.
"""

import json
import stat
import sys
from pathlib import Path

from PIL import Image

from generators.snapshot.config import load_snapshot_config
from tests.conftest import REPO_ROOT

SNAPSHOT_CONFIG = load_snapshot_config(REPO_ROOT / "config" / "generation_config.yml")
# Invented page names: no case in the corpus is numbered this high.
STEMS = ("CASE901_invoice", "CASE902_receipt")
FAKE_VERSION = "9.8.7"

_FAKE_CONDA = """#!{python}
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ["FAKE_CONDA_LOG"], "a") as log:
    log.write(json.dumps({{"argv": args, "env": {{k: v for k, v in os.environ.items()
                                               if k.startswith(("MINERU", "HF_", "TORCH"))}}}}) + "\\n")
command = args[args.index("-n") + 2:]
if command[0] == "printenv":
    value = os.environ.get(command[1], "")
    print(value)
    sys.exit(0 if value else 1)
if command[:2] == ["python", "-c"]:
    print("{version}")
    sys.exit(0)
outputs = [args[i + 1] for i, arg in enumerate(args) if arg in ("-o", "--output", "--output_dir")]
inputs = [Path(arg) for arg in command if Path(arg).is_dir() and any(Path(arg).glob("*.png"))]
for image in sorted(inputs[0].glob("*.png")):
    target = Path(outputs[0]) / image.stem
    target.mkdir(parents=True, exist_ok=True)
    (target / (image.stem + ".md")).write_text("# Invented heading\\n")
print("parsed", len(list(inputs[0].glob("*.png"))), "pages")
sys.exit(int(os.environ.get("FAKE_CONDA_EXIT", "0")))
"""


def make_snapshot(root: Path, half: str = "plain", stems: tuple[str, ...] = STEMS) -> Path:
    """Write a snapshot holding one half: its parsing file and a small image per page.

    Args:
        root: Directory that receives `snapshot_20990101/`.
        half: The half to write.
        stems: The page image stems.

    Returns:
        The snapshot directory.
    """
    snapshot = root / "snapshot_20990101"
    images = snapshot / f"{SNAPSHOT_CONFIG.half_prefixes[half]}_20990101"
    images.mkdir(parents=True)
    pages = []
    for stem in stems:
        Image.new("RGB", (8, 8), "white").save(images / f"{stem}.png")
        pages.append({"layout_dets": [], "page_info": {"image_path": f"{images.name}/{stem}.png"}})
    parsing = snapshot / SNAPSHOT_CONFIG.parsing_dir
    parsing.mkdir()
    (parsing / f"{SNAPSHOT_CONFIG.half_prefixes[half]}.json").write_text(json.dumps(pages))
    return snapshot


def fake_conda(root: Path) -> tuple[Path, Path]:
    """Write the stand-in `conda` executable; return it and the log of its calls."""
    script = root / "fake_conda"
    script.write_text(_FAKE_CONDA.format(python=sys.executable, version=FAKE_VERSION))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script, root / "fake_conda_calls.jsonl"


def calls(log: Path) -> list[dict]:
    """The calls the fake `conda` recorded, in order."""
    return [json.loads(line) for line in log.read_text().splitlines()]
