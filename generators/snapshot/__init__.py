"""The dated snapshot export: four paired halves rendered from a recorded commit and working tree.

`generators.snapshot.export.export_snapshot` is what `python -m generators.pipeline eval-set` runs. The
configuration it reads is checked in `generators.snapshot.config`, and the repository state it records
(its commit, any working-tree changes and their patch) in `generators.snapshot.repo_state`. `manifest`
builds MANIFEST.json and SHA256SUMS, `readme` the README from `readme_template.md`, and `summary` holds
what one export produced.
"""
