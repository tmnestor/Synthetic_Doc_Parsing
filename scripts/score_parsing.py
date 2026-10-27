#!/usr/bin/env python3
"""Score every collected parser run against a snapshot's parsing ground truth, and write the leaderboard.

    python scripts/score_parsing.py --snapshot ../evaluation_data/snapshot_20261014 \\
        --snapshot-for degraded=../evaluation_data/snapshot_20261013

For each tool in `tools` of `config/parsing_config.yml` and each half with predictions under
`<eval_set.output_root>/predictions/<tool>/<half>/`, the pinned evaluator (`run_end2end`, unmodified)
scores them against `<snapshot>/<parsing_dir>/<half prefix>.json`, working in
`<eval_set.output_root>/scores/<tool>/<half>/`. The leaderboard is written to
`<eval_set.output_root>/leaderboard_<YYYYMMDD>.md` and `.csv`: rows per tool, columns per half, for text
edit distance, table TEDS (with structure-only TEDS), table edit distance and reading-order edit distance,
then the same broken down by document type, degradation tier and layout key (the page attributes the
evaluator slices by, read from its metric result), and each run's back-end, version and device, read from
the `run.json` the runner left beside the raw output. A run whose `run.json` names another tool or half is
refused, and so is one made on another snapshot unless every image of its half is byte-identical in the
two (the same SHA-256 in each MANIFEST.json), as after a re-export that changed only the ground truth.
`--snapshot-for <half>=<snapshot dir>` (repeatable) scores one half against another snapshot, e.g. the
one its runs were made on when a re-export changed that half's images; the leaderboard opens by naming
each half's snapshot and whether an overridden half's ground truth is byte-identical in the two. The leaderboard ends with a "Tools and licences" block crediting
each scored project, its URL and the licences of its code and weights, read from `credits` in
`config/parsing_config.yml`.

The typeset sets are scored the same way, each run under `predictions/<tool>/<set>_<input>/` (e.g.
`typeset_plain_pdf`, the runner's name for it) against `<snapshot>/<parsing_dir>/<set prefix>.json`, its
`run.json` recording the set as its half and the input it was given; a tool is scored only on the inputs
its `inputs` lists, so `text_layer` fills PDF columns only, and predictions for any other input are
refused. The leaderboard gains a column per typeset run scored, and a "Native PDFs" section: each tool's
gain over its stage one image score for the same half (`IMAGE_BASELINE`; `pdf_gain` gives the sign, so
a positive gain always means the PDF helped), why each tool without a gain has none (Marker takes images
only), and the control: every typeset PNG run beside the same tool's stage one image score, each metric
`native_pdf_control.metrics` lists checked against `native_pdf_control.max_difference`, a warning above
the gain table when one exceeds it.

Tables lead with TEDS. Table edit distance is reported beside it with its caveat: it compares html
strings, and the evaluator converts a markdown pipe table to html with attributes an html table does not
carry, so it compares only tools with the same output form (the design's section 5.3, recommendation 8).
Overall needs formula scores, which arrive in stage three, so it is shown as not applicable and never
replaced by an average of the other components.
"""

import argparse
import csv
import hashlib
import io
import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generators.parsing.config import (  # noqa: E402
    INPUT_KINDS,
    PARSING_CONFIG_PATH,
    ParsingConfig,
    ParsingConfigError,
    load_parsing_config,
)
from generators.snapshot.config import (  # noqa: E402
    HALVES,
    TYPESET_SETS,
    SnapshotConfig,
    load_snapshot_config,
)
from generators.snapshot.manifest import MANIFEST_NAME  # noqa: E402
from scripts.collect_predictions import PREDICTIONS_DIR  # noqa: E402
from scripts.omnidocbench_eval import (  # noqa: E402
    EvaluatorError,
    ensure_checkout,
    result_path,
    run_end2end,
)
from scripts.run_local_parser import RAW_DIR, RUN_RECORD, default_run_name  # noqa: E402

