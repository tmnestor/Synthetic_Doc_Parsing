"""The one spelling of the distractor key prefix.

A leaf module: it imports nothing, so the geometry recorder, the layout engine, the layout schema, the
provider registry and the catalogue loader can all share it without an import cycle.
"""

# The prefix every distractor key carries, e.g. DX_STORE_NUMBER; see config/distractors.yml.
DX_PREFIX = "DX_"
