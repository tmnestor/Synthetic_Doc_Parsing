"""The overlay layer: marks drawn over a finished distractor page, verified against its recorded ink.

`engine.apply_overlay` draws a layout's `overlay:` blocks; `verify` holds the clearance, inset and
contrast checks; `registry` holds the primitive registry and the types a drawer works with. This package
module imports none of them, so the layout DSL's schema can import `registry` alone without a cycle.
"""
