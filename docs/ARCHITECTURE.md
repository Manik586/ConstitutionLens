# ARCHITECTURE.md

This document describes the system architecture of Constitutional Evidence RAG: the canonical pipelines, the module map from architecture to actual repo paths, the architectural invariants that must hold in every phase, and the current implementation status. It is a living document — update it in the same commit that changes a module's shape, not after the fact.

For *why* a given implementation choice was made where the SRS leaves room for interpretation, see `DECISIONS.md`. This document describes *what the architecture is*; `DECISIONS.md` explains *why it is that way*.

---

## 1. Scope of this document

Covers both phases, since architecture is designed once and built incrementally:

- **V1 architecture** (SRS §15) — complete, independently evaluable retrieval + generation pipeline.
- **V2 architecture** (SRS §16) — extends only the tail of V1, from "Top Evidence" onward, plus user document collections as additional corpora (SRS v4.2 amendment).
- **Corpus boundary** (SRS v4.2 amendment, `docs/SRS_v4.2_AMENDMENT.md`) — V1 is one curated corpus; user-provided documents are V2 only.

Current build status is tracked in **Section 6**, separate from the target architecture described in Sections 2–5, so this document doesn't need rewriting each phase — only Section 6's status table changes.

---

## 2. Architectural Invariants

These hold across both phases and are the two things a code reviewer should check regardless of which module is being reviewed:

1. **Retrieval and reranking never receive authority-level or section-type input.** These signals (SRS §20) exist only downstream, for labeling and verification. Any code path that passes `source_type`/`section_type`/authority-level into a retrieval or reranking scoring function is a defect, not a feature — see D6-adjacent note in `DECISIONS.md` and SRS §20.2 / FR-SA-05.
2. **No autonomous multi-agent architecture, at either phase.** Verification (V2) is a deterministic pipeline of scoring stages, not an agentic loop. The only loop anywhere in the system is the bounded, optional self-correction retry (SRS §30, max 2 attempts).
3. **V1 has zero dependency on any V2 component.** The RAD (§7.3) confirms this at the requirement-dependency level: all of V2 depends on FR-12 (V1) existing; no V1 requirement depends on anything in V2. Architecturally, this means the `src/.../retrieval/`, `reranking/`, `generation/` (base), `citations/`, and `evidence/` modules must build, run, and be tested with no import of anything under `src/.../verification/`.
4. **Corpus isolation (SRS v4.2 amendment, D15).** Every document belongs to exactly one corpus (`corpus_id`), and storage (and, from Phase 3, indices) are per corpus. Ingesting into one corpus never modifies another corpus's files. V1 has exactly one corpus, `constitutional-core` (`CURATED_CORPUS_ID`); nothing in V1 lets an end user create or write to a corpus. `corpus_id` is a *scope*, never a ranking input, so Invariant 1 is unaffected.

---

## 3. V1 Architecture (SRS §15)

```
User Query
   ↓
Query Processing
   ↓
Dense Retrieval  ──┐
                    ├─→ RRF Fusion → Cross-Encoder Reranking → Top Evidence
BM25 Retrieval  ────┘
   ↓
LLM Generation (evidence-constrained)
   ↓
Basic Citation Linkage
   ↓
Basic Evidence Check (similarity / presence — no NLI)
   ↓
Answer + Citations + Basic Check Result
```

V1 is a complete, independently evaluable system (SRS §41.1: "at the end of Phase 8, V1 is considered a complete, independently successful project").

### 3.1 Module map (V1)

