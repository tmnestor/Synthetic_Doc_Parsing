"""The snapshot's README.md, generated from `readme_template.md` and the manifest, and the earlier
snapshot its provenance record names.

The README is published with the snapshot to readers who never see the repository, so it says what the
data is and how to use it, never how it was produced. Every count and date in it is filled from the
manifest (without its `files` map, since the README is itself one of the files hashed). The field
definitions come from the required `field_descriptions:` block of `config/extraction_schema.yml`
(Decision 13). The commits since the latest earlier snapshot go into the provenance record, not the
README (Decision 14).
"""

import json
import re
import string
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from generators.exporters.eval_projection import ExtractionSchema
from generators.labels.keys import LABEL_STYLES
from generators.snapshot.config import HALVES
from generators.snapshot.manifest import PROVENANCE_NAME

TEMPLATE_PATH = Path(__file__).resolve().parent / "readme_template.md"

_DESCRIPTIONS_KEY = "field_descriptions"
_DESCRIPTIONS_EXAMPLE = (
    "field_descriptions:\n  TOTAL_AMOUNT: \"The total amount payable or paid, with a leading '$'.\""
)

# The relations an earlier snapshot's commit can have to this one's.
ANCESTOR = "ancestor"
UNKNOWN = "unknown"
DIVERGED = "diverged"


class ReadmeError(ValueError):
    """The README cannot be generated: a field description is missing or stray, or an input unreadable."""


@dataclass(frozen=True)
class PreviousSnapshot:
    """The latest earlier snapshot under the same root that carries a provenance record.

    Attributes:
        directory: Its snapshot directory name.
        snapshot_date: Its record's `snapshot_date`.
        commit: Its record's `generator_commit`.
        relation: `ancestor` (its commit is an ancestor of this one's), `unknown` (the repository does
            not know its commit) or `diverged` (known, but not an ancestor).
        subjects: For an ancestor, the subjects of the commits after it up to this one, oldest first.
    """

    directory: str
    snapshot_date: str
    commit: str
    relation: str
    subjects: tuple[str, ...] = ()

    def as_record(self) -> dict:
        """The form the provenance record stores, keys sorted."""
        return {
            "commit": self.commit,
            "commits_since": list(self.subjects),
            "directory": self.directory,
            "relation": self.relation,
            "snapshot_date": self.snapshot_date,
        }


def _refuse(what: str, *, where: str, expected: str, recover: str) -> ReadmeError:
    """Build a four-part diagnostic."""
    return ReadmeError(
        "Cannot generate the snapshot README.\n"
        f"  What:     {what}\n"
        f"  Where:    {where}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def load_field_descriptions(schema: ExtractionSchema) -> dict[str, str]:
    """Read `field_descriptions:` from the extraction schema, one sentence per extraction field.

    Args:
        schema: The loaded extraction schema; its `source_path` is read again for the block.

    Returns:
        Field -> description, for exactly the fields named under `document_fields`.

    Raises:
        ReadmeError: The block is missing or not a mapping, a field has no description, or a description
            names no field.
    """
    path = schema.source_path.resolve()
    data = yaml.safe_load(path.read_text()) or {}
    block = data.get(_DESCRIPTIONS_KEY)
    fields = [f for fields in schema.document_fields.values() for f in fields]
    known = list(dict.fromkeys(fields))
    if not isinstance(block, dict):
        raise _refuse(
            f"the '{_DESCRIPTIONS_KEY}' block is missing or not a mapping; the README defines every field.",
            where=f"{path} -> {_DESCRIPTIONS_KEY}",
            expected=f"one sentence per field under document_fields, e.g.\n{_DESCRIPTIONS_EXAMPLE}",
            recover=f"add a '{_DESCRIPTIONS_KEY}:' block naming each of the {len(known)} fields.",
        )
    for field in known:
        text = block.get(field)
        if not isinstance(text, str) or not text.strip():
            raise _refuse(
                f"extraction field {field} has no description.",
                where=f"{path} -> {_DESCRIPTIONS_KEY}.{field}",
                expected=f"a one-sentence string, e.g.\n{_DESCRIPTIONS_EXAMPLE}",
                recover=f"add '{field}: \"<one sentence>\"' under {_DESCRIPTIONS_KEY}.",
            )
    for name in block:
        if name not in known:
            raise _refuse(
                f"'{name}' under {_DESCRIPTIONS_KEY} names no extraction field.",
                where=f"{path} -> {_DESCRIPTIONS_KEY}.{name}",
                expected=f"a field named under document_fields: {known}.",
                recover=f"remove '{name}' from {_DESCRIPTIONS_KEY}, or correct its spelling.",
            )
    return {field: str(block[field]).strip() for field in known}


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True, check=False)


