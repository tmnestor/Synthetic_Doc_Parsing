"""YAML loading utilities for ground truth, layout registries, and generation config.

All loaders fail fast with diagnostic errors per the project's engineering
standards.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKING_DIR_KEYS = ("output_dir", "derived_dir")


def load_ground_truth(path: Path) -> dict:
    """Load a ground truth YAML file.

    Args:
        path: Path to the YAML file.

    Returns:
        Dict mapping case IDs to entry dicts.

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If YAML cannot be parsed.
    """
    if not path.exists():
        msg = (
            f"Ground truth file not found: {path.resolve()}. "
            f"Create the file or check the path in config/generation_config.yml."
        )
        raise FileNotFoundError(msg)

    text = path.read_text()
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = (
            f"Failed to parse YAML in {path.resolve()}: {exc}. "
            f"Check for syntax errors (indentation, colons, quotes)."
        )
        raise ValueError(msg) from exc

    if not isinstance(data, dict):
        msg = (
            f"Expected top-level mapping in {path.resolve()}, "
            f"got {type(data).__name__}. "
            f"Each top-level key should be a CASE ID."
        )
        raise ValueError(msg)

    return data


def load_layout_registry(path: Path) -> dict:
    """Load a layout registry YAML file.

    Args:
        path: Path to the layout YAML file.

    Returns:
        Dict mapping layout IDs to layout config dicts.

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If YAML cannot be parsed.
    """
    if not path.exists():
        msg = f"Layout registry not found: {path.resolve()}. Create the file under config/layouts/."
        raise FileNotFoundError(msg)

    text = path.read_text()
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = f"Failed to parse layout YAML in {path.resolve()}: {exc}."
        raise ValueError(msg) from exc

    if not isinstance(data, dict):
        msg = f"Expected mapping in {path.resolve()}, got {type(data).__name__}."
        raise ValueError(msg)

    # If the YAML has a top-level "layouts:" wrapper, extract the inner dict.
    # Underscore-prefixed siblings are anchor definitions (e.g. `_bank_base`,
    # `_cba`) used for de-duplication via YAML merge keys — they are not
    # layouts and must not surface in the registry. Any other sibling is a
    # mis-indented layout and must fail fast rather than be silently
    # swallowed as a bogus "layout id".
    if "layouts" in data and isinstance(data["layouts"], dict):
        stray = sorted(k for k in data if k != "layouts" and not str(k).startswith("_"))
        if stray:
            msg = (
                f"Unexpected top-level key(s) {stray} in {path.resolve()}.\n"
                f"  What:     only 'layouts:' and underscore-prefixed anchor "
                f"definitions may sit at the top level.\n"
                f"  Where:    {path.resolve()}\n"
                f"  Expected: layouts:\\n  <layout_id>: ...   plus optional "
                f"_anchor: &anchor blocks.\n"
                f"  Recover:  indent {stray} under 'layouts:', or rename to "
                f"'_{stray[0]}' if it is an anchor definition."
            )
            raise ValueError(msg)
        return data["layouts"]

    return data


def _resolve_working_dir(value: object, key: str, config_path: Path) -> str:
    """Resolve a working directory against the repository root, outside it.

    Args:
        value: The YAML value, relative to the repository root or absolute.
        key: The config key, for diagnostics.
        config_path: The config file, for diagnostics.

    Returns:
        The absolute path as a string.

    Raises:
        ValueError: If the path lies inside the repository, or its parent is missing.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(
            "Invalid working directory.\n"
            f"  What:     '{key}' must be a path string, got {value!r}.\n"
            f"  Where:    {config_path.resolve()} -> {key}\n"
            f"  Expected: {key}: ../evaluation_data/{key.removesuffix('_dir')}\n"
            f"  Recover:  set '{key}' to a path string in {config_path.name}."
        )
    resolved = (REPO_ROOT / value).resolve()
    where = f"  Where:    {config_path.resolve()} -> {key}\n"
    expected = f"  Expected: {key}: ../evaluation_data/{Path(value).name or key}\n"
    if resolved.is_relative_to(REPO_ROOT):
        raise ValueError(
            "Invalid working directory.\n"
            f"  What:     '{key}' resolves inside the repository ({resolved}); generated "
            "content must live outside it.\n"
            f"{where}{expected}"
            f"  Recover:  point '{key}' under ../evaluation_data/ in {config_path.name}."
        )
    if not resolved.parent.is_dir():
        raise ValueError(
            "Invalid working directory.\n"
            f"  What:     the parent of '{key}' does not exist: {resolved.parent}.\n"
            f"{where}{expected}"
            f"  Recover:  create {resolved.parent}, or correct '{key}'."
        )
    return str(resolved)


def load_generation_config(path: Path) -> dict:
    """Load the master generation config.

    Args:
        path: Path to generation_config.yml.

    Returns:
        Config dict with document_types, degradation params, etc.

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If YAML cannot be parsed or required keys missing.
    """
    if not path.exists():
        msg = (
            f"Generation config not found: {path.resolve()}. "
            f"Create config/generation_config.yml with 'document_types' mapping."
        )
        raise FileNotFoundError(msg)

    text = path.read_text()
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = f"Failed to parse config YAML in {path.resolve()}: {exc}."
        raise ValueError(msg) from exc

    required_keys = {
        "output_dir": "../evaluation_data/output",
        "derived_dir": "../evaluation_data/derived",
        "ground_truth_dir": "ground_truth",
        "document_types": "{receipts: {ground_truth: ..., layouts: ..., output_subdir: receipts, ...}}",
        "distractor_output_subdir": "distractor",
        "labelled_varied_output_subdir": "labelled_varied",
    }
    for key, example in required_keys.items():
        if key not in data:
            raise ValueError(
                "Incomplete generation config.\n"
                f"  What:     required key '{key}' is missing.\n"
                f"  Where:    {path.resolve()} -> {key}\n"
                f"  Expected: {key}: {example}\n"
                f"  Recover:  add '{key}:' at the top level of {path.name}."
            )

    for key in WORKING_DIR_KEYS:
        data[key] = _resolve_working_dir(data[key], key, path)
    _check_distractor_subdir(data["distractor_output_subdir"], path)
    _check_labelled_varied_subdir(
        data["labelled_varied_output_subdir"], data["distractor_output_subdir"], path
    )
    _check_generate_clean(data["document_types"], path)
    return data


