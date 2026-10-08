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