OVERALL = "n/a (no formulas in stage one)"
# Every column a run can fill, as (column, set, input): a stage-one half is read from its PNGs only and
# keeps its own name; a typeset set's run is named `<set>_<input>`, as the runner names it.
COLUMNS = (
    *((half, half, "png") for half in HALVES),
    *((default_run_name(name, kind), name, kind) for name in TYPESET_SETS for kind in INPUT_KINDS),
)
# The stage one half each typeset set's native-PDF gain is measured against: the same cases, typeset.
IMAGE_BASELINE = {"typeset_plain": "plain", "typeset_distractor": "distractor"}
# The metrics that are better higher (TEDS); every other metric is an edit distance, better lower.
HIGHER_IS_BETTER = frozenset({"table_teds", "table_teds_structure_only"})
SCORES_DIR = "scores"
# Each metric: its name in run_end2end's scores, its heading, and where the evaluator's metric result
# keeps its per-attribute values (element, metric under `page`). Tables lead with TEDS.
METRICS = (
    ("text_block_edit_dist", "Text edit distance", ("text_block", "Edit_dist")),
    ("table_teds", "Table TEDS", ("table", "TEDS")),
    ("table_teds_structure_only", "Table TEDS, structure only", ("table", "TEDS_structure_only")),
    ("table_edit_dist", "Table edit distance", ("table", "Edit_dist")),
    ("reading_order_edit_dist", "Reading-order edit distance", ("reading_order", "Edit_dist")),
)
# Reading order again, from a second run of the same checkout with the table matcher's first-table fault fixed
# in memory (scripts/omnidocbench_corrected.py, OmniDocBench issue #229). Reported beside, never instead of,
# the unmodified evaluator's score; its breakdown is kept under its own key.
CORRECTED = (
    "reading_order_edit_dist_corrected",
    "Reading-order edit distance, corrected",
    ("reading_order", "Edit_dist_corrected"),
)
REPORTED = (*METRICS, CORRECTED)
CORRECTED_DIR = "corrected_reading_order"
# Every score but reading order must be identical in the corrected run, or it is refused.
UNMOVED = tuple(name for name, _, _ in METRICS if name != "reading_order_edit_dist")
# The page attributes broken down, as the evaluator names them, with each one's section and column title.
BREAKDOWNS = (
    ("doc_type", "By document type", "Document type"),
    ("tier", "By degradation tier", "Tier"),
    ("layout_id", "By layout", "Layout key"),
)
_NOTES = {
    "table_edit_dist": "Table edit distance compares html strings. The evaluator turns a markdown pipe "
    'table into html with `colspan="1" rowspan="1"` on every cell, which an html table does not carry, '
    "so a perfect markdown table can score 0.5 here while scoring TEDS 1: compare it only between "
    "tools with the same output form (html or markdown). TEDS is not affected.",
    "reading_order_edit_dist": "Reading order includes one known fault of the pinned evaluator, as upstream "
    "publishes it: once matched, a page's first predicted table drops out of the predicted order, one edit "
    "on every such page for every tool alike, even for a perfect prediction (OmniDocBench issue #229; the "
    "design's section 5.3). The corrected score below removes it.",
    "reading_order_edit_dist_corrected": "The same evaluator checkout, run again with that one fault fixed "
    "in memory (scripts/omnidocbench_corrected.py, the change OmniDocBench PR #230 proposes): a matched "
    "first table keeps its place in the predicted order. The checkout is not modified, and every other "
    "score of the corrected run is checked identical to the unmodified run's. Compare this one with "
    "other corrected scores, and the score above with upstream's published numbers.",
}
RECORD_KEYS = ("tool", "half", "snapshot", "backend", "tool_version", "device", "page_count", "seconds")
_RUN_FIELDS = (("backend", "Backend"), ("tool_version", "Version"), ("device", "Device"))


class ScoreError(RuntimeError):
    """The runs cannot be scored: nothing to score, or a run's record or ground truth is missing."""


def _diagnostic(title: str, *, what: str, where: str, expected: str, recover: str) -> ScoreError:
    """Build a four-part refusal, each part on a labelled line."""
    return ScoreError(
        f"{title}\n  What:     {what}\n  Where:    {where}\n  Expected: {expected}\n  Recover:  {recover}"
    )


def _run_record(path: Path) -> dict:
    try:
        record = json.loads(path.read_text())
        if not isinstance(record, dict):
            raise TypeError(f"the record is {type(record).__name__}, not an object")
        for key in RECORD_KEYS:
            record[key]  # noqa: B018  every field the leaderboard reports must be present
    except (OSError, ValueError, KeyError, TypeError) as err:
        raise _diagnostic(
            "A scored run has no readable record.",
            what=f"{path} is missing or incomplete ({err!r}); the leaderboard reports each run's device "
            "and version from it.",
            where=f"{path}",
            expected='the run.json scripts/run_local_parser.py writes, e.g. {"tool": "docling", '
            '"half": "plain", "snapshot": "/data/snapshot_20260101", "backend": "default", '
            '"tool_version": "2.0.0", "device": "mps", "page_count": 165, "seconds": 900.0}',
            recover="run the tool with scripts/run_local_parser.py and collect it again; a run made "
            "another way needs a run.json beside its raw output.",
        ) from err
    return record


def _ground_truth(snapshot: Path, name: str, *, snapshot_config: SnapshotConfig) -> Path:
    """A half's or typeset set's parsing file in `snapshot`: `<parsing_dir>/<its prefix>.json`."""
    prefixes = snapshot_config.half_prefixes if name in HALVES else snapshot_config.typeset_prefixes
    return snapshot / snapshot_config.parsing_dir / f"{prefixes[name]}.json"


def _image_digests(
    snapshot: Path, half: str, input_kind: str, *, snapshot_config: SnapshotConfig
) -> dict[str, str]:
    """File name -> SHA-256, for every page the half's (or set's) parsing file lists, from MANIFEST.json.

    The pages are the images the parsing file names, and for a PDF run also the PDF beside each, the file
    the tool was given.

    Raises:
        ScoreError: The parsing file or the manifest is missing or unreadable, or lists a page the
            manifest does not hash.
    """
    gt = _ground_truth(snapshot, half, snapshot_config=snapshot_config)
    manifest = snapshot / MANIFEST_NAME
    try:
        files = json.loads(manifest.read_text())["files"]
        images = [str(page["page_info"]["image_path"]) for page in json.loads(gt.read_text())]
        if input_kind == "pdf":
            images += [str(Path(image).with_suffix(".pdf")) for image in images]
        return {Path(image).name: str(files[image]) for image in images}
    except (OSError, ValueError, KeyError, TypeError) as err:
        raise _diagnostic(
            "A snapshot's images cannot be compared.",
            what=f"the {half} images of {snapshot} could not be read from {gt.name} and {MANIFEST_NAME} "
            f"({err!r}), so it is unknown whether they are the images the run parsed.",
            where=f"{gt}; {manifest} -> files",
            expected=f"both files as `eval-set` writes them: {gt.name} listing each page's "
            f"page_info.image_path, and {MANIFEST_NAME} hashing it under files, e.g. "
            '{"files": {"plain_20990101/CASE001_receipt.png": "<sha256>"}}',
            recover="keep the snapshot the run was made on beside the one scored, or pass --snapshot the "
            "snapshot the run was made on.",
        ) from err


