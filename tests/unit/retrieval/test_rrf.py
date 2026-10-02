import pytest

from constitutional_evidence_rag.retrieval.rrf import reciprocal_rank_fusion


def test_scores_follow_the_rrf_formula():
    fused = dict(reciprocal_rank_fusion({"bm25": [10, 20, 30], "dense": [30, 40]}, k=60))

    assert fused[30] == pytest.approx(1 / 63 + 1 / 61)
    assert fused[10] == pytest.approx(1 / 61)
    assert fused[40] == pytest.approx(1 / 62)
    assert len(fused) == 4  # de-duplicated: 30 appears once


def test_items_in_both_lists_rise_to_the_top():
    assert reciprocal_rank_fusion({"bm25": [1, 2, 3], "dense": [3, 4, 5]}, k=60)[0][0] == 3


def test_weights_scale_contributions_and_zero_weight_drops_a_list():
    weighted = dict(reciprocal_rank_fusion({"bm25": [1], "dense": [2]}, k=60, weights={"bm25": 2.0, "dense": 1.0}))
    assert weighted[1] == pytest.approx(2 / 61) and weighted[2] == pytest.approx(1 / 61)

    only_bm25 = reciprocal_rank_fusion({"bm25": [5, 6], "dense": [7, 5]}, k=60, weights={"dense": 0.0})
    assert [row for row, _ in only_bm25] == [5, 6]


def test_ties_are_deterministic():
    # rows 7 and 3 both have one rank-1 hit: equal scores, equal best rank -> lower row first
    assert [r for r, _ in reciprocal_rank_fusion({"a": [7], "b": [3]}, k=60)] == [3, 7]
    first = reciprocal_rank_fusion({"b": [4, 2], "a": [2, 9]}, k=10)
    assert first == reciprocal_rank_fusion({"a": [2, 9], "b": [4, 2]}, k=10)  # list order does not matter


def test_invalid_k():
    with pytest.raises(ValueError):
        reciprocal_rank_fusion({"a": [1]}, k=0)
