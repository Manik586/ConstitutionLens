# ConstitutionLens — Constitutional Evidence RAG

Evidence-grounded RAG system for Indian Constitutional Law. See `docs/SRS.md` (v4.1) and `docs/SRS_v4.2_AMENDMENT.md` for requirements, and `docs/ARCHITECTURE.md` for the system architecture.

**Scope:** answers questions over the curated corpus (the Constitution of India and selected Supreme Court constitutional judgments) and over isolated corpora of your own PDFs (Phase 7), through the CLI, the HTTP API, or the ConstitutionLens web UI (Phase 8). For private, local use: there is no authentication.

Status: Phases 1–8 implemented (Phase 8: web UI) — see `docs/ARCHITECTURE.md` §6. Not production-ready for public deployment.

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

## Grounded LLM answers (Phase 5)

Phase 5 writes the answer with an LLM, from Phase 4's evidence only:

```
Final Evidence (E1..En) + Query -> Context Builder -> LLM -> Grounded Answer -> Citation Validation
```

1. Configure the provider in `configs/v1.yaml` (`llm:`) or with environment variables. Generation is **off by default** (`provider: none`).
   - Local model, nothing leaves your machine: `LLM_PROVIDER=openai_compatible`, `LLM_MODEL=qwen2.5:7b-instruct`, `LLM_BASE_URL=http://localhost:11434/v1` (Ollama), and `llm.require_api_key: false`.
   - Hosted model: also set `llm.allow_remote: true` (explicit consent to send corpus passages) and put the key in `.env` as `LLM_API_KEY=...`. Never commit it.
2. Run:

```bash
python scripts/query.py "What protections are provided for personal liberty?" --mode hybrid --top-k 5 --generate
python scripts/query.py "What does Article 21 provide?" --generate --show-evidence
python scripts/query.py "What does Article 21 provide?" --generate --json
```

Answers cite evidence as `[E1]`, `[E2]`. Every citation's source, pages and chunk come from the retrieved evidence, never from the model; statements with unknown citations or inexact quotations are removed and reported. If evidence is weak, or the model call fails, you get an explicit "insufficient evidence" or "generation failed" result with the evidence listed, never an answer from the model's own knowledge. Retrieval-only (`--mode bm25|dense|hybrid` without `--generate`) and the extractive `--answer` work exactly as before.

## Claim-level evidence-support check (Phase 6)

> Phase 6 verifies whether retrieved evidence provides textual/semantic support for generated claims. It does not determine legal correctness, precedent validity, or whether a case remains good law.

Phase 5 guarantees that citations exist and quotations are exact, but a real citation can still point at a passage that does not say what the sentence claims. Phase 6 splits every LLM answer into individual claims and checks each against the evidence it cites:

- **supported**: the cited evidence states it. The claim is kept.
- **insufficient**: support could not be confirmed. The claim is removed, with a note. This is the default when in doubt.
- **contradicted**: an explicit conflict with the cited evidence. The claim is removed and flagged.

If anything is removed, the answer is marked `partial`. If nothing survives, the result is `insufficient_evidence`; the model's own knowledge is never used to fill gaps. The check is deterministic, makes no extra LLM call, and runs automatically with `--generate`:

```bash
python scripts/query.py "What protections are provided for personal liberty?" --mode hybrid --top-k 5 --generate
python scripts/query.py "What does Article 21 provide?" --generate --show-validation   # per-claim status, score, reason
```

Configure it under `evidence_validation` in `configs/v1.yaml`: `enabled`, `method` (`lexical` default, or `semantic`, which reuses the embedding model), and the heuristic thresholds. See `docs/ARCHITECTURE.md` §3.6 and `docs/DECISIONS.md` D26 for the rules and limitations.

## Your own documents: user corpora (Phase 7)

A **corpus** is an isolated set of documents with its own uploads, chunks and indexes. `constitutional-core` (the Constitution and curated judgments) is the default and is read-only; everything you upload goes into a corpus of your own, and queries never mix corpora.

**Limits:** PDF files with a text layer only (scanned PDFs are not supported yet), at most 20 MB each, 20 documents, 200 MB and 500 pages per document. Change these under `uploads` in `configs/v1.yaml`.

### Command line

