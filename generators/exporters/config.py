"""Load and validate config/export_config.yml.

Every key is required. A missing or invalid key fails fast with a diagnostic
naming what is wrong, where to fix it, a valid example, and the remediation step.
"""

from pathlib import Path
from typing import Any

import yaml

ENUM_KEYS: dict[str, list[str]] = {
    "abn_tfn_canonical_form": ["spaced", "digits_only"],
    "abn_tfn_equality_form": ["spaced", "digits_only"],
    "cord_extension_scoring": [
        "in_tree",
        "excluded_scored_separately",
        "excluded_unscored",
    ],
}

LIST_KEYS: dict[str, list[str]] = {
    "export_targets": ["cord", "docile", "doc_refs", "native"],
}

MAPPING_KEYS: tuple[str, ...] = ("docile_fieldtypes",)

EXAMPLES: dict[str, str] = {
    "abn_tfn_canonical_form": "abn_tfn_canonical_form: spaced",
    "abn_tfn_equality_form": "abn_tfn_equality_form: digits_only",
    "cord_extension_scoring": "cord_extension_scoring: excluded_scored_separately",
    "export_targets": "export_targets: [cord, docile, doc_refs, native]",
    "docile_fieldtypes": "docile_fieldtypes:\n  SUPPLIER_NAME: vendor_name",
}


def load_export_config(path: Path) -> dict[str, Any]:
    """Load and validate the export config.

    Args:
        path: Path to export_config.yml.

    Returns:
        The validated config dict.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the YAML is unparseable, or any key is missing or invalid.
    """
    if not path.exists():
        msg = (
            f"Export config not found: {path.resolve()}. "
            f"Create config/export_config.yml containing every required key. "
            f"Example:\n{EXAMPLES['export_targets']}\n"
            f"Remediation: copy the template from Export_Implementation_Plan.md Task 3."
        )
        raise FileNotFoundError(msg)

    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        msg = (
            f"Failed to parse YAML in {path.resolve()}: {exc}. "
            f"Check indentation, colons and quoting. "
            f"Example:\n{EXAMPLES['export_targets']}\n"
            f"Remediation: fix the syntax error at the reported line."
        )
        raise ValueError(msg) from exc

    if not isinstance(data, dict):
        msg = (
            f"Expected a top-level mapping in {path.resolve()}, got {type(data).__name__}. "
            f"Example:\n{EXAMPLES['export_targets']}\n"
            f"Remediation: replace the file contents with a key/value mapping."
        )
        raise ValueError(msg)

    for key, allowed in ENUM_KEYS.items():
        _require(data, key, path)
        if data[key] not in allowed:
            msg = (
                f"Invalid value '{data[key]}' for '{key}' in {path.resolve()}. "
                f"Allowed values: {allowed}. "
                f"Example:\n{EXAMPLES[key]}\n"
                f"Remediation: set '{key}:' to one of the allowed values."
            )
            raise ValueError(msg)

    for key, allowed_members in LIST_KEYS.items():
        _require(data, key, path)
        if not isinstance(data[key], list):
            msg = (
                f"Key '{key}' in {path.resolve()} must be a list, "
                f"got {type(data[key]).__name__}. "
                f"Example:\n{EXAMPLES[key]}\n"
                f"Remediation: write '{key}:' as a YAML list, using [] to disable every target."
            )
            raise ValueError(msg)
        for member in data[key]:
            if member not in allowed_members:
                msg = (
                    f"Unknown member '{member}' in '{key}' in {path.resolve()}. "
                    f"Allowed members: {allowed_members}. "
                    f"Example:\n{EXAMPLES[key]}\n"
                    f"Remediation: remove '{member}' or correct its spelling."
                )
                raise ValueError(msg)

    for key in MAPPING_KEYS:
        _require(data, key, path)
        if not isinstance(data[key], dict):
            msg = (
                f"Key '{key}' in {path.resolve()} must be a mapping, "
                f"got {type(data[key]).__name__}. "
                f"Example:\n{EXAMPLES[key]}\n"
                f"Remediation: write '{key}:' as a mapping of source column to fieldtype."
            )
            raise ValueError(msg)

    return data


def _require(data: dict[str, Any], key: str, path: Path) -> None:
    """Raise a four-element diagnostic if a required key is absent.

    Args:
        data: The parsed config mapping.
        key: The required key.
        path: Path to the config file, for the diagnostic.

    Raises:
        ValueError: If the key is absent.
    """
    if key not in data:
        msg = (
            f"Missing required key '{key}' in {path.resolve()}. "
            f"Every export config key is required — omitted keys are never defaulted. "
            f"Example:\n{EXAMPLES[key]}\n"
            f"Remediation: Add the '{key}:' block to config/export_config.yml."
        )
        raise ValueError(msg)