| Pipeline stage | Repo module | SRS reference |
|---|---|---|
| Shared config, logging, document metadata, provenance | `src/.../common/config.py`, `logging.py`, `models.py`, `provenance.py` | §24, NFR-03, NFR-06 |
| Page-level parsed content (`ParsedPage`, `ParsedDocument`) | `src/.../common/pages.py` | NFR-03 (D11) |
| Ingestion (manifest, stable ID, versioned registry) | `src/.../ingestion/metadata.py`, `pipeline.py`; CLI `scripts/ingest_corpus.py` | §21.1 (FR-01), NFR-07 (D3, D8–D10) |
| Parsing (page-by-page text; headings, article/case metadata pending — D8) | `src/.../ingestion/pdf_parser.py` | §21.1 (FR-02, partial) |
| Chunk model (`Chunk`, `ChunkingMethod`, `Division`) | `src/.../common/chunks.py` | §21.1 (FR-03), §24, NFR-03 (D16) |
| Structure detection (page furniture; Constitution Parts/Chapters/Articles/clauses; judgment front matter, opinions, verbatim headings) | `src/.../chunking/structure.py` | §21.1 (FR-03) (D16) |
| Chunking (section-bounded packing, overlap, provenance, IDs); CLI `scripts/chunk_corpus.py` | `src/.../chunking/legal_chunker.py` | §21.1 (FR-03, FR-04) (D16) |
| Index plumbing (per-corpus paths, chunk-order mapping, staleness, query validation) | `src/.../retrieval/store.py` | FR-05a, NFR-03 (D18) |
| Dense retrieval (embedder, cached embeddings, FAISS) | `src/.../retrieval/dense.py` | §21.2 (FR-05) (D17) |
| BM25 retrieval (legal-aware tokenizer) | `src/.../retrieval/bm25.py` | §21.2 (FR-06) (D18) |
| RRF fusion; `Retriever` (bm25 / dense / hybrid) | `src/.../retrieval/rrf.py`, `hybrid.py`; result model `common/retrieval.py`; CLIs `scripts/build_indexes.py`, `scripts/query.py` | §21.2 (FR-07) (D19) |
| Cross-encoder reranking | `src/.../reranking/cross_encoder.py` (**not built**; FR-08 remains open) | §21.2 (FR-08, FR-09) |
| Query understanding (provision / interpretation / case / general; article refs; case resolution) | `src/.../query/understanding.py` | Phase 4 as scoped in D20 |
| Evidence selection (lookups, locator boosts, intent-ordered groups, caps, confidence) | `src/.../reranking/evidence_selector.py`; model `common/evidence.py` | D20 |
| Generation (interface + deterministic extractive backend; LLM backend open) | `src/.../generation/answer.py`, `pipeline.py`; model `common/answer.py` | §21.3 (FR-10, FR-11, FR-14) (D21) |
| Citation linkage and grounding validation | `src/.../citations/citation_builder.py`, `src/.../generation/grounding.py` | §21.3 (FR-12), NFR-02 (D22) |
| Basic Evidence Check | `src/.../evidence/basic_check.py` | §21.3 (FR-13) |
| Non-advice framing (cross-cutting, not a pipeline stage) | Applied in `generation/prompts.py` and `app/` UI copy | §21.3 (FR-14), §21.4 (FR-17) |
| UI | `app/streamlit_app.py`, `app/components/` | §21.4 (FR-15–FR-17) |
| Retrieval evaluation | `src/.../evaluation/retrieval_metrics.py`, `runner.py`, `reports.py` | §34.1 |

FR-14 and FR-17 are **cross-cutting constraints**, not stages in the chain (RAD §7.1) — they gate every generated string and every UI screen, not one point in the pipeline. There is no single module that "implements" them; they're a review criterion applied to `generation/` output and all of `app/`.

### 3.2 Corpus model and the generic ingestion interface (V1, SRS v4.2 FR-01a)

V1 ingests exactly one curated corpus, but the ingestion core is written against *a* corpus, not *the* corpus, so V2 can add user collections without rewriting it:

```
V1 caller (only one):   ingest_corpus(manifest.yaml, raw_dir, processed_root)        # curated corpus
                              │  ManifestEntry = DocumentSpec + file
                              ▼
Generic core:           ingest_documents([(pdf_path, DocumentSpec), ...], corpus_id, processed_root, base_dir)
                        ingest_document(pdf_path, DocumentSpec, corpus_id, processed_root)   # ≈ ingest(document_path, corpus_id)
                              │
                              ▼
Storage:                data/processed/<corpus_id>/{documents.jsonl, metadata.jsonl}
```

| Element | Where | Generic because |
|---|---|---|
| `DocumentSpec` (source type, title, source URL, source date) | `ingestion/metadata.py` | Describes one document regardless of how it arrived; the manifest is just one source of specs |
| `corpus_id` on `DocumentMetadata`, `Provenance`, `ParsedDocument` | `common/models.py`, `provenance.py`, `pages.py` | Every record says which corpus it belongs to; required, with no default, so a caller can't silently write into the curated corpus |
| Corpus-scoped document IDs, `make_document_id(corpus_id, …)` | `ingestion/metadata.py` | The same source in two corpora never collides |
| Per-corpus storage, `corpus_dir(processed_root, corpus_id)` | `ingestion/pipeline.py` | Writing one corpus cannot rewrite another's files (Invariant 4) |
| CLI `scripts/ingest_corpus.py` | V1 only | Always the curated corpus; deliberately **no** `--corpus` option (creating corpora is the V2 feature) |

