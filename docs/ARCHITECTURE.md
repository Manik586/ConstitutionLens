# ARCHITECTURE.md

This document describes the system architecture of Constitutional Evidence RAG: the canonical pipelines, the module map from architecture to actual repo paths, the architectural invariants that must hold in every phase, and the current implementation status. It is a living document — update it in the same commit that changes a module's shape, not after the fact.

For *why* a given implementation choice was made where the SRS leaves room for interpretation, see `DECISIONS.md`. This document describes *what the architecture is*; `DECISIONS.md` explains *why it is that way*.

---

## 1. Scope of this document

Covers both phases, since architecture is designed once and built incrementally:

- **V1 architecture** (SRS §15) — complete, independently evaluable retrieval + generation pipeline.
- **V2 architecture** (SRS §16) — extends only the tail of V1, from "Top Evidence" onward.

Current build status is tracked in **Section 6**, separate from the target architecture described in Sections 2–5, so this document doesn't need rewriting each phase — only Section 6's status table changes.

---

## 2. Architectural Invariants

These hold across both phases and are the two things a code reviewer should check regardless of which module is being reviewed:

1. **Retrieval and reranking never receive authority-level or section-type input.** These signals (SRS §20) exist only downstream, for labeling and verification. Any code path that passes `source_type`/`section_type`/authority-level into a retrieval or reranking scoring function is a defect, not a feature — see D6-adjacent note in `DECISIONS.md` and SRS §20.2 / FR-SA-05.
2. **No autonomous multi-agent architecture, at either phase.** Verification (V2) is a deterministic pipeline of scoring stages, not an agentic loop. The only loop anywhere in the system is the bounded, optional self-correction retry (SRS §30, max 2 attempts).
3. **V1 has zero dependency on any V2 component.** The RAD (§7.3) confirms this at the requirement-dependency level: all of V2 depends on FR-12 (V1) existing; no V1 requirement depends on anything in V2. Architecturally, this means the `src/.../retrieval/`, `reranking/`, `generation/` (base), `citations/`, and `evidence/` modules must build, run, and be tested with no import of anything under `src/.../verification/`.

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
| Chunking (locational metadata, overlap) | `src/.../chunking/legal_chunker.py` | §21.1 (FR-03, FR-04) |
| Dense retrieval | `src/.../retrieval/dense.py` | §21.2 (FR-05) |
| BM25 retrieval | `src/.../retrieval/bm25.py` | §21.2 (FR-06) |
| RRF fusion | `src/.../retrieval/rrf.py`, `hybrid.py` | §21.2 (FR-07) |
| Cross-encoder reranking | `src/.../reranking/cross_encoder.py` | §21.2 (FR-08, FR-09) |
| Generation (evidence-constrained, structured output) | `src/.../generation/llm.py`, `prompts.py`, `claims.py`, `answer.py` | §21.3 (FR-10, FR-11) |
| Citation linkage | `src/.../citations/citation_builder.py`, `source_formatter.py` | §21.3 (FR-12) |
| Basic Evidence Check | `src/.../evidence/basic_check.py` | §21.3 (FR-13) |
| Non-advice framing (cross-cutting, not a pipeline stage) | Applied in `generation/prompts.py` and `app/` UI copy | §21.3 (FR-14), §21.4 (FR-17) |
| UI | `app/streamlit_app.py`, `app/components/` | §21.4 (FR-15–FR-17) |
| Retrieval evaluation | `src/.../evaluation/retrieval_metrics.py`, `runner.py`, `reports.py` | §34.1 |

FR-14 and FR-17 are **cross-cutting constraints**, not stages in the chain (RAD §7.1) — they gate every generated string and every UI screen, not one point in the pipeline. There is no single module that "implements" them; they're a review criterion applied to `generation/` output and all of `app/`.

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
| Source-type/section-type tagging (extends chunking) | `src/.../chunking/section_detector.py` | §19.2 (FR-JS-01), §20.3 (FR-SA-01/02) |
| Authority- and structure-aware generation | `src/.../generation/prompts.py` (V2 prompt variant) | §26 (FR-JS-02) |
| Claim extraction | `src/.../verification/claim_mapper.py` | §22 (FR-18) |
| Semantic similarity | `src/.../verification/semantic.py` | §22 (FR-19) |
| NLI verification | `src/.../verification/nli.py` | §22 (FR-20) |
| Holding-vs-argument check | `src/.../verification/section_classifier.py`, `decision.py` | §19.2 (FR-JS-03/04), §22 (FR-21) |
| Verification decision / self-correction | `src/.../verification/verifier.py` | §22 (FR-22–FR-24) |
| Citation completeness | `src/.../verification/verifier.py` (reads `citations/`) | §22 (FR-25) |
| V2 UI additions (authority level, section type, 4-way status) | `app/components/verification_view.py` | §20.3 (FR-SA-03/04), §31.2 |
| Verification evaluation | `src/.../evaluation/verification_metrics.py` | §34.2, §35 |

