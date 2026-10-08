"""Build the ABN register that screens invented ABNs from the ABN Lookup Bulk Extract.

Download both ZIPs of the extract from data.gov.au ("ABN Bulk Extract", CC BY 3.0 AU), then:

    python scripts/build_abn_register.py public_split_1_10.zip public_split_11_20.zip

The register is written to `abn_register.path` in config/data_pools.yml. Only the ABN numbers are
kept, current and cancelled alike; names and addresses are discarded.
"""

# Ensure we can import from project root
import sys
from pathlib import Path

import typer

sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.abn_register import build_from_zips  # noqa: E402
from generators.content_engine import load_pools  # noqa: E402


def main(zips: list[Path] = typer.Argument(..., exists=True, dir_okay=False, help="Extract ZIPs")) -> None:
    """Reduce the extract ZIPs to a sorted array of ABNs at the configured register path."""
    pools = load_pools()
    out_path = Path(pools["abn_register"]["path"]).expanduser()
    register = build_from_zips(zips)
    register.save(out_path)
    print(f"Wrote {len(register):,} distinct ABNs to {out_path}")


if __name__ == "__main__":
    typer.run(main)