**Phase 3 contract (FR-05a):** dense and BM25 indices are built per `corpus_id`, and retrieval/reranking functions take the `corpus_id` to search. V1 always passes `CURATED_CORPUS_ID`. Implemented in Phase 3: see §3.3.

### 3.3 Retrieval and indexing (Phase 3 — D17, D18, D19)

```
data/processed/<corpus>/chunks.jsonl
        │  scripts/build_indexes.py
        ├──► indexes/<corpus>/bm25/    terms.npy, offsets.npy, doc_ids.npy, tfs.npy, doc_lengths.npy,
        │                               chunk_order.npy, meta.json
        └──► indexes/<corpus>/dense/   embeddings.npy (float32, L2-normalized), chunk_order.npy,
                                        index.faiss (IndexFlatIP), meta.json

query ──► Retriever.load(processed_root, indexes_root, corpus_id, settings)
            ├─ .bm25(q)    BM25 top-20  (FR-06)               ─┐
            ├─ .dense(q)   BGE + FAISS top-20  (FR-05)        ─┼─► .hybrid(q): weighted RRF (k=60) top-20  (FR-07)
            └─ list[RetrievedChunk]: full Phase 2 Chunk + method, rank, score, bm25/dense ranks & scores
                                                                     └─► Phase 4 reranking (FR-08: 20 → 5)
```

| Element | Design |
|---|---|
| **Row ↔ chunk mapping** | Every index stores `chunk_order.npy`: row *i* of the BM25 statistics, of `embeddings.npy` and of the FAISS index is `chunk_order[i]` (= line *i* of `chunks.jsonl`). |
| **Staleness** | Indexes record a fingerprint of the chunk-ID list. `Retriever.load` refuses an index whose chunk list differs from the current `chunks.jsonl` (`IndexStaleError`), and a dense index built with another model or dimension (`IndexMismatchError`). |
| **BM25** (D18) | Own implementation over numpy postings (no pickle; byte-reproducible). Tokenizer keeps legal references whole — `19(2)`, `19(1)(g)` plus their prefixes — and normalizes OCR digit confusions (`19(l)(g)`, `l996`) on both index and query side. Raw term statistics are stored; `k1`/`b` apply at query time. |
| **Embeddings** (D17) | `BAAI/bge-small-en-v1.5` (384-dim) via sentence-transformers, behind an `Embedder` interface (NFR-05). BGE query instruction on queries only. Cached by chunk ID: unchanged chunks are never re-embedded; a model change re-embeds everything. |
| **FAISS** | `IndexFlatIP` over L2-normalized vectors = exact cosine search; deterministic; trivial size at V1/V2 scale (SRS §39). |
| **Hybrid** (D19) | Weighted RRF over ranks only (never raw scores, never metadata); ties broken by best rank, then row. A query with no BM25 terms still returns the dense list. |
| **Scoring inputs** | Chunk text only. No authority, section type, division, article or any other metadata enters BM25, the embeddings or RRF (Invariant 1, FR-SA-05). |

### 3.4 Answering (Phase 4 as scoped in D20 — D20, D21, D22)

```
question ─► validate ─► analyze_query            (query/understanding.py: type, Article refs, named case)
         ─► Retriever.hybrid(pool = 50)          (Phase 3, unchanged)
         ─► select_evidence                      (reranking/evidence_selector.py)
               + provision lookup (cited Article's own text) + case lookup (named judgment's headnote)
               final_score = hybrid RRF + locator boosts (is the Article / is the case / mentions the Article)
               order = presentation group by intent, then final_score; caps; top_k = 6
         ─► assess_confidence ──(fails)──► Answer(status=insufficient_evidence, evidence + reasons, no claims)
         ─► generator.generate                   (generation/answer.py: extractive, verbatim quotes)
         ─► validate_answer                      (generation/grounding.py: every citation & quote vs evidence)
         ─► Answer (claims by section, numbered citations, evidence with all scores, notices, disclaimer)
```

