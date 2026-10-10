"""Phase 8 web UI tests reuse the Phase 7 fixtures (generated PDFs, per-test storage roots,
the deterministic test embedder) so nothing downloads a model or calls an LLM."""
import sys
from pathlib import Path

CORPORA_TESTS = Path(__file__).resolve().parents[1] / "corpora"
if str(CORPORA_TESTS) not in sys.path:
    sys.path.insert(0, str(CORPORA_TESTS))