def _check_run_matches(
    record: dict,
    path: Path,
    *,
    tool: str,
    half: str,
    input_kind: str,
    snapshot: Path,
    snapshot_config: SnapshotConfig,
) -> None:
    """Refuse predictions that a run.json says came from another tool, half (or set), input or snapshot.

    A typeset run must record its input; a stage-one record may leave it out (stage one recorded none, and
    a half is only ever run on its PNGs). A run recorded on another snapshot is accepted only when every
    page of the half is byte-identical in the two (the same file names with the same SHA-256 in each
    MANIFEST.json, the PDFs too for a PDF run), so its predictions read the very pages scored: a re-export
    that changed only the ground truth is scored without running the tool again.
    """
    problems = []
    if record["tool"] != tool:
        problems.append(f"tool {record['tool']!r}, not {tool!r}")
    if record["half"] != half:
        problems.append(f"half {record['half']!r}, not {half!r}")
    recorded_input = record.get("input", "png" if half in HALVES else None)
    if recorded_input != input_kind:
        problems.append(f"input {recorded_input!r}, not {input_kind!r}")
    if problems:
        raise _diagnostic(
            "A run's record does not match what is being scored.",
            what=f"{path} records {'; '.join(problems)}, so its predictions were left from another run.",
            where=f"{path}",
            expected=f"a run of {tool} on the {half} pages ({input_kind}) of {snapshot.name}, e.g. "
            f'{{"tool": "{tool}", "half": "{half}", "input": "{input_kind}", '
            f'"snapshot": "<dir>/{snapshot.name}"}}',
            recover=f"run {tool} on {snapshot.name}'s {half} pages with scripts/run_local_parser.py "
            f"--input {input_kind} and collect it again.",
        )
    recorded = Path(str(record["snapshot"]))
    if recorded.name == snapshot.name:
        return
    if not recorded.is_dir():
        recorded = snapshot.parent / recorded.name
    if not recorded.is_dir():
        raise _diagnostic(
            "A run's record names a snapshot that cannot be found.",
            what=f"{path} records snapshot {record['snapshot']!r}, not {str(snapshot)!r}, and neither it "
            f"nor {recorded} exists, so its images cannot be compared with {snapshot.name}'s.",
            where=f"{path} -> snapshot",
            expected=f"the run's snapshot on disk, or a run made on {snapshot.name}, e.g. "
            f'{{"tool": "{tool}", "half": "{half}", "snapshot": "<dir>/{snapshot.name}"}}',
            recover=f"restore {recorded.name} beside {snapshot.name}, pass --snapshot the snapshot the run "
            f"was made on, or run {tool} on {snapshot.name}'s {half} half and collect it again.",
        )
    made_on = _image_digests(recorded, half, input_kind, snapshot_config=snapshot_config)
    scored = _image_digests(snapshot, half, input_kind, snapshot_config=snapshot_config)
    changed = sorted(
        name for name in made_on.keys() | scored.keys() if made_on.get(name) != scored.get(name)
    )
    if changed:
        raise _diagnostic(
            "A run was made on images that differ from the snapshot scored.",
            what=f"{path} records snapshot {recorded.name}, not {snapshot.name}, and {len(changed)} of the "
            f"{half} images differ between the two (absent from one, or a different SHA-256 in "
            f"{MANIFEST_NAME}): {changed[:5]}; the predictions did not read those pages.",
            where=f"{path} -> snapshot; {recorded / MANIFEST_NAME} and {snapshot / MANIFEST_NAME} -> files",
            expected=f"every {half} image byte-identical in both snapshots, or a run made on "
            f"{snapshot.name}, e.g. "
            f'{{"tool": "{tool}", "half": "{half}", "snapshot": "<dir>/{snapshot.name}"}}',
            recover=f"pass --snapshot {recorded.name}, or run {tool} on {snapshot.name}'s {half} half with "
            "scripts/run_local_parser.py and collect it again.",
        )


def _missing_metric(source: Path, dotted: str, metric: str, err: Exception) -> ScoreError:
    """The refusal for a metric the evaluator's result does not hold where the pinned commit writes it."""
    return _diagnostic(
        "The evaluator's result lacks a metric the leaderboard reports.",
        what=f"{metric} is missing or not a number at {dotted} ({err!r}).",
        where=f"{source} -> {dotted}",
        expected=f"a number at {dotted}, as the pinned evaluator writes it, e.g. "
        '{"table": {"page": {"TEDS": {"doc_type: invoice": 0.5}}}}',
        recover="check the evaluator checkout is at evaluator.commit and that the run scored text, tables "
        "and reading order (its log lists each element), then score again.",
    )


