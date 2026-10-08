"""The files the distractor modules read, resolved against the repository root.

One source for every path `config.py`, `values.py`, `validator.py` and `providers.py` name, so a moved
file is renamed once. The per-type ground-truth and layout files are not listed here: they are read from
`document_types:` in `config/generation_config.yml`, the pipeline's own declaration, and keyed by the
extraction-schema document type each pipeline key resolves to.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from generators.exporters.eval_projection import load_extraction_schema

REPO_ROOT = Path(__file__).resolve().parents[2]
DISTRACTORS_PATH = REPO_ROOT / "config" / "distractors.yml"
DATA_POOLS_PATH = REPO_ROOT / "config" / "data_pools.yml"
LINKS_PATH = REPO_ROOT / "ground_truth" / "transaction_links.yml"
GENERATION_CONFIG_PATH = REPO_ROOT / "config" / "generation_config.yml"


class DistractorPathError(ValueError):
    """Raised when `config/generation_config.yml` cannot say where a document type's files live."""


@dataclass(frozen=True)
class DocTypeFiles:
    """Where one document type's files live.

    Attributes:
        ground_truth: The type's ground-truth YAML, absolute.
        layouts: The type's layout registry YAML, absolute.
    """

    ground_truth: Path
    layouts: Path


def _error(what: str, key: str, expected: str, recover: str) -> DistractorPathError:
    return DistractorPathError(
        "Cannot locate a document type's files.\n"
        f"  What:     {what}\n"
        f"  Where:    {GENERATION_CONFIG_PATH} -> {key}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


_EXAMPLE = (
    "document_types:\n"
    "                receipts:\n"
    "                  ground_truth: ground_truth/receipts.yml\n"
    "                  layouts: config/layouts/receipts.yml"
)


@lru_cache(maxsize=None)
def document_type_files() -> dict[str, DocTypeFiles]:
    """Return each extraction-schema document type's ground-truth and layout files.

    Read from `document_types:` in `config/generation_config.yml`; each pipeline key (`receipts`) is
    resolved to its extraction-schema type (`receipt`) and each path against the repository root.

    Returns:
        Extraction-schema document type -> its files.

    Raises:
        DistractorPathError: The section is missing, an entry lacks `ground_truth` or `layouts`, or a
            key resolves to no extraction-schema document type.
    """
    if not GENERATION_CONFIG_PATH.exists():
        raise _error(
            "the generation config does not exist.",
            "(file)",
            f"a YAML file carrying {_EXAMPLE}",
            f"restore {GENERATION_CONFIG_PATH} from git.",
        )
    data = yaml.safe_load(GENERATION_CONFIG_PATH.read_text())
    types = data.get("document_types") if isinstance(data, dict) else None
    if not isinstance(types, dict) or not types:
        raise _error(
            "no 'document_types:' mapping.",
            "document_types",
            f"one entry per document type, e.g.\n              {_EXAMPLE}",
            f"add the document_types: section to {GENERATION_CONFIG_PATH}.",
        )
    schema = load_extraction_schema()
    files: dict[str, DocTypeFiles] = {}
    for key, spec in types.items():
        canonical = schema.resolve_doc_type(str(key))
        if canonical not in schema.document_fields:
            raise _error(
                f"document type {key!r} resolves to no extraction-schema document type.",
                f"document_types.{key}",
                f"a key naming one of {sorted(schema.document_fields)}, e.g. receipts.",
                f"rename document_types.{key}, or declare it in config/extraction_schema.yml.",
            )
        for field in ("ground_truth", "layouts"):
            if not isinstance(spec, dict) or not isinstance(spec.get(field), str):
                raise _error(
                    f"document_types.{key} has no '{field}' path.",
                    f"document_types.{key}.{field}",
                    f"a repository-relative path, e.g.\n              {_EXAMPLE}",
                    f"add {field}: to document_types.{key}.",
                )
        files[canonical] = DocTypeFiles(
            ground_truth=REPO_ROOT / spec["ground_truth"], layouts=REPO_ROOT / spec["layouts"]
        )
    return files


def _files(doc_type: str) -> DocTypeFiles:
    files = document_type_files()
    if doc_type not in files:
        raise _error(
            f"no document_types entry resolves to {doc_type!r}.",
            "document_types",
            f"an entry for each of {sorted(load_extraction_schema().document_fields)}.",
            f"add the document_types entry for {doc_type!r}.",
        )
    return files[doc_type]


def ground_truth_file(doc_type: str) -> Path:
    """Return a document type's ground-truth YAML.

    Args:
        doc_type: The extraction-schema document type, e.g. `invoice`.

    Returns:
        The absolute path `document_types:` declares for it.

    Raises:
        DistractorPathError: No `document_types:` entry resolves to `doc_type`.
    """
    return _files(doc_type).ground_truth


def layout_file(doc_type: str) -> Path:
    """Return a document type's layout registry YAML.

    Args:
        doc_type: The extraction-schema document type, e.g. `invoice`.

    Returns:
        The absolute path `document_types:` declares for it.

    Raises:
        DistractorPathError: No `document_types:` entry resolves to `doc_type`.
    """
    return _files(doc_type).layouts
