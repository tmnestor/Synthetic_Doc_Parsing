"""Declarative layout DSL: YAML owns arrangement, Python owns drawing.

Importing any part of the DSL registers every field provider: `field_providers` registers the
ordinary ones, the import of `generators.distractors.providers` below the distractor ones and that of
`generators.labels.provider` the label provider. Those import only the leaf `provider_registry` from
this package, so no order of first import forms a cycle.
"""

import generators.distractors.providers  # noqa: F401  registers the distractor field providers
import generators.labels.provider  # noqa: F401  registers the label_styles field provider

from generators.layout_dsl.context import Region, RenderContext
from generators.layout_dsl.engine import render_blocks, render_body
from generators.layout_dsl.field_providers import apply_field_providers, field_provider
from generators.layout_dsl.providers import row_provider
from generators.layout_dsl.schema import validate_body, validate_layout

__all__ = [
    "Region",
    "RenderContext",
    "apply_field_providers",
    "field_provider",
    "render_blocks",
    "render_body",
    "row_provider",
    "validate_body",
    "validate_layout",
]