def _headline(scores: dict, *, source: Path) -> dict[str, float]:
    """The headline scores the leaderboard reports, refusing four-part when one is missing."""
    headline: dict[str, float] = {}
    for name, _, (element, metric) in METRICS:
        try:
            headline[name] = float(scores[name])
        except (KeyError, TypeError, ValueError) as err:
            raise _missing_metric(source, f"{element}.all.{metric}", name, err) from err
    return headline


def _breakdown(source: Path) -> dict[tuple[str, str], dict[str, float]]:
    """(element, metric) -> {page attribute group: value}, from the evaluator's metric result."""
    try:
        result = json.loads(source.read_text())
    except (OSError, ValueError) as err:
        raise _missing_metric(source, "(file)", "the metric result", err) from err
    breakdown: dict[tuple[str, str], dict[str, float]] = {}
    for name, _, (element, metric) in METRICS:
        dotted = f"{element}.page.{metric}"
        try:
            values = result[element]["page"][metric]
            breakdown[(element, metric)] = {group: float(value) for group, value in values.items()}
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            raise _missing_metric(source, dotted, name, err) from err
    return breakdown


def _same(a: float, b: float) -> bool:
    return (math.isnan(a) and math.isnan(b)) or a == b


def _check_only_reading_order_moved(
    official: dict[str, float],
    fixed: dict[str, float],
    official_breakdown: dict[tuple[str, str], dict[str, float]],
    fixed_breakdown: dict[tuple[str, str], dict[str, float]],
    *,
    source: Path,
) -> None:
    """Refuse a corrected run in which any score but reading order differs from the unmodified run."""
    moved = [name for name in UNMOVED if not _same(official[name], fixed[name])]
    for name, _, key in METRICS:
        if name in UNMOVED and set(official_breakdown[key]) != set(fixed_breakdown[key]):
            moved.append(f"{name} (its page groups)")
        elif name in UNMOVED:
            moved += [
                f"{name} ({g})"
                for g in official_breakdown[key]
                if not _same(official_breakdown[key][g], fixed_breakdown[key][g])
            ]
    if moved:
        raise _diagnostic(
            "The corrected run moved a score other than reading order.",
            what=f"{moved[:5]} differ from the unmodified run, but the fix only restores a matched table's "
            "position in the predicted order.",
            where=f"{source}",
            expected="text and table scores identical in both runs, reading order alone different",
            recover="check scripts/omnidocbench_corrected.py against the evaluator checkout at "
            "evaluator.commit; until they agree, report only the unmodified scores.",
        )


def _number(value: float | None) -> str:
    if value is None:
        return ""
    return "n/a" if math.isnan(value) else f"{value:.3f}"


def _raw(value: float | None) -> str:
    if value is None:
        return ""
    return "n/a" if math.isnan(value) else repr(value)


