# SRS v4.2 Amendment — Curated V1 Corpus, User Document Collections in V2

| Field | Value |
|---|---|
| Amends | Constitutional Evidence RAG SRS **v4.1** |
| Resulting version | **v4.2** = v4.1 + this amendment |
| Precedence | Where this amendment and v4.1 differ, this amendment governs. Every section not named here is unchanged. |
| Format | Follows the SRS's own convention (v4.1 Appendix A): an itemized change record keyed to v4.1 section numbers, with replacement or added text. The v4.1 text is not reproduced. |
| Companion updates | `docs/ARCHITECTURE.md` (§2 invariant 4, §3.2, §4.2, §5, §6), `docs/DECISIONS.md` D15 |

---

## 1. Summary

1. **V1 is one controlled, curated corpus**: the Constitution of India plus ~30 selected Supreme Court constitutional judgments. There is no user upload of any kind in V1.
2. **User-provided documents move to V2**, as a product extension alongside V2's research contribution (claim-level verification). This covers user-uploaded legal PDFs, named document collections, collection-scoped retrieval, and verification of answers over a collection.
3. **V1 ingestion is generic over a corpus** (`corpus_id`), even though V1 has exactly one corpus. V2 collections reuse the V1 ingestion pipeline unchanged; they are additional corpora, not a second pipeline.

---

## 2. Version History (adds a row to v4.1 §2)

| Version | Change |
|---|---|
| 4.2 | Scope-boundary amendment. User-provided document uploads and user document collections are explicitly V2. V1 is restricted to a single curated corpus (Constitution + ~30 curated judgments) and gains an explicit OCR fallback requirement. The document model gains a `corpus_id` so that V1 ingestion, storage, and retrieval interfaces are corpus-generic. The v4.1 research design (RQ1–RQ3, Experiments A–D), terminology, authority model, and out-of-scope list are unchanged, except for the clarifications in §7 of this amendment. |

---

## 3. The V1 / V2 Boundary

| Capability | V1 | V2 | Notes |
|---|---|---|---|
| Constitution of India (full text, dated "as on") | ✅ | ✅ | Curated corpus (`constitutional-core`) |
| Supreme Court constitutional judgments | ✅ ~30 curated | ✅ expanded curated set, 75–150 (v4.1 §10) | Curated corpus only; selected, dated, licence-checked (§18, D7, D14) |
| Relevant amendment material | ✅ | ✅ | Curated corpus |
| PDF ingestion, page-by-page, with provenance | ✅ | ✅ (same code) | FR-01–FR-04 |
| OCR fallback for pages without a text layer | ✅ (FR-02a) | ✅ (same code) | Existing OCR text layers are used as-is |
| Generic ingestion interface `ingest(document_path, corpus_id)` | ✅ interface exists; only the curated corpus is ever used | ✅ used for user collections | FR-01a; V1 never exposes it to users |
| Legal-aware chunking | ✅ | ✅ | FR-03 |
| BM25 + dense (FAISS) retrieval, RRF, cross-encoder reranking | ✅ over the curated corpus | ✅ over the curated corpus **or** one selected collection | FR-05a: retrieval is parameterized by `corpus_id` from V1 onward |
| Evidence-grounded generation, page-level citations | ✅ | ✅ | FR-10–FR-12 |
| Basic Evidence Check | ✅ | superseded by verification | FR-13 |
| **User-uploaded legal PDFs** | ❌ | ✅ | FR-COL-01 |
| **User document collections** (named corpora) | ❌ | ✅ | FR-COL-02 |
| **Retrieval restricted to the selected collection** | ❌ | ✅ | FR-COL-04 |
| **Claim-level citation / evidence verification** | ❌ | ✅ | FR-18–FR-25, FR-JS-*, FR-SA-* (v4.1, unchanged); applies to collections via FR-COL-05 |
| **Persistent collections on the local deployment** (listing, re-ingestion as new version) | ❌ | ✅ | FR-COL-06; supported by existing NFR-07 and UC-6 |
| Deleting collections or documents | ❌ | ❌ not specified | NFR-07 makes ingested documents immutable; deletion is an open question, not added (§7, C3) |
| Per-user ownership, access control between collections | ❌ | ❌ out of scope | Requires authentication (v4.1 §11) |
| Authentication, multi-tenancy, cloud storage, collaboration, sharing, permissions | ❌ | ❌ out of scope | v4.1 §11, restated in §5.4 below |