```bash
python scripts/ingest_corpus.py --corpus-id my-research --input-dir path/to/pdfs   # validate + store + ingest
python scripts/chunk_corpus.py  --corpus-id my-research
python scripts/build_indexes.py --corpus-id my-research                           # -> status READY
python scripts/query.py "What does the corpus say about reinforcement learning?" \
    --corpus-id my-research --mode hybrid --top-k 5 --generate
```

Without `--corpus-id`, every script works exactly as before, on `constitutional-core`. Corpus ids are 1–64 lowercase letters, digits and hyphens. Querying a corpus that is missing or not yet READY prints one clear line and exits with code 2; add `--verbose` for a traceback.

### HTTP API

```bash
pip install -e ".[api]"
uvicorn --factory constitutional_evidence_rag.api.app:create_app --port 8000
```

`POST /corpora` → `POST /corpora/{id}/documents` (multipart `files`) → `POST /corpora/{id}/build` → poll `GET /corpora/{id}` until `ready` → `POST /corpora/{id}/query` with `{"question": "...", "top_k": 5, "generate": true}`. The response is the usual Answer JSON, with citations like `[E1] file.pdf — Page 7` and the Phase 6 `validation`. `GET /health` reports liveness.

With Docker: `docker compose up --build` serves the API and the web UI on 127.0.0.1:8000. Data, indexes and the model cache live in the mounted `data/`, `indexes/` and `models/` folders.

**Important:**
- **The API has no authentication.** Run it only privately (on your machine, or a server only trusted people can reach), never as a public website. Anyone who can reach it can see and query every corpus.
- With `generate`, the retrieved passages from your documents are sent to the configured LLM provider.
- Answers from your documents are not legal advice and not an authoritative statement of law. The Phase 6 check is an evidence-support check, not legal verification.

## Retrieval evaluation

`data/eval/retrieval_v1.jsonl` holds 39 questions with known correct sources (case, provision, out-of-scope). Measure the search modes and, optionally, cross-encoder rerankers:

```bash
python scripts/evaluate_retrieval.py
python scripts/evaluate_retrieval.py --rerank BAAI/bge-reranker-base
```

It prints hit@1/5/10 and MRR per mode, hit@6 for the final evidence set, and whether rank-1 scores separate answerable from out-of-scope questions. A JSON report goes to `data/eval/reports/`. Results and the decision to keep reranking off are in `docs/DECISIONS.md` D31. Reranking can be switched on under `reranking:` in `configs/v1.yaml`, but it is only used by the evaluation script for now.

## ConstitutionLens web UI (Phase 8)

**ConstitutionLens — Evidence-Grounded Document Research.** A browser UI for everything in Phase 7: create a corpus, upload PDFs, build its index, ask questions, and read the answer next to its evidence and the Phase 6 evidence-support check. It is plain HTML, CSS and JavaScript (ES modules) served by the same FastAPI app. There is no build step, no Node server, and no CDN: fonts are the system's, and the animation library (GSAP 3.13, standard no-charge licence) is vendored under `web/static/vendor/`.

Screenshots of a real run (real server, BGE model, generated PDFs) are in `docs/screenshots/phase8/`. Files `10-` and `11-` use a mocked LLM answer, because no LLM was configured.

### Run it locally

