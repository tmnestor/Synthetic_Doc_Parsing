"""The public ABN register, reduced to the set of ABNs it lists, for screening invented ABNs.

`generate_abn()` draws a checksum-valid ABN at random. About a billion ABNs pass the checksum and tens
of millions have been issued, so a random draw can land on a real business's number and print it
beside an invented name. The register screens those draws out.

Source: the ABN Lookup Bulk Extract published on data.gov.au (CC BY 3.0 AU), two ZIPs of XML holding
one `<ABR>` record per line, current and cancelled ABNs alike. `scripts/build_abn_register.py`
reduces it to a sorted `uint64` array of the ABNs alone (no names) at the path declared in
`config/data_pools.yml` under `abn_register.path`. The array is too large to commit, so each machine
that seeds ground truth builds its own.
"""

import re
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path

import numpy as np

_BUILD_COMMAND = "python scripts/build_abn_register.py public_split_1_10.zip public_split_11_20.zip"
_ABN_RE = re.compile(r'<ABN status="[A-Z]+" ABNStatusFromDate="\d*">(\d{11})</ABN>')
_RECORD_COUNT_RE = re.compile(r"<RecordCount>(\d+)</RecordCount>")


def _digits(abn: object) -> int | None:
    """The ABN as an integer, from a spaced or bare string or an int; None if it is not 11 digits."""
    if isinstance(abn, int):
        return abn
    if isinstance(abn, str):
        bare = abn.replace(" ", "")
        if len(bare) == 11 and bare.isdigit():
            return int(bare)
    return None


class AbnRegister:
    """A sorted, de-duplicated set of ABNs, held as a `uint64` array for compact membership tests."""

    def __init__(self, numbers: np.ndarray) -> None:
        self._numbers = numbers

    @classmethod
    def from_abns(cls, abns: Iterable[int]) -> "AbnRegister":
        """Build a register from integer ABNs, in any order and with repeats."""
        return cls(np.unique(np.fromiter(abns, dtype=np.uint64)))

    def __contains__(self, abn: object) -> bool:
        value = _digits(abn)
        if value is None:
            return False
        i = int(np.searchsorted(self._numbers, np.uint64(value)))
        return i < len(self._numbers) and int(self._numbers[i]) == value

    def __len__(self) -> int:
        return len(self._numbers)

    def save(self, path: Path) -> None:
        """Write the array to `path` as a `.npy` file, creating its directory."""
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, self._numbers)

    @classmethod
    def load(cls, path: Path, pools_path: Path) -> "AbnRegister":
        """Load a register written by `save`, failing fast if it is absent or malformed.

        Args:
            path: The `.npy` file.
            pools_path: The YAML that declared `path`, named in any diagnostic.

        Raises:
            FileNotFoundError: `path` does not exist.
            ValueError: the array is not a sorted, de-duplicated, one-dimensional `uint64` array.
        """
        if not path.exists():
            raise FileNotFoundError(
                "ABN register file not found.\n"
                f"  What:     {path} does not exist, so invented ABNs cannot be screened against the "
                "public register.\n"
                f"  Where:    {pools_path} -> 'abn_register.path'.\n"
                "  Expected: a .npy file built from the ABN Lookup Bulk Extract (data.gov.au), e.g. "
                "'abn_register:\\n  path: ~/data/abr/abn_registered.npy'.\n"
                "  Recover:  download both extract ZIPs from data.gov.au and run "
                f"`{_BUILD_COMMAND}`, or point 'abn_register.path' at an existing build."
            )
        numbers = np.load(path, allow_pickle=False)
        problem = None
        if numbers.dtype != np.uint64 or numbers.ndim != 1:
            problem = f"it holds a {numbers.ndim}-dimensional {numbers.dtype} array"
        elif len(numbers) > 1 and not bool(np.all(numbers[1:] > numbers[:-1])):
            problem = "its ABNs are not strictly ascending"
        if problem:
            raise ValueError(
                "ABN register file is malformed.\n"
                f"  What:     {path}: {problem}.\n"
                f"  Where:    {pools_path} -> 'abn_register.path'.\n"
                "  Expected: a one-dimensional uint64 array of 11-digit ABNs, strictly ascending.\n"
                f"  Recover:  rebuild it with `{_BUILD_COMMAND}`."
            )
        return cls(numbers)


def load_abn_register(pools: dict, pools_path: Path) -> AbnRegister:
    """Load the register at `abn_register.path` in the pools, expanding `~`.

    Args:
        pools: The parsed `config/data_pools.yml`.
        pools_path: Where `pools` was read from, named in any diagnostic.
    """
    return AbnRegister.load(Path(pools["abn_register"]["path"]).expanduser(), pools_path)


def abns_in_extract(lines: Iterable[str], source: str) -> Iterator[int]:
    """Yield the ABN of every record in one extract XML file, checking the header's record count.

    Args:
        lines: The file's lines.
        source: The file's name, for diagnostics.

    Raises:
        ValueError: the file declares no record count, or yields a different number of ABNs.
    """
    declared = None
    found = 0
    for line in lines:
        if declared is None:
            header = _RECORD_COUNT_RE.search(line)
            if header:
                declared = int(header.group(1))
        for match in _ABN_RE.finditer(line):
            found += 1
            yield int(match.group(1))
    if declared is None or declared != found:
        what = "declares no <RecordCount>" if declared is None else f"declares {declared} records"
        raise ValueError(
            "ABN extract file failed its record count.\n"
            f"  What:     {source} {what} but yielded {found} ABNs.\n"
            f"  Where:    {source}, inside the extract ZIP passed to scripts/build_abn_register.py.\n"
            "  Expected: a header '<RecordCount>N</RecordCount>' and N records shaped "
            '\'<ABN status="ACT" ABNStatusFromDate="YYYYMMDD">NNNNNNNNNNN</ABN>\'.\n'
            "  Recover:  re-download the extract ZIPs from data.gov.au; if the format has changed, "
            "update the patterns in generators/abn_register.py."
        )


def build_from_zips(zip_paths: Iterable[Path]) -> AbnRegister:
    """Read every XML member of the extract ZIPs into one register."""
    chunks = []
    for zip_path in zip_paths:
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                with zf.open(member) as fh:
                    lines = (raw.decode("utf-8") for raw in fh)
                    numbers = np.fromiter(abns_in_extract(lines, f"{zip_path.name}:{member}"), np.uint64)
                chunks.append(numbers)
    return AbnRegister(np.unique(np.concatenate(chunks)) if chunks else np.empty(0, np.uint64))
