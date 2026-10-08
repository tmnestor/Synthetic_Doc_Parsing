"""Re-capture tests/fixtures/derived_snapshot.json. Dry run unless --confirm."""

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.derived_tools import DERIVED_SNAPSHOT_PATH, derive_into  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="write the snapshot")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as scratch:
        current = derive_into(Path(scratch))
    print(f"{len(current)} derived files hashed.")
    if not args.confirm:
        print("Dry run: nothing written. Pass --confirm to write.")
        return
    DERIVED_SNAPSHOT_PATH.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
    print(f"Wrote {DERIVED_SNAPSHOT_PATH}")


if __name__ == "__main__":
    main()