```bash
pip install -e ".[api]"
uvicorn --factory constitutional_evidence_rag.api.app:create_app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/>. The UI is at `/` and its assets are under `/static/`. Every API route (`/health`, `/config`, `/corpora/...`) is unchanged.

### Using it

1. **New corpus** (sidebar): type a name. A corpus ID is suggested from it (lowercase letters, digits, hyphens) and can be edited. An ID is a label, not a password.
2. **Documents → Choose PDFs** (or drop PDFs on the drop zone). Each file is reported as `accepted`, `unchanged` (already in the corpus) or `rejected` with the server's reason. Limits come from the server (`GET /config`). With the defaults:
   - PDF only, 20 MB per file, 20 documents and 200 MB per corpus, 500 pages per document.
   - PDFs need selectable text. **Scanned PDFs without a text layer are rejected; there is no OCR.**
3. **Build index**. Status changes to *Processing* and the page checks it every 2 s, then every 5 s after the first 30 s, until it is *Ready* or *Failed*. A failed build shows the server's reason. Polling pauses while the tab is hidden.
4. **Ask a question** once the corpus is *Ready*. The result has three sections:
   - **Answer:** citation markers (`[E1]`, or `[1]` for quoted passages) jump to the evidence they cite.
   - **Evidence:** expandable cards with evidence ID, file, page, corpus and the excerpt.
   - **Evidence-support check:** the Phase 6 per-claim status (`supported` / `insufficient` / `contradicted`) and its reason. Claims kept in the answer are listed separately from claims that were removed or flagged.

   The check tests textual support in the retrieved documents only, not whether the law is correct or current.

The built-in `constitutional-core` corpus is query-only: it has no upload, build or delete controls.

**Behaviour to know:**
- **Adding PDFs to a *Ready* corpus** returns it to *Not indexed* (API status `uploaded`). Queries are refused until you build again, which re-indexes old and new documents together.
- **The document and passage counts** describe the *built index*. They change when a build finishes, not when a file is uploaded.
- **Answer mode:** without an LLM, answers are verbatim quotes from the evidence ("Quoted passages (no LLM)") and the evidence-support check does not run. The "Generate answer with the LLM" switch appears only when the server reports an LLM is configured (`generation_available` in `/config`).
- **Queries:** a query can be cancelled. The browser waits up to the server's LLM worst case plus 15 s.
- **Separate frontend:** to serve the UI from another origin, set `<meta name="api-base" content="https://api.example">` in `index.html`, and widen `connect-src` in the server's Content-Security-Policy (`api/app.py`) to match.

### LLM provider (optional)

Generation is off by default (`llm.provider: none`). Configure it with environment variables (in `.env`, never in the YAML):

```bash
LLM_PROVIDER=openai_compatible
LLM_MODEL=qwen2.5:7b-instruct
LLM_BASE_URL=http://localhost:11434/v1   # local server: also set llm.require_api_key: false in configs/v1.yaml
LLM_API_KEY=...                          # hosted provider only; also set llm.allow_remote: true in configs/v1.yaml
```

Restart the server. `GET /config` then reports `"generation_available": true` and the UI shows the switch.

### Storage

All state lives under the data root, so keep these folders to keep corpora between runs:

| Contents | Folder | Environment override |
|---|---|---|
| Uploaded PDFs | `data/uploads/` | `UPLOADS_DIR` |
| Processed text and chunks | `data/processed/` | `DATA_PROCESSED_DIR` |
| BM25 and FAISS indexes | `indexes/` | `INDEXES_DIR` |
| Embedding model cache | `models/` | `MODELS_DIR` |

Set `CER_DATA_ROOT` to put relative paths somewhere else.

### Docker

```bash
cp .env.example .env
docker compose up --build
```

Open <http://127.0.0.1:8000/>. Compose publishes the port on **127.0.0.1 only**. Change the address in `docker-compose.yml` deliberately, and only behind access controls. `HOST_PORT` picks the host port. The image runs uvicorn as a non-root user with a `/health` HEALTHCHECK, and `data/`, `indexes/` and `models/` are mounted volumes.

### Tests

```bash
pip install -e ".[api,dev,e2e]"
python -m playwright install chromium
pytest -q                 # everything, including the browser tests
pytest -q -m e2e          # only the browser tests (Playwright + Chromium)
pytest -q -m "not e2e"    # without a browser
```

- **Browser tests with a mocked API:** most of them intercept the API (`page.route`) to check exact requests, rendering, polling, errors, hostile content and keyboard use.
- **Real-server test:** `test_ui_real_server.py` drives the real API with generated PDFs, the deterministic test embedder and no LLM.
- **No network:** nothing downloads a model or calls an LLM.

### Limitations

- **No authentication.** Anyone who can reach the server can see, query, upload to and delete every user corpus. Corpus IDs are labels, not secrets. Use it **on your own machine or a private network of trusted people only**. A public deployment needs authentication, per-user corpus ownership, rate limiting and HTTPS first (Phase 9 and later).
- PDFs with extractable text only (no OCR).
- Indexing runs in a background thread inside the server process. Restarting the server during a build marks that corpus *Failed*, so build it again.
- With LLM generation on, retrieved passages from your documents are sent to the configured LLM provider (a remote one only with `llm.allow_remote: true`).
- ConstitutionLens is an evidence-grounded research tool. Its answers are not legal advice and not an authoritative statement of law, and the evidence-support check is not legal verification.
