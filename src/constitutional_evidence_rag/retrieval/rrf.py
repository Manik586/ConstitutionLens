"""Reciprocal Rank Fusion (SRS FR-07; docs/DECISIONS.md D19).

    fused(d) = sum over lists L containing d of  weight_L / (k + rank_L(d))

with 1-based ranks and a fixed constant k (FR-07: "ranking is reproducible given
fixed RRF constant k"). Only ranks enter the fusion — never raw BM25 or cosine
scores (they are not on comparable scales) and never chunk metadata (FR-SA-05).
Ties are broken by the best rank achieved in any list, then by row number, so
the output order is fully deterministic. Output is de-duplicated by construction.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence


def reciprocal_rank_fusion(
    ranked_lists: Mapping[str, Sequence[int]],
    *,
    k: int,
    weights: Mapping[str, float] | None = None,
) -> list[tuple[int, float]]:
    """Fuse ranked lists of row ids into [(row, fused_score)], best first."""
    if k < 1:
        raise ValueError("RRF constant k must be >= 1")
    weights = weights or {}
    scores: dict[int, float] = {}
    best_rank: dict[int, int] = {}
    for name in sorted(ranked_lists):  # fixed summation order -> bit-reproducible scores
        weight = weights.get(name, 1.0)
        for rank, row in enumerate(ranked_lists[name], start=1):
            if weight > 0:
                scores[row] = scores.get(row, 0.0) + weight / (k + rank)
            best_rank[row] = min(best_rank.get(row, rank), rank)
    return sorted(((row, score) for row, score in scores.items()), key=lambda rs: (-rs[1], best_rank[rs[0]], rs[0]))