---

## 4. Section-by-Section Amendments

### v4.1 §3 Executive Summary — V2 paragraph, append
> V2 also adds user document collections: a user may upload their own legal PDFs into a named collection and query that collection. Collections are ingested by the same pipeline as the curated corpus, and answers over them are verified by the same claim-level verification layer. Collections are a product extension. They are not part of the V2 research question (RQ3), which is evaluated on the curated corpus only.

### v4.1 §6.1 V1 Objectives — objective 1, replace
> 1. Ingest and parse the Constitution of India, relevant amendment material, and a curated set of **~30** Supreme Court constitutional judgments, forming the single curated V1 corpus. The ingestion/chunking design must scale to ~75 without rework, and must be generic over the target corpus so that V2 can ingest user collections without rewriting it.

### v4.1 §6.2 V2 Objectives — add
> 17. Allow users to upload legal PDFs into named document collections, ingested through the unchanged V1 ingestion pipeline, and to query a selected collection with retrieval restricted to it. This is a product objective, not a research objective; it adds no research question.

### v4.1 §9 V1 Scope — replace "Sources" paragraph and add two paragraphs
> **Sources:** a single curated corpus (`constitutional-core`): the full Constitution of India (with its "as on" date), relevant amendment material, and **~30** carefully selected Supreme Court constitutional judgments. The architecture must scale to ~75 without redesign. Selection prioritizes Article/doctrine coverage over document count. (v4.1 stated 30–50; ~30 is now the target, and the band up to 50 remains acceptable.)
>
> **Ingestion:** PDF ingestion page by page, using each page's text layer, including OCR text layers already present in scanned judgments, with an OCR fallback for pages that have no text layer (FR-02a).
>
> **No user-provided documents.** V1 has no upload path, no user collections, and no way for an end user to add documents. Documents enter V1 only through the curated manifest maintained by the project team.

### v4.1 §10 V2 Scope — add after the corpus-expansion bullet
> • **User document collections (product extension, FR-COL-01–06):** user-uploaded legal PDFs are ingested into named collections by the same pipeline as the curated corpus; retrieval can be restricted to one selected collection; claim-level verification applies to collection answers; collections persist on the local deployment. Collections are **not** part of the C vs. D experiment and are not a prerequisite for answering RQ3 (see §4, §41).

The v4.1 sentence *"It adds exactly one research contribution … it does not introduce new research directions"* remains true. Collections add engineering scope, not research scope (§7, C1).

### v4.1 §12 User Personas — add row
| Persona | Interest / Need | Phase |
|---|---|---|
| Researcher with own document set | Query and verify answers over legal PDFs they supply themselves | V2 |

### v4.1 §13 Use Cases — add rows
| # | Use Case | Primary Actor | Phase | Flow |
|---|---|---|---|---|
| UC-10 | Create a document collection | Any user | V2 | Name collection → collection listed |
| UC-11 | Upload a legal PDF into a collection | Any user | V2 | Upload → same ingestion pipeline → versioned record in that collection |
| UC-12 | Ask a question restricted to a collection | Any user | V2 | Select collection → query → retrieval over that collection only → verified answer |

UC-6 (re-ingest an updated source, admin) is unchanged and remains the curated-corpus path in both phases.

### v4.1 §14 System Architecture — add a third invariant
> 3. **Corpus isolation.** Every document belongs to exactly one corpus (`corpus_id`). Ingesting into one corpus never modifies another corpus's stored documents, chunks, or indices. V1 has exactly one corpus. The curated corpus cannot be written to through the V2 upload path.

