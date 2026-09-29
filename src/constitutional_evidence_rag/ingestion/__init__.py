"""Phase 1 ingestion: register sources, parse PDFs page-by-page, persist
versioned JSONL output (SRS Section 21.1, FR-01/FR-02; docs/DECISIONS.md
D3, D4, D8-D12).

Modules:
    metadata   - manifest loading, stable document IDs, version registry
    pdf_parser - page-by-page PDF text extraction (the only PyMuPDF user)
    pipeline   - ties the two together and writes data/processed/*.jsonl

As with `common/`, nothing is re-exported here: import from the
specific submodule.
"""