def score(
    snapshot: Path,
    *,
    output_root: Path,
    config: ParsingConfig,
    snapshot_config: SnapshotConfig,
    checkout: Path,
    today: date,
    snapshot_for: dict[str, Path] | None = None,
) -> tuple[Path, Path]:
    """Score every collected run and write the leaderboard.

    Args:
        snapshot: The snapshot directory holding the parsing ground truth.
        output_root: The directory holding `predictions/` and `raw_predictions/`; scores and the
            leaderboard are written there too.
        config: The validated parsing configuration, listing the tools.
        snapshot_config: The snapshot configuration, naming the parsing directory and half prefixes.
        checkout: The evaluator checkout.
        today: The date the leaderboard is named and headed with.
        snapshot_for: Half or typeset set -> the snapshot it is scored against instead of `snapshot` (the
            `--snapshot-for` overrides, as `parse_snapshot_for` returns them); None overrides nothing.

    Returns:
        The leaderboard's markdown and csv paths.

    Raises:
        ScoreError: No configured tool has predictions, a tool has predictions for an input it does not
            take, a scored run lacks its record or its set's ground truth, or the evaluator's result
            lacks a reported metric.
        EvaluatorError: The evaluator failed.
    """
    scored_on = {name: (snapshot_for or {}).get(name, snapshot) for name in (*HALVES, *TYPESET_SETS)}
    runs: dict[tuple[str, str], tuple[dict, dict[str, float], dict]] = {}
    for index, tool in enumerate(config.tools):
        for column, name, kind in COLUMNS:
            pred = output_root / PREDICTIONS_DIR / tool.name / column
            if not pred.is_dir():
                continue
            if kind not in tool.inputs:
                raise _diagnostic(
                    "A tool has predictions for an input it is not scored on.",
                    what=f"{pred} holds {tool.name}'s predictions from {kind} pages, but {tool.name} takes "
                    f"only {list(tool.inputs)}, so they are a stray run and not a score.",
                    where=f"{pred}; {PARSING_CONFIG_PATH} -> tools[{index}].inputs",
                    expected=f"predictions only for the inputs a tool takes, e.g. predictions/{tool.name}/"
                    f"{default_run_name(TYPESET_SETS[0], tool.inputs[0])}/",
                    recover=f"move {pred} out of {output_root / PREDICTIONS_DIR}, or add {kind} to "
                    f"tools[{index}].inputs if {tool.name} is to be scored on it.",
                )
            record_path = output_root / RAW_DIR / tool.name / column / RUN_RECORD
            record = _run_record(record_path)
            _check_run_matches(
                record,
                record_path,
                tool=tool.name,
                half=name,
                input_kind=kind,
                snapshot=scored_on[name],
                snapshot_config=snapshot_config,
            )
            gt = _ground_truth(scored_on[name], name, snapshot_config=snapshot_config)
            if not gt.is_file():
                raise _diagnostic(
                    "A scored half has no ground truth in the snapshot.",
                    what=f"{tool.name} has predictions for {column}, but {gt} does not exist.",
                    where=f"{gt}",
                    expected=f"the snapshot's parsing file for {name}, {gt.name}, as `eval-set` writes it.",
                    recover="pass --snapshot (or --snapshot-for) the snapshot the predictions were made "
                    "from.",
                )
            work = output_root / SCORES_DIR / tool.name / column
            source = result_path(pred, work_dir=work)
            headline = _headline(run_end2end(gt, pred, checkout=checkout, work_dir=work), source=source)
            breakdown = _breakdown(source)
            fixed_work = work / CORRECTED_DIR
            fixed_source = result_path(pred, work_dir=fixed_work)
            fixed = _headline(
                run_end2end(gt, pred, checkout=checkout, work_dir=fixed_work, corrected=True),
                source=fixed_source,
            )
            fixed_breakdown = _breakdown(fixed_source)
            _check_only_reading_order_moved(
                headline, fixed, breakdown, fixed_breakdown, source=fixed_source
            )
            headline[CORRECTED[0]] = fixed["reading_order_edit_dist"]
            breakdown[CORRECTED[2]] = fixed_breakdown[("reading_order", "Edit_dist")]
            runs[(tool.name, column)] = (record, headline, breakdown)
    if not runs:
        raise _diagnostic(
            "There is nothing to score.",
            what=f"no configured tool {[tool.name for tool in config.tools]} has a predictions "
            f"directory for any half or typeset run {[column for column, _, _ in COLUMNS]}.",
            where=f"{output_root / PREDICTIONS_DIR}",
            expected="predictions/<tool>/<half, or set_input>/<image stem>.md, e.g. "
            "predictions/docling/plain/ or predictions/docling/typeset_plain_pdf/",
            recover="run scripts/run_local_parser.py and scripts/collect_predictions.py for a tool first.",
        )
    tools = list(dict.fromkeys(tool for tool, _ in runs))
    # Every stage-one half, then each typeset run some tool was scored on.
    scored = {column for _, column in runs}
    typeset_columns = [(c, name) for c, name, _ in COLUMNS if name in TYPESET_SETS and c in scored]
    columns = [*HALVES, *(c for c, _ in typeset_columns)]
    typeset = list(dict.fromkeys(name for _, name in typeset_columns))
    native = _native(runs, tools, config=config)
    rows = _rows(runs, tools, columns, native)
    stamp = today.strftime("%Y%m%d")
    markdown = output_root / f"leaderboard_{stamp}.md"
    table = output_root / f"leaderboard_{stamp}.csv"
    provenance = _provenance(snapshot, scored_on, typeset, snapshot_config=snapshot_config)
    markdown.write_text(
        _markdown(rows, runs, tools, columns, native, provenance=provenance, config=config, today=today)
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["section", "metric", "tool", "group", *columns])
    for (section, metric, name, group), cells in rows.items():
        writer.writerow([section, metric, name, group, *(cells.get(column, "") for column in columns)])
    table.write_text(buffer.getvalue())
    return markdown, table


def _provenance(
    snapshot: Path, scored_on: dict[str, Path], typeset: list[str], *, snapshot_config: SnapshotConfig
) -> list[str]:
    """The leaderboard's opening lines: the snapshot each half, and each typeset set scored, was scored
    against.

    For a half or set scored against another snapshot (`--snapshot-for`), they say whether its ground
    truth is identical in the two: compared by SHA-256 of each page list with every `page_info.image_path`
    cut to its file name, since each snapshot names its own dated image directory there and nothing else
    may differ.
    """
    against = ", ".join(f"{half} `{scored_on[half].name}`" for half in HALVES)
    lines = [f"Snapshot `{snapshot.name}`. Each half is scored against: {against}."]
    if typeset:
        sets = ", ".join(f"{name} `{scored_on[name].name}`" for name in typeset)
        lines[0] += f" The typeset sets are scored against: {sets}."
    for half in (*HALVES, *typeset):
        other = scored_on[half]
        if other == snapshot:
            continue
        theirs = _ground_truth(other, half, snapshot_config=snapshot_config)
        ours = _ground_truth(snapshot, half, snapshot_config=snapshot_config)
        name = ours.name
        if not ours.is_file():
            lines.append(f"`{snapshot.name}` has no {half} ground truth to compare with `{other.name}`'s.")
            continue
        digests = [hashlib.sha256(_comparable(path)).hexdigest() for path in (theirs, ours)]
        verdict = "identical in" if digests[0] == digests[1] else "DIFFERENT between"
        lines.append(
            f"The {half} half is scored against `{other.name}`, the snapshot its runs were made on; its "
            f"ground truth (`{snapshot_config.parsing_dir}/{name}`) is {verdict} the two snapshots, "
            "every page's image_path cut to its file name since each snapshot names its own dated image "
            f"directory there (SHA-256 {digests[0]} and {digests[1]})."
        )
    return lines