### v4.1 §17 Data Pipeline — step 1, replace
> 1. **Ingestion:** a source is registered **into a corpus**. In V1 the only corpus is the curated corpus, registered by an admin through the manifest; in V2, a user collection, registered through upload. The system assigns a corpus-scoped document ID and a version.

### v4.1 §18 Legal Corpus and Provenance — add
> The licensing requirement (confirmed before ingestion, re-confirmed on expansion) applies to the **curated corpus**. User-uploaded documents (V2) are supplied by the user for their own local use. They are stored only on the local deployment, are never merged into the curated corpus, and are never used in any reported experiment.
>
> Provenance chain, both phases: `corpus_id → document_id → source_type → version → source_url` (+ page). User uploads without a public URL receive a synthetic source URI that identifies the upload, so NFR-03 traceability still holds.

### v4.1 §20 Source Authority Model — add
> **User-supplied documents (V2)** carry `source_type = user_document`, a value added in V2. They are labeled "user-supplied, unverified" and are never displayed as Level 1 or Level 2 authority, whatever they claim to contain. FR-SA-05 is unchanged: neither authority level nor corpus membership is a ranking signal. Restricting retrieval to one corpus is a scope filter applied before ranking (FR-05a), not a score modifier.

### v4.1 §21.1 Functional Requirements — FR-01 and new FR-01a / FR-02a (V1)

