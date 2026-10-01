"""Phase 2: legal-aware chunking with provenance (SRS FR-03; docs/DECISIONS.md D16).

Modules:
    structure     - page-furniture removal and structure detection (Constitution
                    Parts/Chapters/Articles/clauses; judgment front matter, opinions,
                    explicit headings), with an unstructured fallback
    legal_chunker - packs detected sections into size-bounded chunks and writes
                    data/processed/<corpus_id>/chunks.jsonl

Import from the specific submodule; nothing is re-exported here.
"""
