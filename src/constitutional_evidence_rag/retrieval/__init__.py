"""Phase 3: indexing and retrieval (SRS FR-05 - FR-07, FR-05a; docs/DECISIONS.md D17 - D19).

Modules:
    store  - per-corpus index locations, chunk-order mapping, metadata, errors,
             query validation (shared plumbing for both indexes)
    bm25   - legal-aware tokenizer and BM25 index (FR-06)
    dense  - embedding model wrapper, cached embeddings, FAISS index (FR-05)
    rrf    - reciprocal rank fusion (FR-07)
    hybrid - Retriever: BM25 / dense / hybrid retrieval with full provenance

Retrieval and fusion never take authority level, section type, or any other
chunk metadata as a scoring input (SRS FR-SA-05, ARCHITECTURE Invariant 1):
only chunk text is indexed. Import from the specific submodule.
"""