**FR-01, add to the acceptance criterion:** "…each document is registered into exactly one corpus; in V1 that corpus is always `constitutional-core`." (D3's function-level interpretation of the HTTP wording is unchanged.)

| ID | Requirement | Priority | Acceptance Criterion |
|---|---|---|---|
| FR-01a | The ingestion interface takes the target corpus as a parameter, conceptually `ingest(document_path, metadata, corpus_id)`. Nothing in ingestion, parsing, or storage assumes a specific corpus or document set. The curated manifest is one caller of that interface. | Must | Code review: the manifest path calls the generic interface. A test ingests into a second corpus through the same interface and shows the curated corpus's stored files are byte-identical before and after. |
| FR-02a | Page text comes from the PDF's text layer, including an OCR text layer already present in a scan. Pages with no text layer are OCR'd as a fallback. Pages whose text originates from OCR are recorded as such. | Must | On a spot-check sample (FR-02), OCR-derived pages are identified as OCR-derived; a fully image-only PDF yields text instead of being rejected. |

### v4.1 §21.2 — new FR-05a (V1)
| ID | Requirement | Priority | Acceptance Criterion |
|---|---|---|---|
| FR-05a | Indices (dense and BM25) are built per corpus, and retrieval/reranking take the `corpus_id` to search. V1 always passes the curated corpus. The parameter is a scope, never a ranking input (FR-SA-05 unchanged). | Must | Retrieval functions accept `corpus_id`; a query against one corpus returns no chunk from another (test with identical text in two corpora). |

### v4.1 §22 Functional Requirements — V2: add "User Document Collections"
| ID | Requirement | Priority | Acceptance Criterion |
|---|---|---|---|
| FR-COL-01 | A user uploads a legal PDF into a collection from the UI. It is ingested by the V1 pipeline (FR-01–FR-04, FR-02a) with `corpus_id` = that collection. There is no separate parser or ingestion path. | Must (Phase 15) | Code review: the upload handler calls the same ingestion interface as the curated manifest. |
| FR-COL-02 | Users can create and list named collections. The curated corpus ID is reserved and cannot be uploaded into. | Must (Phase 15) | An upload targeting `constitutional-core` is rejected; created collections are listed. |
| FR-COL-03 | Upload metadata: title required; `source_type = user_document`; synthetic source URI when no public URL exists; source date optional. | Must (Phase 15) | Every uploaded document satisfies the NFR-03 provenance chain. |
| FR-COL-04 | A query targets exactly one corpus: the curated corpus or one selected collection. Answers cite only documents from that corpus. | Must (Phase 15) | Test with the same text in two corpora: only the selected corpus's chunks are retrieved and cited. |
| FR-COL-05 | Claim-level verification (FR-18–FR-25, FR-JS-01–04) runs on collection answers exactly as on curated answers. User-supplied sources are labeled per §20 above. Section-type classification falls back to `unknown` (FR-JS-04) for documents that are not SC judgments. | Must (Phase 15) | Collection answers carry verification labels; no user-supplied citation is shown as Level 1–2. |
| FR-COL-06 | Collections and their documents persist on the local deployment across restarts. Re-uploading a changed document creates a new version (NFR-07). | Should (Phase 15) | Collections survive restart; a re-upload with changed content is version 2 under the same document ID. |

### v4.1 §23 Non-Functional Requirements — amend NFR-04, NFR-07
- **NFR-04, append:** "Reported experiments (A–D) use only the curated corpus snapshot. User collections are excluded from every experiment and evaluation set."
- **NFR-07, append:** "Versioning is per corpus. Ingestion into one corpus never modifies another corpus's records."

### v4.1 §24 Data Model — changes
- `documents(document_id, corpus_id, source_type, title, source_url, document_version, source_date, ingestion_date, replaced_by)`: **`corpus_id` added (V1)**. The V1 value is always `constitutional-core`. `document_id` is unique across corpora because it is derived from `(corpus_id, source_type, source_url)` (D15).
- `queries(query_id, corpus_id, question, timestamp)`: **`corpus_id` added (V1 column, always the curated corpus in V1)**, so the query log needs no migration in V2.
- `chunks`: unchanged. They inherit their corpus through `document_id`.
- **No `collections` table in V1.** V2 may add `collections(corpus_id, display_name, created_at)` only if collection display metadata is needed; otherwise collections are derived from `documents.corpus_id` (consistent with v4.1 correction #8).
- `source_type` gains `user_document` **in V2 only**.

### v4.1 §25 Retrieval Design — append
> Retrieval runs over one corpus at a time (FR-05a). In V1 that is always the curated corpus. V2 adds the choice of a user collection (FR-COL-04). Combining the curated corpus and a collection in one query is not specified.

### v4.1 §31 UI Requirements — add §31.4 (V2)
> Collection selector (curated corpus or one collection), PDF upload into a non-curated collection, and a collection document list. User-supplied citations are visibly labeled "user-supplied, unverified". The non-advice notice (FR-17) persists on every screen, including upload screens.

### v4.1 §37 Risks — add (V2)
| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 16 | Users read verified answers over their own documents as authoritative law | Medium | High | §20 labeling ("user-supplied, unverified"); non-advice notice; FR-COL-05 |
| 17 | Uploaded documents are not SC judgments, so section-type classification is unreliable | High | Medium | FR-JS-04 `unknown` fallback; holding-vs-argument results labeled as unevaluated for non-judgment documents |
| 18 | Collection content leaks into curated-corpus answers, or the reverse | Low | High | Per-corpus storage and indices (FR-01a, FR-05a); isolation tests |
| 19 | Collections add engineering load that delays RQ3 | Medium | Medium | Phase 15 is scheduled after, and independent of, the C vs. D evaluation (§41 below) |

### v4.1 §41.2 Milestones — add a phase
| Phase | Deliverable |
|---|---|
| 15 | User document collections: upload, collections, collection-scoped retrieval, verification over collections (FR-COL-01–06). A **product extension**: not required for Phase 14's RQ3 result, and not a prerequisite for V2's research extension to count as complete. |

### v4.1 §42.2 V2 Success — add (evaluated separately from items 1–7)
> 8. (Phase 15) A user can upload a PDF into a collection and ask questions answered only from that collection, with verification labels, without any change to the ingestion pipeline and without altering the curated corpus.

### v4.1 §43.2 Deliverables — add
> 21. User document collections (upload, collection-scoped retrieval, collection UI).

### v4.1 §44 Limitations — add
> User collections have no access control: anyone with access to the deployment can see all collections (single-user / trusted-small-group deployment, v4.1 §40). Documents and collections cannot be deleted through the system.

---

## 5. Explicitly Unchanged

1. Research design: RQ1–RQ3, Experiments A/B/C/D, and C vs. D as the only primary V2 comparison. All experiments run on the curated corpus.
2. Terminology (§19.1), the authority model's rule that authority never affects ranking (§20.2, FR-SA-05), and holding-vs-argument detection (§29).
3. V1 technology stack (§39): no new infrastructure. V2 upload uses the existing UI layer and local storage; no API server, object store, or database server is required by this amendment.
4. **Out of scope (v4.1 §11), restated for collections:** user authentication, multi-tenant deployment, cloud storage, collaboration or sharing, per-user permissions or access control, and real-time synchronization remain out of scope in both phases.

---

## 6. Implementation Status (at the time of this amendment)

| Item | Status |
|---|---|
| FR-01a generic ingestion interface (`ingest_documents` / `ingest_document(document_path, spec, corpus_id=…)`) | Implemented (Phase 1); V1 CLI uses only the curated corpus |
| `corpus_id` on documents, page provenance, parsed documents; corpus-scoped document IDs; per-corpus storage | Implemented (Phase 1, D15) |
| FR-02a: use of existing OCR text layers | Works today (verified on two real SCR judgments) |
| FR-02a: OCR fallback for image-only pages; recording OCR origin | **Not implemented.** Image-only PDFs are still rejected with `NoExtractableTextError` (D12) until the fallback is built |
| FR-05a per-corpus indices / retrieval scope | Not started (Phase 3) |
| `queries.corpus_id` | Not started (no query log yet) |
| FR-COL-01–06 | V2 (Phase 15); not implemented |

---

## 7. Conflicts With v4.1 and Their Resolution

| # | v4.1 position | Tension with this change | Resolution |
|---|---|---|---|
| C1 | §3, §10, Appendix B: V2 is "the single research extension" (~25–30% of effort) | Collections add engineering work to V2, shifting the 70–75 / 25–30 balance | Collections add no research question; they are a separate phase (15), after and independent of RQ3. The research extension still completes at Phase 14 |
| C2 | §11: no coverage beyond constitutional law or beyond the SC | Users may upload any legal PDF | §11 governs what the project curates and evaluates. Collections are user material with no claim of fitness; verification quality is only evaluated on the curated corpus (Risk 17) |
| C3 | §11 / §40: no multi-user auth; single-user or trusted-small-group; NFR-07 immutability | "User-specific collections" and "document management" could imply ownership and deletion | Collections are named, not owned. Persistence, listing and versioned re-upload are supported. Ownership and access control are out of scope. Deletion is left unspecified, as an open question, rather than added |
| C4 | §18 / §38: licence confirmed before ingestion | Uploads have no project-level licence check | The licence check applies to the curated corpus; uploads stay local and out of every experiment |
| C5 | §9: 30–50 judgments | Request specifies ~30 | ~30 is the target; the 30–50 band remains acceptable |
| C6 | v4.1 does not mention OCR (only Risk #2) | V1 requires OCR fallback | Added as FR-02a (V1); implementation pending |

---

## 8. Required Updates to the Requirement Analysis Document (RAD v1.0)

RAD §2 requires the RAD to be updated in the same revision cycle as SRS requirement changes. The RAD is not in the repository and is not edited here. It needs:
- **§5 / §6:** classify and prioritize FR-01a, FR-02a, FR-05a (V1, Must) and FR-COL-01–06 (V2).
- **§7:** add FR-01a as a node before FR-01. Add the V2 chain FR-01a → FR-COL-01 → FR-COL-04 → FR-COL-05 (depends on FR-18–FR-25). Confirm that no V1 requirement depends on FR-COL-*.
- **§8:** feasibility of Phase 15 (engineering only; no new infrastructure).
- **§9:** traceability rows for objective 17 → FR-COL-* → Deliverable #21 (no RQ).
- **§10:** Risks 16–19.
- **§12:** conflicts C1–C6 above.