| Query type | Leads with | Then |
|---|---|---|
| PROVISION ("What is Article 14?") | the Article's own text (looked up if retrieval missed it) | judgments mentioning it, as supporting evidence |
| INTERPRETATION ("How has the Court interpreted Article 21?", "… doctrine") | judgment passages | the Article's text in one reserved slot |
| CASE ("What did X decide?") | the named judgment (its reporter's summary for a bare question) | other judgments discussing it |
| GENERAL | relevance order | — |

**Invariant 1 in Phase 4:** `final_score` never contains a source-type or authority weight. It is the hybrid RRF score plus *locator* matches only: the chunk is the cited Article (`article_number`), belongs to the named case (`document_id`), or mentions the cited Article in its text. Source type decides only the presentation group, which is labelling/display (SRS §20.2). Experiments A–D and FR-08 consume `Retriever` output, which Phase 4 does not change.

**Grounding:** every answer, from any backend, passes `validate_answer`. Each citation must point to a selected evidence item and equal that item's metadata field for field. Each quote must be verbatim (whitespace-normalized) in the cited chunk. An insufficient-evidence answer carries no claims or citations.

```
python scripts/query.py "What is Article 21?" --answer                  # grounded, cited answer
python scripts/query.py "What is Article 21?" --answer --show-evidence  # + every evidence item, its scores and boosts
python scripts/query.py "What is Article 21?" --answer --json           # Answer JSON
```

**Build and query:**
```
python scripts/build_indexes.py                    # BM25 + embeddings + FAISS (first run downloads the model)
python scripts/build_indexes.py --only bm25        # BM25 only, no model needed
python scripts/query.py "What does Article 21 provide?"            # hybrid
python scripts/query.py "Article 19(2)" --mode bm25 --top-k 5      # bm25 | dense | hybrid
python scripts/query.py "basic structure" --json                   # RetrievedChunk JSON lines
```

---

## 4. V2 Architecture (SRS §16)

V2 replaces only the tail of the V1 pipeline, from "Top Evidence" onward — it does not touch ingestion, chunking, retrieval, or reranking:

```
Top Evidence
   ↓
LLM Answer Generation (authority- and structure-aware prompting)
   ↓
Claim Extraction
   ↓
Claim → Citation Mapping
   ↓
Evidence Verification
   ├── Semantic Similarity
   ├── NLI
   ├── Holding-vs-Argument Check
   └── Optional LLM Judge (tie-breaker only, low-confidence NLI cases)
   ↓
Verification Decision
   (Supported / Partially Supported / Contradicted / Neutral-Insufficient)
   ↓
Self-Correction: Remove/Flag Unsupported Claim
   (bounded regeneration, max 2 attempts — enhancement, not required for MVP)
   ↓
Final Answer
```

### 4.1 Module map (V2, additive)

| Pipeline stage | Repo module | SRS reference |
|---|---|---|
| Source-type/section-type tagging (extends chunking) | `src/.../chunking/section_detector.py` | §19.2 (FR-JS-01), §20.3 (FR-SA-01/02). V1's `structure.py` records judgment headings verbatim and never assigns `section_type` |
| Authority- and structure-aware generation | `src/.../generation/prompts.py` (V2 prompt variant) | §26 (FR-JS-02) |
| Claim extraction | `src/.../verification/claim_mapper.py` | §22 (FR-18) |
| Semantic similarity | `src/.../verification/semantic.py` | §22 (FR-19) |
| NLI verification | `src/.../verification/nli.py` | §22 (FR-20) |
| Holding-vs-argument check | `src/.../verification/section_classifier.py`, `decision.py` | §19.2 (FR-JS-03/04), §22 (FR-21) |
| Verification decision / self-correction | `src/.../verification/verifier.py` | §22 (FR-22–FR-24) |
| Citation completeness | `src/.../verification/verifier.py` (reads `citations/`) | §22 (FR-25) |
| V2 UI additions (authority level, section type, 4-way status) | `app/components/verification_view.py` | §20.3 (FR-SA-03/04), §31.2 |
| Verification evaluation | `src/.../evaluation/verification_metrics.py` | §34.2, §35 |

### 4.2 User document collections (V2, Phase 15 — SRS v4.2 FR-COL-01–06)

A product extension, independent of RQ3 and the C vs. D experiment. It adds **no new pipeline**: a collection is another `corpus_id`, fed through the V1 ingestion core.

| Capability | Planned location | Builds on |
|---|---|---|
| Upload a PDF into a collection | `app/components/collections.py` → calls `ingestion.pipeline.ingest_document(path, spec, corpus_id=<collection>)` | FR-01a (exists) |
| Create / list collections; `constitutional-core` reserved | `app/components/collections.py` (list = distinct corpus directories/IDs; a `collections` table only if display metadata is needed) | Invariant 4 |
| `source_type = user_document`, "user-supplied, unverified" label | `common/models.py` (enum value added in V2), `app/components/verification_view.py` | SRS v4.2 §20 amendment |
| Collection-scoped retrieval | `retrieval/`, `reranking/` with `corpus_id` = selected collection | FR-05a (Phase 3) |
| Verification over collection answers | `verification/` (unchanged) | FR-18–FR-25 |

Not planned in any phase: authentication, per-user ownership or permissions, sharing or collaboration, cloud storage, deletion (unspecified; see the amendment's conflict C3).

**Note:** `src/.../verification/` is the one module tree with a hard architectural rule attached (Invariant 3, Section 2 above) — nothing under `retrieval/`, `reranking/`, or the V1 half of `generation/` may import from it.

---

## 5. Data Flow (SRS §17)

**Ingestion → Parsing → Chunking → Indexing → Retrieval → Generation → (V2: Verification) → Presentation.**

1. **Ingestion** — a source is registered **into a corpus** and gets a corpus-scoped `document_id` and a version. V1: admin/script via the curated manifest, always `constitutional-core`. V2: also user upload into a collection (see `DECISIONS.md` D3 for why this is a function call, not an HTTP endpoint, in Phase 1, and D15 for the corpus model).
2. **Parsing** — text, headings, article numbers, case metadata, page numbers, source URL extracted.
3. **Chunking** — structure-aware (D16): articles and headed judgment sections are chunk boundaries; units over `max_tokens` (default 600) are split at line boundaries with 50–100 token overlap inside the unit only; full provenance on every chunk. Output: `data/processed/<corpus_id>/chunks.jsonl`, current document versions only.
4. **Indexing** — BM25 term index preserving article/sub-clause tokens, and BGE embeddings in a FAISS flat inner-product index, per corpus under `indexes/<corpus_id>/` (§3.3; D17, D18).
5. **Retrieval** — BM25 top-20 + dense top-20 fused by RRF into a top-20 candidate list (Phase 3, D19); cross-encoder reranking to top-K (Phase 4).
6. **Generation** — LLM produces structured claims + citations constrained to retrieved evidence.
7. **(V2 only) Verification** — claim extraction, claim-citation mapping, similarity + NLI + holding-check scoring, verification-label assignment, self-correction.
8. **Presentation** — UI renders answer, inline citations, and (V2) verification status.

All writes to the document store / metadata store are versioned (NFR-07); the evaluation pipeline is decoupled from the live query path in both phases (NFR-01).

**Storage note (Phases 1–2):** per `DECISIONS.md` D4, step 1–2's output currently lands in `data/processed/<corpus_id>/documents.jsonl` and `metadata.jsonl` (per-corpus directory, D15; V1: `data/processed/constitutional-core/`), not a database — this is a Phase 1–only interim, mirroring the SRS §24 schema field-for-field so the later move to SQLite (Phase 2, when chunking/indexing need queryable storage) is a backend swap, not a schema change. Per D11: `metadata.jsonl` is the SRS §24 `documents` table (one `DocumentMetadata` row per document version); `documents.jsonl` holds the parsed content (one `ParsedDocument` per document version, each page carrying its own `Provenance`). Input is a manifest, `data/raw/manifest.yaml` (D8; format in `data/raw/manifest.example.yaml`). Phase 2 adds `chunks.jsonl` (one `Chunk` per line) to the same corpus directory; it is derived data, regenerated in full by `scripts/chunk_corpus.py`.

**Traceability invariant (Phase 1):** `page text → page (1-based page_number) → document_id@document_version → corpus_id → that corpus's metadata.jsonl row`. `ParsedDocument` enforces this at validation time — a page whose provenance names another corpus/document/version, or pages not numbered exactly 1..N, cannot be constructed or loaded.

---

## 6. Current Implementation Status

Updated as of Phase 4 (query understanding, evidence selection, grounded answers). Status values: **Not started / Scaffolded / In progress / Complete**.

| Module | Status | Notes |
|---|---|---|
| `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `.gitignore`, `.dockerignore` | Complete | Phase 1 scope only (D2). Dockerfile install order fixed so the image builds; `scripts/` now copied in |
| `docs/ARCHITECTURE.md`, `DECISIONS.md` | Complete (this pass) | Living documents, updated each phase |
| `docs/SRS_v4.2_AMENDMENT.md` | Complete | SRS v4.2 = v4.1 + this amendment (V1 curated corpus / V2 user collections) |
| `docs/SRS.md`, `docs/REQUIREMENT_ANALYSIS.md` | Not in repo | Base texts exist only as SRS v4.1 / RAD v1.0 PDFs outside the repo. The RAD needs the updates listed in the amendment's §8 |
| `src/.../common/` (config, models, provenance, logging, pages) | Complete | `pages.py` for page-level output (D11); `corpus_id` on documents/provenance/pages (D15) |
| `src/.../ingestion/` + `scripts/ingest_corpus.py` | In progress | Page-by-page extraction, corpus-generic core + curated-manifest adapter (FR-01a, D15), versioning, failure handling complete; existing OCR text layers used. Pending: OCR fallback for image-only pages and OCR-origin marking (FR-02a), FR-02 structural fields and the 10-document spot-check (D8) |
| User document collections (upload, collection UI) | Not started | **V2, Phase 15** (FR-COL-01–06); must not be exposed in V1 (Invariant 4) |
| `src/.../chunking/` (`structure.py`, `legal_chunker.py`) + `common/chunks.py` + `scripts/chunk_corpus.py` | Complete (Phase 2) | D16. Verified on synthetic Constitution/judgment fixtures and on two real SCR judgments (Vishaka, Mafatlal). Not yet run on the real Constitution PDF (not in the corpus yet). `paragraph_number` not detected; `extraction_method` awaits FR-02a |
| `src/.../retrieval/` + `common/retrieval.py` + `scripts/build_indexes.py`, `scripts/query.py` | Complete (Phase 3) | D17–D19. BM25 built and queried on the real corpus. Dense path tested end to end with a deterministic test embedder only: the BGE model has not yet been run, because model downloads were blocked in the build environment. Open: locator text for article-number queries (DECISIONS open items) |
| `src/.../query/`, `reranking/evidence_selector.py`, `citations/`, `generation/` + `common/evidence.py`, `common/answer.py`; `scripts/query.py --answer` | Complete (Phase 4 as scoped in D20) | D20–D22. Extractive generator only (no LLM chosen yet). Tested on fixtures and run on the real corpus with BM25 only, because BGE was unavailable in the build environment |
| `src/.../reranking/cross_encoder.py` | Not started | SRS FR-08 / Experiment C — still open |
| `src/.../generation/`, `citations/`, `evidence/` | Not started | Phase 5–6 target |
| `app/` | Not started | Phase 7 target |
| `src/.../evaluation/` (retrieval metrics) | Not started | Phase 8 target |
| `src/.../verification/` | Not started | V2, Phase 9–13 target — must not be started before V1 Phase 8 is complete, per Invariant 3 |

---

## 7. Deployment Architecture (SRS §40)

Single-machine deployment via Docker Compose. Current Phase 1 state: one `app` service (ingestion is run on demand: `docker compose run --rm app python scripts/ingest_corpus.py`, with `./data` mounted), no separate DB container (see `DECISIONS.md` D6 for why the SRS's "one container for the app, one for the DB" language isn't acted on literally while SQLite remains the default metadata store). A `db` service is added only if the project later adopts PostgreSQL under §39's "larger deployment" condition — not as a default step.

Evaluation runs (`scripts/evaluate.py`) are a separate, decoupled process against a fixed corpus snapshot (NFR-04) and never sit on the live query path (NFR-01) — this applies from Phase 8 (V1 retrieval benchmark) onward.

---

## 8. Cross-References

- **`docs/SRS.md`** — the requirements this architecture implements (v4.1 base text).
- **`docs/SRS_v4.2_AMENDMENT.md`** — v4.2 changes: V1 curated corpus, V2 user collections, FR-01a / FR-02a / FR-05a / FR-COL-*.
- **Requirement Analysis Document (RAD v1.0)** — dependency chains (§7), feasibility (§8), and traceability matrix (§9) that this architecture's module map is derived from.
- **`docs/DECISIONS.md`** — every point where this architecture document states something the SRS leaves as a choice (storage backend, API layer timing, container topology).