def _comparable(path: Path) -> bytes:
    """A parsing file's pages as canonical JSON, each `page_info.image_path` cut to its file name."""
    pages = json.loads(path.read_text())
    for page in pages:
        info = page["page_info"]
        info["image_path"] = Path(str(info["image_path"])).name
    return json.dumps(pages, sort_keys=True, separators=(",", ":")).encode()


def parse_snapshot_for(values: list[str]) -> dict[str, Path]:
    """Read the `--snapshot-for <half>=<snapshot dir>` overrides.

    Args:
        values: Each `--snapshot-for` value, as given; the half may also be a typeset set.

    Returns:
        Half or typeset set -> the resolved snapshot directory it is scored against.

    Raises:
        ScoreError: A value is not `<half>=<dir>`, names an unknown half or set or one already given, or
            names a directory that does not exist.
    """
    example = "--snapshot-for degraded=../evaluation_data/snapshot_20261013"
    names = (*HALVES, *TYPESET_SETS)
    overrides: dict[str, Path] = {}
    for value in values:
        half, sep, directory = value.partition("=")
        if not sep or not directory:
            problem = f"{value!r} is not <half>=<snapshot dir>"
        elif half not in names:
            problem = f"{value!r} names the half {half!r}, which is not one of {list(names)}"
        elif half in overrides:
            problem = f"{value!r} gives the {half} half a second snapshot"
        elif not Path(directory).is_dir():
            problem = f"{value!r} names {Path(directory).resolve()}, which is not a directory"
        else:
            overrides[half] = Path(directory).resolve()
            continue
        raise _diagnostic(
            "A --snapshot-for override is invalid.",
            what=f"{problem}.",
            where=f"the command line -> --snapshot-for {value}",
            expected=f"<half>=<snapshot dir>, the half one of {list(names)}, given once, the directory an "
            f"existing snapshot, e.g. {example}",
            recover="correct or drop the --snapshot-for value; every half or set not overridden is scored "
            "against --snapshot.",
        )
    return overrides


def pdf_gain(metric: str, *, image: float, pdf: float) -> float:
    """How much a tool's score improved when it read the native PDF instead of the image.

    Args:
        metric: The headline metric's name, e.g. `text_block_edit_dist` or `table_teds`.
        image: The tool's stage one image score.
        pdf: The tool's score on the native PDFs.

    Returns:
        Image minus PDF for an edit distance (better lower), PDF minus image for TEDS (better higher), so
        a positive gain always means the PDF helped; NaN when either score is NaN.
    """
    return pdf - image if metric in HIGHER_IS_BETTER else image - pdf


@dataclass(frozen=True)
class Native:
    """The native-PDF section's content, computed once for the markdown and the csv.

    Attributes:
        shown: Whether any typeset run was scored (the section is left out otherwise).
        gains: (tool, set, metric, stage one image score, PDF score, gain), per tool, set and metric.
        controls: (tool, set, metric, typeset PNG score, stage one image score, PNG minus image,
            verdict), per tool, set and metric of every typeset PNG run.
        failures: One clause per checked control metric beyond its threshold, for the warning.
        notes: Why each scored tool without a gain (or a control) has none.
    """

    shown: bool
    gains: list[tuple[str, str, str, float, float, float]]
    controls: list[tuple[str, str, str, float, float, float, str]]
    failures: list[str]
    notes: list[str]


def _native(runs: dict, tools: list[str], *, config: ParsingConfig) -> Native:
    """Each tool's native-PDF gain over its stage one image score, and the typeset PNG control."""
    control = config.native_pdf_control
    limit = f"{control.max_difference:g}"
    specs = {tool.name: tool for tool in config.tools}
    gains: list[tuple[str, str, str, float, float, float]] = []
    controls: list[tuple[str, str, str, float, float, float, str]] = []
    failures: list[str] = []
    notes: list[str] = []
    for tool in tools:
        inputs = specs[tool].inputs
        listed = f"`inputs: [{', '.join(inputs)}]`"
        if "pdf" not in inputs:
            notes.append(
                f"`{tool}` takes images only ({listed}) and was not run on the native PDFs; its stage one "
                "image scores stand in the tables above."
            )
        elif "png" not in inputs:
            notes.append(
                f"`{tool}` takes PDFs only ({listed}): it has no stage one image score, so it appears in "
                "the PDF columns only and shows no gain."
            )
        elif not any((tool, default_run_name(name, "pdf")) in runs for name in TYPESET_SETS):
            notes.append(f"`{tool}` has no PDF run scored, so it shows no gain.")
        for name in TYPESET_SETS:
            half = IMAGE_BASELINE[name]
            for kind in ("pdf", "png"):
                run = (tool, default_run_name(name, kind))
                if run not in runs or "png" not in inputs:
                    continue
                if (tool, half) not in runs:
                    what = "PDF run" if kind == "pdf" else "typeset PNG run"
                    shows = "shows no gain" if kind == "pdf" else "gives no control"
                    notes.append(
                        f"`{tool}` has a {what} on {name} but no stage one {half} run scored, so it "
                        f"{shows} there."
                    )
                    continue
                image, other = runs[(tool, half)][1], runs[run][1]
                for metric, title, _ in REPORTED:
                    if kind == "pdf":
                        gain = pdf_gain(metric, image=image[metric], pdf=other[metric])
                        gains.append((tool, name, metric, image[metric], other[metric], gain))
                        continue
                    difference = other[metric] - image[metric]
                    if metric not in control.metrics:
                        verdict = "not checked"
                    elif math.isnan(image[metric]) and math.isnan(other[metric]):
                        verdict = "n/a in both"
                    elif math.isnan(difference):
                        verdict = f"BEYOND {limit}"
                        failures.append(f"{tool} on {name}: {title} is n/a in only one of the two")
                    elif abs(difference) <= control.max_difference or math.isclose(
                        abs(difference), control.max_difference
                    ):
                        verdict = f"within {limit}"
                    else:
                        verdict = f"BEYOND {limit}"
                        failures.append(
                            f"{tool} on {name}: {title} differs from its stage one image score by "
                            f"{abs(difference):.3f}, more than {limit}"
                        )
                    controls.append((tool, name, metric, other[metric], image[metric], difference, verdict))
    shown = any(column not in HALVES for _, column in runs)
    return Native(shown=shown, gains=gains, controls=controls, failures=failures, notes=notes)


