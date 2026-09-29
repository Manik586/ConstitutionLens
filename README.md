# Constitutional Evidence RAG

Evidence-grounded RAG system for Indian Constitutional Law. See `docs/SRS.md` for requirements and `docs/ARCHITECTURE.md` for the system architecture.

Status: Phase 1 (corpus + ingestion + parsing) in progress — see `docs/ARCHITECTURE.md` §6.

## Setup

```bash
pip install -e ".[dev]"
cp .env.example .env
pytest
```

## Ingesting PDFs (Phase 1)

1. Place PDFs under `data/raw/constitution/` and `data/raw/judgments/` (only once corpus licensing is confirmed — `docs/DECISIONS.md` D7).
2. Copy `data/raw/manifest.example.yaml` to `data/raw/manifest.yaml` and list each PDF with its source type, title, and canonical source URL.
3. Run:

```bash
python scripts/ingest_corpus.py
# or: docker compose run --rm app python scripts/ingest_corpus.py
```

Output goes to `data/processed/`: `metadata.jsonl` (one row per document version) and `documents.jsonl` (page-by-page text, each page with its provenance). Re-running is safe: unchanged documents are skipped, changed ones get a new version under the same document ID.
