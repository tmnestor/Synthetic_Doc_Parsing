"""Shared fixtures for the parser-run scripts: a tiny snapshot, and a stand-in for `conda`.

The snapshot holds one half's (or typeset set's) parsing file and its pages, laid out as `eval-set` writes
them. The fake `conda` records every call it is given, answers `printenv` from its own environment and, for
a parser command, writes one `<stem>.md` per input page (a PNG or a PDF) into the output directory the
command names, so the runner is exercised without any model.
"""

import json
import stat
import sys
from pathlib import Path

from PIL import Image

from generators.snapshot.config import TYPESET_SETS, load_snapshot_config
from generators.typst.compile import compile_page
from generators.typst.config import TYPESET_CONFIG_PATH, load_typeset_config
from tests.conftest import REPO_ROOT

SNAPSHOT_CONFIG = load_snapshot_config(REPO_ROOT / "config" / "generation_config.yml")
# Invented page names: no case in the corpus is numbered this high.
STEMS = ("CASE901_invoice", "CASE902_receipt")
FAKE_VERSION = "9.8.7"
# Invented text for a native PDF's text layer, absent from ground_truth/.
INVENTED_LINES = ("Harbour lantern ledger", "Quarterly kelp tally")

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
inputs = [Path(arg) for arg in command if Path(arg).is_dir() and any(Path(arg).glob("*.p[nd][gf]"))]
pages = sorted(inputs[0].glob("*.p[nd][gf]"))
for page in pages:
    target = Path(outputs[0]) / page.stem
    target.mkdir(parents=True, exist_ok=True)
    (target / (page.stem + ".md")).write_text("# Invented heading\\n")
print("parsed", len(pages), "pages")
sys.exit(int(os.environ.get("FAKE_CONDA_EXIT", "0")))
"""


def make_snapshot(
    root: Path, half: str = "plain", stems: tuple[str, ...] = STEMS, pdfs: dict[str, bytes] | None = None
) -> Path:
    """Write a snapshot holding one half or typeset set: its parsing file and a small image per page.

    A typeset set's pages each have a PDF beside the PNG, as the export writes them, and its parsing file
    names the PNG.

    Args:
        root: Directory that receives `snapshot_20990101/` (a second call adds a set beside the first).
        half: The half, or typeset set, to write.
        stems: The page image stems.
        pdfs: A typeset set's PDF bytes by stem; a stem not given gets a placeholder.

    Returns:
        The snapshot directory.
    """
    typeset = half in TYPESET_SETS
    prefix = (SNAPSHOT_CONFIG.typeset_prefixes if typeset else SNAPSHOT_CONFIG.half_prefixes)[half]
    snapshot = root / "snapshot_20990101"
    images = snapshot / f"{prefix}_20990101"
    images.mkdir(parents=True)
    pages = []
    for stem in stems:
        Image.new("RGB", (8, 8), "white").save(images / f"{stem}.png")
        if typeset:
            (images / f"{stem}.pdf").write_bytes((pdfs or {}).get(stem, b"%PDF-1.7 placeholder\n"))
        pages.append({"layout_dets": [], "page_info": {"image_path": f"{images.name}/{stem}.png"}})
    parsing = snapshot / SNAPSHOT_CONFIG.parsing_dir
    parsing.mkdir(exist_ok=True)
    (parsing / f"{prefix}.json").write_text(json.dumps(pages))
    return snapshot


def invented_pdf(workdir: Path, lines: tuple[str, ...] = INVENTED_LINES) -> bytes:
    """Compile a one-page native PDF holding `lines`, each its own paragraph, so each is one text line."""
    source = "\n\n".join(['#set text(font: "Carlito")', *lines]) + "\n"
    return compile_page(source, workdir=workdir, config=load_typeset_config(TYPESET_CONFIG_PATH)).pdf


def fake_conda(root: Path) -> tuple[Path, Path]:
    """Write the stand-in `conda` executable; return it and the log of its calls."""
    script = root / "fake_conda"
    script.write_text(_FAKE_CONDA.format(python=sys.executable, version=FAKE_VERSION))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script, root / "fake_conda_calls.jsonl"


def calls(log: Path) -> list[dict]:
    """The calls the fake `conda` recorded, in order."""
    return [json.loads(line) for line in log.read_text().splitlines()]