Rows = dict[tuple[str, str, str, str], dict[str, str]]


def _rows(runs: dict, tools: list[str], columns: list[str], native: Native) -> Rows:
    """Every csv row: (section, metric, tool, group) -> column -> cell, in leaderboard order."""
    rows: Rows = {}

    def put(key: tuple[str, str, str, str], half: str, cell: str) -> None:
        rows.setdefault(key, {})[half] = cell

    for tool in tools:
        for half in columns:
            if (tool, half) in runs:
                put(("overall", "overall", tool, ""), half, OVERALL)
    for tool in tools:
        for half in columns:
            if (tool, half) not in runs:
                continue
            record = runs[(tool, half)][0]
            for field, _ in _RUN_FIELDS:
                put(("run", field, tool, ""), half, str(record[field]))
            put(("run", "pages", tool, ""), half, str(record["page_count"]))
            put(
                ("run", "seconds_per_page", tool, ""),
                half,
                f"{record['seconds'] / record['page_count']:.1f}",
            )
    for name, _, _ in REPORTED:
        for tool in tools:
            for half in columns:
                if (tool, half) in runs:
                    put(("all", name, tool, ""), half, _raw(runs[(tool, half)][1][name]))
    for attribute, _, _ in BREAKDOWNS:
        prefix = f"{attribute}: "
        for name, _, source in REPORTED:
            for tool in tools:
                groups = sorted(
                    {
                        group.removeprefix(prefix)
                        for half in columns
                        if (tool, half) in runs
                        for group in runs[(tool, half)][2][source]
                        if group.startswith(prefix)
                    }
                )
                for group in groups:
                    for half in columns:
                        value = (
                            runs[(tool, half)][2][source].get(prefix + group)
                            if (tool, half) in runs
                            else None
                        )
                        if value is not None:
                            put((attribute, name, tool, group), half, _raw(value))
    for tool, name, metric, _, _, gain in native.gains:
        put(("native_pdf_gain", metric, tool, ""), default_run_name(name, "pdf"), _raw(gain))
    for tool, name, metric, _, _, difference, _ in native.controls:
        put(("typeset_png_control", metric, tool, ""), default_run_name(name, "png"), _raw(difference))
    return rows


