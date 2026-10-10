"""Phase 7: user-provided corpora (D27 - D29).

    models    Corpus, CorpusStatus
    errors    user-safe error classes (no server paths in messages)
    registry  corpus.json registry, reserved curated corpus, status, locking, crash recovery
    uploads   per-file PDF validation and storage (data/uploads/<corpus_id>/)
    indexing  staged BM25 + dense build with all-or-nothing swap
    service   framework-independent service layer used by the CLI scripts and the API

Everything below reuses the corpus-generic Phase 1-6 code; nothing here re-implements
parsing, chunking, retrieval, generation or validation. Import from the submodule.
"""
