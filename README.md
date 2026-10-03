# Constitutional Evidence RAG

Evidence-grounded RAG system for Indian Constitutional Law. See `docs/SRS.md` (v4.1) and `docs/SRS_v4.2_AMENDMENT.md` for requirements, and `docs/ARCHITECTURE.md` for the system architecture.

**Scope:** V1 answers questions over one curated corpus — the Constitution of India and ~30 selected Supreme Court constitutional judgments. Uploading your own documents and querying personal collections is planned for V2; it is not available in V1.

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

Output goes to `data/processed/constitutional-core/` (the curated corpus): `metadata.jsonl` (one row per document version) and `documents.jsonl` (page-by-page text, each page with its provenance). Re-running is safe: unchanged documents are skipped, changed ones get a new version under the same document ID.


## Chunking (Phase 2)

After ingestion:

```bash
python scripts/chunk_corpus.py
```

This writes `data/processed/constitutional-core/chunks.jsonl`: legal-aware chunks (articles, judgment opinions and headed sections) with page ranges and full provenance. Chunk sizes are set in `configs/v1.yaml` under `chunking`.

## Retrieval (Phase 3)

After chunking:

```bash
python scripts/build_indexes.py            # BM25 + BGE embeddings + FAISS -> indexes/constitutional-core/
python scripts/query.py "What does Article 21 provide?"               # hybrid (BM25 + dense, RRF)
python scripts/query.py "Article 19(2)" --mode bm25 --top-k 5        # modes: hybrid | bm25 | dense
```

The first dense build downloads the embedding model (`embedding.model_name` in `configs/v1.yaml`); later builds re-embed only changed chunks. `--only bm25` builds the sparse index without the model. Model, top-n values, RRF constant and weights are all in `configs/v1.yaml`.

## Grounded answers (Phase 4)

```bash
python scripts/query.py "What is Article 21?" --answer
python scripts/query.py "How has the Supreme Court interpreted Article 21?" --answer --show-evidence
python scripts/query.py "What did Kesavananda Bharati establish?" --answer --json
```

Answers quote retrieved evidence verbatim, keep constitutional text and judicial passages in separate sections, number every citation from the source metadata, and answer "insufficient evidence" rather than guessing. Thresholds, evidence counts and boosts live under `evidence` and `generation` in `configs/v1.yaml`. Without `--answer`, `query.py` behaves exactly as before.