def find_previous(
    output_root: Path, *, prefix: str, suffix: str, current_dir: str, head: str, repo_root: Path
) -> PreviousSnapshot | None:
    """Find the latest earlier snapshot with a provenance record under the root, and relate its commit.

    Args:
        output_root: The root snapshots are written under.
        prefix: `eval_set.snapshot_dir_prefix`.
        suffix: `eval_set.provenance_suffix`; a record lives in `<snapshot dir>.<suffix>/`.
        current_dir: This snapshot's directory name; only names sorting before it count as earlier.
        head: This snapshot's commit.
        repo_root: The repository, asked whether the earlier commit is an ancestor.

    Returns:
        The earlier snapshot, or None when no earlier snapshot has a provenance record.

    Raises:
        ReadmeError: An earlier record is not readable JSON with a `generator_commit`.
    """
    pattern = re.compile(rf"({re.escape(prefix)}_\d{{8}})\.{re.escape(suffix)}")
    earlier = sorted(
        (match.group(1), path)
        for path in output_root.glob(f"{prefix}_*.{suffix}/{PROVENANCE_NAME}")
        if (match := pattern.fullmatch(path.parent.name)) and match.group(1) < current_dir
    )
    if not earlier:
        return None
    directory, record_path = earlier[-1]
    try:
        record = json.loads(record_path.read_text())
        commit = str(record["generator_commit"])
        snapshot_date = str(record["snapshot_date"])
    except (json.JSONDecodeError, KeyError, TypeError) as err:
        raise _refuse(
            f"the earlier snapshot's provenance record is unreadable ({err}).",
            where=str(record_path),
            expected='a JSON object with "generator_commit" and "snapshot_date".',
            recover="restore that record from a backup, or move its directory out of the output root.",
        ) from err
    if _git(repo_root, "cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
        return PreviousSnapshot(directory, snapshot_date, commit, UNKNOWN)
    if _git(repo_root, "merge-base", "--is-ancestor", commit, head).returncode != 0:
        return PreviousSnapshot(directory, snapshot_date, commit, DIVERGED)
    log = _git(repo_root, "log", "--reverse", "--format=%s", f"{commit}..{head}")
    subjects = tuple(line for line in log.stdout.splitlines() if line.strip())
    return PreviousSnapshot(directory, snapshot_date, commit, ANCESTOR, subjects)


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def _fill(template: str, values: dict[str, str]) -> str:
    """Fill the template; a placeholder left unfilled raises rather than shipping as `$name`."""
    try:
        return string.Template(template).substitute(values)
    except (KeyError, ValueError) as err:
        raise _refuse(
            f"the README template has a placeholder with no value: {err}.",
            where=str(TEMPLATE_PATH),
            expected="every $name in the template filled from the manifest; a literal dollar written $$.",
            recover="add the value in generators/snapshot/readme.py, or escape the dollar as $$.",
        ) from err


def render_readme(manifest: dict, *, schema: ExtractionSchema) -> str:
    """Generate the README from the manifest (without its `files` map).

    Args:
        manifest: The manifest description (`describe_snapshot`), or the full manifest.
        schema: The extraction schema, for the field list and its descriptions.

    Returns:
        The README text.

    Raises:
        ReadmeError: A field description is missing or stray, or a template placeholder is unfilled.
    """
    descriptions = load_field_descriptions(schema)
    halves = manifest["halves"]
    counts = manifest["counts"]
    types_of = {
        field: [t for t, fields in schema.document_fields.items() if field in fields]
        for field in descriptions
    }
    field_rows = [[f"`{field}`", ", ".join(types_of[field]), text] for field, text in descriptions.items()]
    count_rows = [
        [
            f"`{halves[half]}/`",
            str(counts[half]["total"]),
            ", ".join(f"{n} {t}" for t, n in counts[half]["by_document_type"].items()),
        ]
        for half in HALVES
    ]
    style_rows = [
        [doc_type, f"`{field}`", *(str(styles.get(style, 0)) for style in LABEL_STYLES)]
        for doc_type, per_field in manifest["label_style_counts"].items()
        for field, styles in per_field.items()
    ]
    tiers = manifest["degradation_tiers"]
    values = {
        "snapshot_dir": manifest["snapshot_dir"],
        "snapshot_date": manifest["snapshot_date"],
        "plain_dir": halves["plain"],
        "distractor_dir": halves["distractor"],
        "labelled_varied_dir": halves["labelled_varied"],
        "degraded_dir": halves["degraded"],
        "geometry_dir": manifest["geometry_dir"],
        "csv_name": manifest["ground_truth_files"][0],
        "jsonl_name": manifest["ground_truth_files"][1],
        "image_total": str(sum(counts[half]["total"] for half in halves)),
        "clean_total": str(counts["plain"]["total"]),
        "count_table": _table(["Directory", "Images", "By document type"], count_rows),
        "tier_list": ", ".join(f"`_{t['suffix']}` ({t['name']})" for t in tiers),
        "light_suffix": tiers[0]["suffix"],
        "tier_count": str(len(tiers)),
        "field_count": str(len(descriptions)),
        "field_table": _table(["Field", "Document types", "Definition"], field_rows),
        "style_table": _table(["Document type", "Field", *LABEL_STYLES], style_rows),
    }
    return _fill(TEMPLATE_PATH.read_text(), values)
