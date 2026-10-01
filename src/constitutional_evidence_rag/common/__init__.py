"""Shared, phase-agnostic building blocks: configuration loading, core
document/metadata models, provenance representation, and logging.

See docs/ARCHITECTURE.md Section 3.1 for how these map to SRS sections.
No symbols are re-exported here deliberately — import from the specific
submodule (config, models, provenance, logging) rather than from the
package root, so it stays obvious where each piece actually lives.
"""