def _table(header: list[str], body: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return lines


def _cells(rows: Rows, key: tuple[str, str, str, str], columns: list[str]) -> list[str]:
    """A row's cells for each column, three decimals, blank where the tool was not run."""
    cells = rows.get(key, {})
    return [_number(None if half not in cells else _parse(cells[half])) for half in columns]


def _signed(value: float) -> str:
    return "n/a" if math.isnan(value) else f"{value:+.3f}"


def _native_section(native: Native, *, config: ParsingConfig) -> list[str]:
    """The "Native PDFs" section: any control warning, the notes, the control and the gain tables."""
    control = config.native_pdf_control
    limit = f"{control.max_difference:g}"
    titles = {name: title for name, title, _ in REPORTED}
    checked = ", ".join(titles[name] for name in control.metrics)
    where = f"`native_pdf_control` in config/{PARSING_CONFIG_PATH.name}"
    lines = ["", "## Native PDFs", ""]
    if native.failures:
        lines += [
            "**Warning: the control exceeds its threshold.** "
            + "; ".join(native.failures)
            + ". The typeset render alone moves the score, so the gains below mix the change of render "
            f"with the change of input ({where} sets the threshold).",
            "",
        ]
    lines += [
        "Each tool's native-PDF gain is measured against its stage one image score for the same half: "
        "typeset_plain against the plain half, typeset_distractor against the distractor half. The "
        "typeset pages are rendered afresh from the same cases, so this is not a controlled same-render "
        "comparison; the control below checks how far the render alone moves the score. The gain is the "
        "image score minus the PDF score for an edit distance and the PDF score minus the image score for "
        "TEDS, so a positive gain always means the PDF helped.",
        "",
        *(f"- {note}" for note in native.notes),
    ]
    if native.controls:
        lines += ["", "### Control: the typeset PNG against the stage one image", ""]
        lines += _table(
            ["Tool", "Set", "Metric", "Typeset PNG", "Stage one image score", "Difference", "Checked"],
            [
                [tool, name, titles[metric], _number(png), _number(image), _signed(diff), verdict]
                for tool, name, metric, png, image, diff, verdict in native.controls
            ],
        )
        lines.append("")
        if native.failures:
            lines.append("The control exceeds its threshold: see the warning above.")
        else:
            lines.append(
                f"Every checked metric ({checked}) of every typeset PNG run is within {limit} absolute of "
                f"the tool's stage one image score ({where}), so the typeset render alone does not move "
                "the score beyond it."
            )
    else:
        lines += [
            "",
            "No typeset PNG run was scored, so no control separates the change of render from the change "
            "of input.",
        ]
    lines += ["", "### Native-PDF gain over the stage one image score", ""]
    lines += _table(
        ["Tool", "Set", "Metric", "Stage one image score", "PDF score", "Gain"],
        [
            [tool, name, titles[metric], _number(image), _number(pdf), _signed(gain)]
            for tool, name, metric, image, pdf, gain in native.gains
        ],
    )
    return lines


def _parse(cell: str) -> float:
    return math.nan if cell == "n/a" else float(cell)


def _credits(tools: list[str], config: ParsingConfig) -> list[str]:
    """The closing "Tools and licences" block: one row per project among the scored tools."""
    credited = {tool.name: tool.credits for tool in config.tools}
    projects = list(dict.fromkeys(credited[tool] for tool in tools))
    return [
        "",
        "## Tools and licences",
        "",
        "These scores were produced with the projects below; each is credited with its project page and "
        "the licences of its code and its model weights, as its authors state them.",
        "",
        *_table(
            ["Project", "Project page", "Code licence", "Weights licence"],
            [[c.project, c.url, c.code_licence, c.weights_licence] for c in projects],
        ),
    ]


def _markdown(
    rows: Rows,
    runs: dict,
    tools: list[str],
    halves: list[str],
    native: Native,
    *,
    provenance: list[str],
    config: ParsingConfig,
    today: date,
) -> str:
    lines = [
        "# Parsing leaderboard",
        "",
        *provenance,
        "",
        f"Scored {today.day} {today.strftime('%B %Y')} with OmniDocBench at commit "
        f"`{config.evaluator.commit}`, unmodified, with quick matching.",
        "",
        "Edit distances are normalised: 0 is a perfect reading and 1 the worst. TEDS runs from 0 to 1, 1 "
        "being a perfect table. Text, table and reading-order edit distances are means over a half's "
        "pages; TEDS is the mean over its tables. A blank cell is a half or set the tool was not run on; "
        "n/a is a score the evaluator could not compute, because nothing of that kind was matched.",
        "",
        "## Overall",
        "",
        *_table(
            ["Tool", *halves],
            [
                [tool, *(rows.get(("overall", "overall", tool, ""), {}).get(half, "") for half in halves)]
                for tool in tools
            ],
        ),
        "",
        "Overall is the mean of the text, table and formula scores. It needs formula scores (CDM), which "
        "arrive in stage three; the components below are never averaged in its place.",
        "",
        "## Runs",
        "",
        *_table(
            ["Tool", "Half or set", *(title for _, title in _RUN_FIELDS), "Pages", "Seconds per page"],
            [
                [
                    tool,
                    half,
                    *(rows[("run", field, tool, "")][half] for field, _ in _RUN_FIELDS),
                    rows[("run", "pages", tool, "")][half],
                    rows[("run", "seconds_per_page", tool, "")][half],
                ]
                for tool in tools
                for half in halves
                if (tool, half) in runs
            ],
        ),
        "",
        "## Scores",
    ]
    for name, title, _ in REPORTED:
        lines += ["", f"### {title}", ""]
        lines += _table(
            ["Tool", *halves], [[tool, *_cells(rows, ("all", name, tool, ""), halves)] for tool in tools]
        )
        if name in _NOTES:
            lines += ["", _NOTES[name]]
    if native.shown:
        lines += _native_section(native, config=config)
    for attribute, section, column in BREAKDOWNS:
        lines += ["", f"## {section}"]
        for name, title, _ in REPORTED:
            body = [
                [tool, group, *_cells(rows, (attribute, name, tool, group), halves)]
                for (kind, metric, tool, group) in rows
                if kind == attribute and metric == name
            ]
            lines += ["", f"### {title}", ""]
            lines += _table(["Tool", column, *halves], body)
    lines += _credits(tools, config)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", type=Path, required=True, help="The snapshot directory")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "generation_config.yml")
    parser.add_argument(
        "--snapshot-for",
        action="append",
        default=[],
        metavar="HALF=DIR",
        help="Score one half or typeset set against another snapshot (repeatable), e.g. degraded=<dir>",
    )
    args = parser.parse_args(argv)
    try:
        snapshot_for = parse_snapshot_for(args.snapshot_for)
        snapshot_config = load_snapshot_config(args.config)
        config = load_parsing_config(PARSING_CONFIG_PATH)
        output_root = snapshot_config.output_root
        checkout = ensure_checkout(config, root=output_root / "tools")
        markdown, table = score(
            args.snapshot.resolve(),
            output_root=output_root,
            config=config,
            snapshot_config=snapshot_config,
            checkout=checkout,
            today=date.today(),
            snapshot_for=snapshot_for,
        )
    except (ScoreError, EvaluatorError, ParsingConfigError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Leaderboard: {markdown}\n             {table}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