**Note:** `src/.../verification/` is the one module tree with a hard architectural rule attached (Invariant 3, Section 2 above) — nothing under `retrieval/`, `reranking/`, or the V1 half of `generation/` may import from it.

---

## 5. Data Flow (SRS §17)

**Ingestion → Parsing → Chunking → Indexing → Retrieval → Generation → (V2: Verification) → Presentation.**

1. **Ingestion** — admin/script registers a source; a `document_id` and version are assigned (see `DECISIONS.md` D3 for why this is a function call, not an HTTP endpoint, in Phase 1).
2. **Parsing** — text, headings, article numbers, case metadata, page numbers, source URL extracted.
3. **Chunking** — 400–600 token chunks (50–100 overlap, configurable), full locational metadata attached.
4. **Indexing** — dense embeddings into a vector index (FAISS, SRS §39); BM25 term index preserving article/sub-clause tokens.
5. **Retrieval** — hybrid RRF fusion, cross-encoder reranking to top-K.
6. **Generation** — LLM produces structured claims + citations constrained to retrieved evidence.
7. **(V2 only) Verification** — claim extraction, claim-citation mapping, similarity + NLI + holding-check scoring, verification-label assignment, self-correction.
8. **Presentation** — UI renders answer, inline citations, and (V2) verification status.

All writes to the document store / metadata store are versioned (NFR-07); the evaluation pipeline is decoupled from the live query path in both phases (NFR-01).

**Storage note (Phase 1):** per `DECISIONS.md` D4, step 1–2's output currently lands in `data/processed/documents.jsonl` and `metadata.jsonl`, not a database — this is a Phase 1–only interim, mirroring the SRS §24 schema field-for-field so the later move to SQLite (Phase 2, when chunking/indexing need queryable storage) is a backend swap, not a schema change. Per D11: `metadata.jsonl` is the SRS §24 `documents` table (one `DocumentMetadata` row per document version); `documents.jsonl` holds the parsed content (one `ParsedDocument` per document version, each page carrying its own `Provenance`). Input is a manifest, `data/raw/manifest.yaml` (D8; format in `data/raw/manifest.example.yaml`).

**Traceability invariant (Phase 1):** `page text → page (1-based page_number) → document_id@document_version → metadata.jsonl row`. `ParsedDocument` enforces this at validation time — a page whose provenance names another document/version, or pages not numbered exactly 1..N, cannot be constructed or loaded.

---

## 6. Current Implementation Status

Updated as of Phase 1 PDF ingestion. Status values: **Not started / Scaffolded / In progress / Complete**.

| Module | Status | Notes |
|---|---|---|
| `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `.gitignore`, `.dockerignore` | Complete | Phase 1 scope only (D2). Dockerfile install order fixed so the image builds; `scripts/` now copied in |
| `docs/ARCHITECTURE.md`, `DECISIONS.md` | Complete (this pass) | Living documents, updated each phase |
| `docs/SRS.md`, `docs/REQUIREMENT_ANALYSIS.md` | Not in repo | Authoritative versions exist as SRS v4.1 / RAD v1.0 PDFs; README links `docs/SRS.md`, so they should be added (or the README link changed) |
| `src/.../common/` (config, models, provenance, logging, pages) | Complete | `pages.py` added for page-level output (D11); validation constraints added to `models.py` / `provenance.py` |
| `src/.../ingestion/` + `scripts/ingest_corpus.py` | In progress | Page-by-page extraction, stable IDs, versioning, failure handling complete. FR-02 structural fields (section headings, article numbers) and the FR-02 10-document spot-check still pending (D8) |
| `src/.../chunking/` | Not started | Phase 2 target |
| `src/.../retrieval/`, `reranking/` | Not started | Phase 3–4 target |
| `src/.../generation/`, `citations/`, `evidence/` | Not started | Phase 5–6 target |
| `app/` | Not started | Phase 7 target |
| `src/.../evaluation/` (retrieval metrics) | Not started | Phase 8 target |
| `src/.../verification/` | Not started | V2, Phase 9–13 target — must not be started before V1 Phase 8 is complete, per Invariant 3 |

---|---|---|
| `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `.gitignore`, `.dockerignore` | Complete | Phase 1 scope only (D2) |
| `docs/SRS.md`, `ARCHITECTURE.md`, `DECISIONS.md` | Complete (this pass) | Living documents, updated each phase |
| `src/.../common/` (config, models, provenance, logging) | Not started | Next: Pydantic models mirroring SRS §24 schema (D4) |
| `src/.../ingestion/` | Not started | Phase 1 target |
| `src/.../chunking/` | Not started | Phase 2 target |
| `src/.../retrieval/`, `reranking/` | Not started | Phase 3–4 target |
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

- **`docs/SRS.md`** — the requirements this architecture implements.
- **Requirement Analysis Document (RAD v1.0)** — dependency chains (§7), feasibility (§8), and traceability matrix (§9) that this architecture's module map is derived from.
- **`docs/DECISIONS.md`** — every point where this architecture document states something the SRS leaves as a choice (storage backend, API layer timing, container topology).
