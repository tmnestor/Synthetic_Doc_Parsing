"""The snapshot export's configuration: the `eval_set:` block of config/generation_config.yml.

Every key is required and checked before anything is read or rendered. `load_eval_set_config` (in
`generators.eval_set`) keeps its checks of the block, the ground-truth file names and the document
types (its presence check of the seven keys); this module refuses the superseded keys first, checks each
key's shape, and resolves the root the snapshot is written under (Decision 3: against the repository
root, outside it, already existing).
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

from generators.eval_set import _err as config_error
from generators.eval_set import load_eval_set_config
from generators.loader import REPO_ROOT

_ROOT_KEY = "eval_set"

# The four halves, in the order they are rendered and reported. The keys are a contract with the
# export (each names one render); the values, the directory names, are the operator's.
HALVES = ("plain", "distractor", "labelled_varied", "degraded")

# Keys an earlier export read, mapped to what replaced them. A stale config naming one is refused, so
# it cannot look meaningful.
SUPERSEDED_KEYS = {
    "clean_dir_prefix": "half_prefixes.plain (and the distractor and labelled_varied halves beside it)",
    "degraded_dir_prefix": "half_prefixes.degraded",
}

# Single-directory-name keys, with the example each diagnostic shows.
_NAME_KEYS = {
    "snapshot_dir_prefix": "snapshot_dir_prefix: snapshot",
    "geometry_dir": "geometry_dir: geometry",
}


@dataclass(frozen=True)
class SnapshotConfig:
    """The validated `eval_set:` block.

    Attributes:
        config_path: The generation config the block was read from, resolved.
        output_root: The existing directory snapshots are written under.
        snapshot_dir_prefix: The snapshot directory's name before its date stamp.
        half_prefixes: Half (one of `HALVES`) -> its directory name before the date stamp.
        geometry_dir: The name of the snapshot's geometry directory.
        document_types: Keys of the top-level `document_types` block to export, in order.
        csv_name: The ground-truth CSV file name written into each half.
        jsonl_name: The ground-truth JSONL file name written into each half.
    """

    config_path: Path
    output_root: Path
    snapshot_dir_prefix: str
    half_prefixes: dict[str, str]
    geometry_dir: str
    document_types: tuple[str, ...]
    csv_name: str
    jsonl_name: str


def _err(what: str, *, config_path: Path, key: str, expected: str, recover: str) -> ValueError:
    """Build a four-part diagnostic naming `<abs config> -> eval_set.<key>` (the shared helper)."""
    return config_error(
        what, path=config_path, key_path=f"{_ROOT_KEY}.{key}", expected=expected, recover=recover
    )


def _is_single_name(value: object) -> bool:
    """Report whether a value is one directory name: a non-empty string with no separator or dot-name."""
    return isinstance(value, str) and bool(value) and Path(value).name == value and value not in (".", "..")


def resolve_output_root(value: object, *, config_path: Path) -> Path:
    """Resolve `eval_set.output_root` against the repository root (Decision 3).

    Args:
        value: The YAML value: a path relative to the repository root, or absolute.
        config_path: The generation config, named in each diagnostic.

    Returns:
        The absolute, existing directory snapshots are written under.

    Raises:
        ValueError: The value is missing or not a string, resolves inside the repository, or is not an
            existing directory.
    """
    expected = "output_root: ../evaluation_data (a directory beside the repository, which must exist)"
    recover = "create the directory beside the repository, or correct eval_set.output_root."
    if not isinstance(value, str) or not value:
        raise _err(
            f"eval_set.output_root must be a path string, got {value!r}.",
            config_path=config_path,
            key="output_root",
            expected=expected,
            recover=recover,
        )
    resolved = (REPO_ROOT / value).resolve()
    if resolved.is_relative_to(REPO_ROOT):
        raise _err(
            f"eval_set.output_root resolves inside the repository ({resolved}); nothing a snapshot "
            "contains may be written there.",
            config_path=config_path,
            key="output_root",
            expected=expected,
            recover=recover,
        )
    if not resolved.is_dir():
        state = "is not a directory" if resolved.exists() else "does not exist"
        raise _err(
            f"eval_set.output_root resolves to {resolved}, which {state}.",
            config_path=config_path,
            key="output_root",
            expected=expected,
            recover=recover,
        )
    return resolved


def _half_prefixes(value: object, *, config_path: Path) -> dict[str, str]:
    """Check `half_prefixes`: exactly the four halves, each a distinct single directory name."""
    example = "half_prefixes: {" + ", ".join(f"{half}: {half}" for half in HALVES) + "}"
    if not isinstance(value, dict) or sorted(value) != sorted(HALVES):
        keys = sorted(value) if isinstance(value, dict) else value
        raise _err(
            f"eval_set.half_prefixes must map exactly the halves {list(HALVES)}, got {keys!r}.",
            config_path=config_path,
            key="half_prefixes",
            expected=example,
            recover="list each of the four halves once under eval_set.half_prefixes.",
        )
    for half in HALVES:
        if not _is_single_name(value[half]):
            raise _err(
                f"eval_set.half_prefixes.{half} must be a single directory name, got {value[half]!r}.",
                config_path=config_path,
                key=f"half_prefixes.{half}",
                expected=example,
                recover=f"set half_prefixes.{half} to a name with no '/', e.g. {half}.",
            )
    if len(set(value.values())) != len(HALVES):
        raise _err(
            f"eval_set.half_prefixes repeats a directory name: {dict(value)!r}; two halves would share "
            "one directory.",
            config_path=config_path,
            key="half_prefixes",
            expected=example,
            recover="give each half its own directory name.",
        )
    return {half: str(value[half]) for half in HALVES}


def load_snapshot_config(config_path: Path) -> SnapshotConfig:
    """Load and check the `eval_set:` block, resolving the output root.

    Args:
        config_path: Path to generation_config.yml.

    Returns:
        The validated configuration.

    Raises:
        FileNotFoundError: `config_path` does not exist.
        ValueError: A key is missing, empty, mistyped or superseded, or the output root is unusable.
    """
    # Superseded keys first, so a stale block is told what replaced its keys rather than that the new
    # ones are missing.
    raw = yaml.safe_load(config_path.read_text()) if config_path.is_file() else None
    stale = raw.get(_ROOT_KEY) if isinstance(raw, dict) else None
    for key, replacement in SUPERSEDED_KEYS.items():
        if isinstance(stale, dict) and key in stale:
            raise _err(
                f"eval_set.{key} is superseded by eval_set.{replacement}; the export no longer reads it.",
                config_path=config_path,
                key=key,
                expected="half_prefixes: {" + ", ".join(f"{half}: {half}" for half in HALVES) + "}",
                recover=f"delete eval_set.{key}; set eval_set.{replacement} instead.",
            )
    cfg = load_eval_set_config(config_path)
    for key in ("csv_name", "jsonl_name"):
        if not _is_single_name(cfg[key]):
            raise _err(
                f"eval_set.{key} must be a single file name, got {cfg[key]!r}.",
                config_path=config_path,
                key=key,
                expected=f"{key}: ground_truth.{key.removesuffix('_name')}",
                recover=f"set eval_set.{key} to a file name with no '/'.",
            )
    for key, example in _NAME_KEYS.items():
        if not _is_single_name(cfg.get(key)):
            raise _err(
                f"eval_set.{key} is missing or not a single directory name: {cfg.get(key)!r}.",
                config_path=config_path,
                key=key,
                expected=example,
                recover=f"set eval_set.{key} to a name with no '/'.",
            )
    halves = _half_prefixes(cfg["half_prefixes"], config_path=config_path)
    root = resolve_output_root(cfg["output_root"], config_path=config_path)
    return SnapshotConfig(
        config_path=config_path.resolve(),
        output_root=root,
        snapshot_dir_prefix=str(cfg["snapshot_dir_prefix"]),
        half_prefixes=halves,
        geometry_dir=str(cfg["geometry_dir"]),
        document_types=tuple(str(dtype) for dtype in cfg["document_types"]),
        csv_name=str(cfg["csv_name"]),
        jsonl_name=str(cfg["jsonl_name"]),
    )