def _check_generate_clean(document_types: object, config_path: Path) -> None:
    """Refuse a document type whose `generate_clean` is missing or not a boolean.

    `generate` reads it to decide whether to save the plain images; with no Python default, the YAML
    alone says what a run writes.

    Args:
        document_types: The YAML `document_types` mapping.
        config_path: The config file, for diagnostics.

    Raises:
        ValueError: `document_types` is not a mapping, or a document type omits `generate_clean` or
            gives it a non-boolean value.
    """
    if not isinstance(document_types, dict):
        raise ValueError(
            "Invalid generation config.\n"
            f"  What:     document_types is a {type(document_types).__name__} ({document_types!r}), not "
            "a mapping of document type to its settings.\n"
            f"  Where:    {config_path.resolve()} -> document_types\n"
            "  Expected: document_types:\n"
            "              receipts: {ground_truth: ground_truth/receipts.yml, layouts: "
            "config/layouts/receipts.yml, output_subdir: receipts, generate_clean: true}\n"
            f"  Recover:  rewrite document_types in {config_path.name} as a mapping with one entry per "
            "document type."
        )
    for doc_type, doc_cfg in document_types.items():
        value = doc_cfg.get("generate_clean") if isinstance(doc_cfg, dict) else None
        if isinstance(value, bool):
            continue
        what = (
            "is missing"
            if not isinstance(doc_cfg, dict) or "generate_clean" not in doc_cfg
            else f"is {value!r}, not true or false"
        )
        raise ValueError(
            "Invalid generation config.\n"
            f"  What:     document type '{doc_type}': generate_clean {what}; generate reads it to "
            "decide whether to save the plain images.\n"
            f"  Where:    {config_path.resolve()} -> document_types.{doc_type}.generate_clean\n"
            "  Expected: generate_clean: true (or false to render without saving the plain images)\n"
            f"  Recover:  set document_types.{doc_type}.generate_clean in {config_path.name}."
        )


# Plain renders are written to `<output_dir>/clean/<type>/` (read by `pipeline.generate`), so the
# distractor directory may not reuse it, in any letter case (macOS file systems fold case).
CLEAN_SUBDIR = "clean"


def _check_distractor_subdir(value: object, config_path: Path) -> None:
    """Refuse a `distractor_output_subdir` that is not one plain directory name beside `clean`.

    Args:
        value: The YAML value.
        config_path: The config file, for diagnostics.

    Raises:
        ValueError: If the value is not a non-empty string naming a single directory other than
            `clean` (in any letter case), `.` or `..`.
    """
    single = isinstance(value, str) and value.casefold() not in ("", ".", "..", CLEAN_SUBDIR.casefold())
    if single and Path(str(value)).name != value:
        single = False
    if not single:
        raise ValueError(
            "Invalid distractor output directory.\n"
            f"  What:     'distractor_output_subdir' is {value!r}, not one directory name; distractor "
            f"renders are written to <output_dir>/<distractor_output_subdir>/<type>/, beside "
            f"<output_dir>/{CLEAN_SUBDIR}/.\n"
            f"  Where:    {config_path.resolve()} -> distractor_output_subdir\n"
            "  Expected: distractor_output_subdir: distractor (a single name, no '/', not "
            f"'{CLEAN_SUBDIR}' in any letter case, '.' or '..').\n"
            f"  Recover:  set distractor_output_subdir to a plain directory name in {config_path.name}."
        )


def _check_labelled_varied_subdir(value: object, distractor_subdir: str, config_path: Path) -> None:
    """Refuse a `labelled_varied_output_subdir` that is not one directory name of its own.

    The labelled_varied renders share their filenames with the plain and distractor renders, so their
    directory may reuse neither `clean` nor `distractor_output_subdir`, in any letter case.

    Args:
        value: The YAML value.
        distractor_subdir: The validated `distractor_output_subdir`.
        config_path: The config file, for diagnostics.

    Raises:
        ValueError: If the value is not a non-empty string naming a single directory other than
            `clean`, `distractor_output_subdir` (in any letter case), `.` or `..`.
    """
    taken = (CLEAN_SUBDIR.casefold(), distractor_subdir.casefold())
    single = isinstance(value, str) and value.casefold() not in ("", ".", "..", *taken)
    if single and Path(str(value)).name != value:
        single = False
    if not single:
        raise ValueError(
            "Invalid labelled varied output directory.\n"
            f"  What:     'labelled_varied_output_subdir' is {value!r}, not one directory name of its own; "
            "labelled varied renders are written to <output_dir>/<labelled_varied_output_subdir>/<type>/, "
            f"beside <output_dir>/{CLEAN_SUBDIR}/ and <output_dir>/{distractor_subdir}/ under the same "
            "filenames.\n"
            f"  Where:    {config_path.resolve()} -> labelled_varied_output_subdir\n"
            "  Expected: labelled_varied_output_subdir: labelled_varied (a single name, no '/', not "
            f"'{CLEAN_SUBDIR}' or '{distractor_subdir}' in any letter case, '.' or '..').\n"
            "  Recover:  set labelled_varied_output_subdir to a plain directory name of its own in "
            f"{config_path.name}."
        )
