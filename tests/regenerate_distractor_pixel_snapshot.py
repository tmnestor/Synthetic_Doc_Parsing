"""Re-capture tests/fixtures/distractor_pixel_snapshot.json. Dry run unless --confirm.

Pins every receipt's, invoice's and bank statement's render with its selected distractors drawn, overlay
included (hash, size and scored boxes). Refuses to write unless the installed Pillow equals the
environment.yml pin, because font metrics, and so every hash, depend on it.

`--types invoice` re-renders that type alone and keeps every other type's pinned entries exactly as they
are, so a deliberate change to one type cannot move another's pins; the report counts the differences per
type either way.
"""

import argparse
import json
import sys
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.regenerate_pixel_snapshot import pinned_pillow  # noqa: E402
from tests.snapshot_tools import (  # noqa: E402
    DISTRACTOR_DOC_TYPES,
    DISTRACTOR_SNAPSHOT_PATH,
    compare,
    merge_types,
    render_corpus,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="write the snapshot")
    parser.add_argument(
        "--types",
        nargs="+",
        choices=DISTRACTOR_DOC_TYPES,
        default=list(DISTRACTOR_DOC_TYPES),
        help="re-render only these document types, keeping the others' pins",
    )
    parser.add_argument("--pretend-pillow", help=argparse.SUPPRESS)
    args = parser.parse_args()

    installed = args.pretend_pillow or version("pillow")
    if args.confirm and installed != pinned_pillow():
        sys.exit(f"Installed Pillow {installed} differs from the pin {pinned_pillow()}; refusing to write.")

    path = DISTRACTOR_SNAPSHOT_PATH
    old = json.loads(path.read_text()) if path.exists() else {}
    rendered = render_corpus(tuple(args.types), distractors=True)
    current = merge_types(old, rendered, tuple(args.types))
    changes = compare(old, current)
    print(
        f"{len(rendered)} distractor pages rendered ({', '.join(args.types)}); {len(changes)} differences."
    )
    for doc_type in DISTRACTOR_DOC_TYPES:
        keys = {key for key, record in current.items() if record["type"] == doc_type}
        changed = sorted({line.split(":")[0] for line in changes} & keys)
        print(f"  {doc_type}: {len(changed)} of {len(keys)} entries differ")
    for line in changes[:60]:
        print(f"  {line}")
    if not args.confirm:
        print("Dry run: nothing written. Pass --confirm to write.")
        return
    path.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
