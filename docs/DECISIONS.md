# DECISIONS.md

Engineering decisions for Constitutional Evidence RAG, logged as they're made. Each entry references the SRS (v4.1) and/or Requirement Analysis Document (v1.0) sections it interprets or depends on. This file is a live artifact — a new decision that reverses or refines an earlier one is appended, not edited in place, so the history stays visible.

Format: **Context → Decision → Consequences → SRS/RAD reference.**

---

## D1 — Python version and package layout

**Context:** Need a concrete runtime version and package structure before writing any ingestion code.

**Decision:** Python ≥3.11, `src/constitutional_evidence_rag/` layout, `hatchling` as the build backend, installed editable (`pip install -e .`).

**Consequences:** Matches the repo structure already agreed (target file tree). Src-layout avoids accidental imports of uninstalled code during testing. No consequence for later phases — this doesn't need revisiting when NLI/embedding dependencies are added in Phase 2+.

**Reference:** SRS §39 (Technology Stack: Python), target repo structure (prior turn).

---

## D2 — Phase 1 dependency scope is ingestion-only

**Context:** The full V1 stack (BM25, dense embeddings, FAISS, cross-encoder, LLM) is specified in SRS §39, but Phase 1 (§41.1, Phase 1) is scoped to "Corpus + ingestion + parsing" only.

**Decision:** `pyproject.toml` installs only `pymupdf`, `pydantic`, `pyyaml`, `python-dotenv`, plus `pytest` as a dev dependency. No retrieval, embedding, reranking, or LLM dependency is added until the phase that needs it.

**Consequences:** Keeps the Phase 1 Docker image small and avoids pinning ML library versions before a model choice is actually made (embedding model, cross-encoder, LLM provider are all still open per SRS §39's "or comparable/available" language). Each later phase (2–6, 9–11 per §41) adds its own dependency in the same commit that introduces the code using it — no dependency is added speculatively.

**Reference:** SRS §39, §41.1.

---

## D3 — FR-01's HTTP framing is deferred; Phase 1 has no API layer

**Context:** SRS §21.1 FR-01's acceptance criterion is written as an HTTP contract: *"POST to `/documents` returns a unique `document_id`."* But §39 marks FastAPI **optional**, and §41.1's Phase 1 has no API/UI milestone — the UI doesn't appear until Phase 7.

**Decision:** In Phase 1, FR-01 is satisfied at the function level: an ingestion pipeline function (`src/.../ingestion/pipeline.py`) assigns a unique `document_id` and writes a versioned record, callable from a script (`scripts/ingest_corpus.py`), not from an HTTP endpoint. The `/documents` POST contract described in FR-01 will be implemented literally only if/when a separate API layer is introduced — which SRS §39 ties to whether "the architecture genuinely requires" one (e.g., if the UI and pipeline end up as separate processes).

**Consequences:** FR-01's acceptance criterion as literally written is **not yet testable via HTTP** in Phase 1; it's testable at the function/CLI level instead. This is a deliberate, documented interpretation, not a gap — but it should be re-checked at Phase 7 (UI) or whenever FastAPI is actually added, to confirm the endpoint contract gets implemented to match FR-01's letter, not just its intent.

**Reference:** SRS §21.1 (FR-01), §39, §41.1; RAD §7.1 (FR-01 is the root of the V1 dependency chain — nothing downstream is blocked by this deferral since the *function* exists, only the *HTTP shape* is deferred).

---

## D4 — Phase 1 storage is flat JSONL files, not SQLite

**Context:** SRS §24 (Data Model) specifies a `documents` table as part of the eventual schema. The target repo structure lists `data/processed/documents.jsonl`, `chunks.jsonl`, `metadata.jsonl` as concrete files, not a database. Phase 1 (ingestion/parsing only) has no query pattern yet that needs an indexed store — retrieval (which would need one) is Phase 3.

**Decision:** Phase 1 persists ingestion output as JSONL files (`data/processed/documents.jsonl`, `metadata.jsonl`) rather than standing up SQLite yet. The Pydantic models in `common/models.py` are written to mirror the SRS §24 `documents` schema field-for-field (`document_id, source_type, title, source_url, document_version, source_date, ingestion_date, replaced_by`) from the start, so the later migration into actual SQLite tables (Phase 2, when chunking/indexing need queryable storage) is a storage-backend swap, not a schema redesign.

**Consequences:** No DB dependency or container needed for Phase 1. Re-ingestion versioning (FR-01, NFR-07) must be implemented as JSONL append/update-by-`document_id` logic in Phase 1, and re-verified once ported to SQLite in Phase 2 — this is a concrete carry-over task to check off, not just a note.

**Reference:** SRS §24, §41.1 (Phase 1 vs. Phase 2), NFR-07.

---

## D5 — `.dockerignore` excludes `tests/` from the Phase 1 build context

**Context:** No ingestion code or tests exist yet; the Dockerfile's `CMD` is a placeholder that only confirms the environment builds.

**Decision:** `tests/` is excluded from the Docker build context for now; the container is not used to run the test suite in Phase 1. Tests run locally / in CI via `pytest`, outside the image.

**Consequences:** Keeps the Phase 1 image minimal and the `CMD` honest (it isn't pretending to run tests it can't meaningfully run yet). **Revisit at Phase 2**: once ingestion + chunking have real unit tests, decide whether the image should run them internally (`CMD ["pytest", "tests/unit"]`) for CI parity, which would mean un-ignoring `tests/` in `.dockerignore` and copying it in the `Dockerfile`.

**Reference:** Repo structure `tests/unit/`; SRS §41.1 (test coverage isn't itself an SRS requirement but supports FR-02/FR-03's acceptance criteria, which rely on spot-checks and schema validation).

---

## D6 — `docker-compose.yml` stays single-service through Phase 1; the SRS's "DB container" language is not acted on yet

**Context:** SRS §40 (Deployment Architecture) describes the eventual deployment as "one container for the application/UI process, one for the metadata database." SRS §39 simultaneously sets **SQLite** (file-based, no server process) as the default metadata DB, and marks PostgreSQL "optional, for a larger deployment only." A file-based SQLite DB doesn't actually need its own container.

**Decision:** `docker-compose.yml` remains a single `app` service through Phase 1 (and, provisionally, through Phase 2's SQLite introduction — a mounted volume covers SQLite's needs, not a second container). A second service is added **only if** the project later switches to PostgreSQL, not as a default step.

**Consequences:** This is flagged explicitly so §40's "two containers" language isn't treated as a checklist item to satisfy literally — doing so with SQLite would mean building a database container with nothing running in it. If PostgreSQL is ever adopted (only under §39's "larger deployment" condition), `docker-compose.yml` gains a `db` service at that point, not before.

**Reference:** SRS §39 (SQLite default, Postgres optional), §40 (deployment description written at a level of generality that assumes a server-based DB).

---

## D7 — Corpus licensing confirmation is a blocking pre-Phase-1 dependency, not a Phase 1 task

**Context:** RAD §8 (Feasibility Analysis) and §15 (Approval/Sign-off) both flag corpus licensing (Constitution text + Supreme Court judgment source) as the single external, unresolved dependency, explicitly called out as **blocking Phase 1 start**, not something Phase 1 code resolves.

**Decision:** No ingestion script in this scaffold assumes a specific judgment source (e.g., a named repository or API) is confirmed licensable. `data/raw/judgments/` is created as an empty target directory; populating it is gated on that confirmation happening first, outside the codebase.

**Consequences:** Ingestion code (Phase 1, next step) should be written source-agnostic where reasonable (accepts a directory of PDFs) rather than hardcoding a scraper against a specific site, so the licensing decision doesn't force a rewrite of the ingestion pipeline once made.

**Reference:** RAD §8 (Feasibility Analysis — Data feasibility), §13 (Assumptions and Constraints), §15 (blocking item); SRS §18, §38.

---

## D8 — Source metadata comes from a manifest; FR-02 is delivered in two parts

**Context:** FR-02 asks the parser to extract text, section headings, article numbers, case name/court/date, page numbers, and source URL. Title, canonical source URL, and source date are generally not recoverable from a judgment or bare-act PDF itself (PDF `/Title` metadata is usually absent or wrong), yet `DocumentMetadata` requires them and NFR-03 depends on `source_url`. D7 also requires ingestion to stay source-agnostic.

**Decision:** Ingestion is driven by `data/raw/manifest.yaml` (format: `data/raw/manifest.example.yaml`), listing each PDF with `source_type`, `title` (the case name, for judgments), `source_url`, and optional `source_date` (the judgment date, for judgments; "court" is Supreme Court by corpus definition, SRS §18). The parser extracts **text and page numbers** from the PDF. **Section headings and article numbers** — which require reading the text's structure — are *not* extracted yet.

**Consequences:** FR-02 is **partially** met: document-level fields via manifest, page-level text via parser. Structural field extraction (headings, article numbers) plus FR-02's 10-document spot-check remain open Phase 1 work. The manifest is also the natural place to record licensing provenance once D7 is resolved. A manifest error is fatal for the whole run (nothing is written), since it is configuration, not data.

**Reference:** SRS §21.1 (FR-02), §18, §24; D7.

---

## D9 — Document IDs are derived from (source_type, normalized source_url)

**Context:** FR-01 requires a unique `document_id`, and that re-registering a source "creates a new version, not a duplicate ID". The ID must therefore identify the *source*, not the *file bytes* — a re-OCR'd PDF of the same judgment must keep its ID.

**Decision:** `document_id = {CONST|AMEND|JUDG}-{first 12 hex of sha256("<source_type>|<normalized source_url>")}`. Normalization lower-cases scheme and host, drops the fragment and a trailing `/`; path and query are kept verbatim (they can be case-sensitive). Two manifest entries resolving to the same ID are rejected.

**Consequences:** IDs are deterministic across machines and re-runs with no ID counter to persist. Changing an entry's `source_url` (e.g. a new mirror) produces a *new* document, not a new version — the manifest example says so explicitly. IDs are opaque; human-readable identity lives in `title` and later in chunk-level `case_name`.

**Reference:** SRS §21.1 (FR-01), §24; NFR-07.

---

## D10 — Versioning rule: a new version only when the PDF bytes, title, or source_date change

**Context:** NFR-07 ("re-ingestion creates a new version") read literally would mint a new version every time the ingestion script is re-run on an unchanged corpus.

**Decision:** Re-ingesting an entry whose file SHA-256, `title`, and `source_date` all match its latest registered version is a no-op (`unchanged`). Any difference creates version N+1 under the same `document_id`; the previous row stays in `metadata.jsonl` with `replaced_by = "<document_id>@v<N+1>"`, and its pages stay in `documents.jsonl`. The file hash is stored on `ParsedDocument.file_sha256` rather than on `DocumentMetadata`, which keeps the SRS §24 mirror exact.

**Consequences:** Ingestion is idempotent. Both output files are rewritten atomically (temp file + `os.replace`) only after all entries are processed; `documents.jsonl` rows whose version is missing from the registry (only possible after an interrupted run) are dropped on the next write, so version numbers are never reused for different content. `replaced_by` is the one field updated on an existing row — it records supersession, not a change to the ingested content. This is the D4 carry-over task, implemented for JSONL; it must be re-verified when ported to SQLite.

**Reference:** SRS §21.1 (FR-01), §24 (`replaced_by`), NFR-07, UC-6; D4.

---

## D11 — Output layout and page representation

**Context:** D4 names `documents.jsonl` and `metadata.jsonl` without fixing what each holds. Page-level output also needs a model, but it embeds `Provenance`, and `provenance.py` imports `models.py`, so it cannot live in `models.py`.

**Decision:** `metadata.jsonl` = the SRS §24 `documents` table (`DocumentMetadata`, one row per version). `documents.jsonl` = one `ParsedDocument` per document version, each containing ordered `ParsedPage`s. Both models live in a new `common/pages.py` (shared with Phase 2 chunking). Each page carries its full `Provenance` (so a page is traceable on its own), `page_number` is the **1-based physical page index**, and the PDF's printed page label (e.g. law-report pagination), when the PDF defines one, is kept separately as `page_label`. Pages with no text are kept (`is_empty=True`) so numbering never shifts. `ParsedDocument` validation rejects pages that point to another document/version or are not numbered exactly 1..N. The only text normalization is ligature expansion ("ﬁ" → "fi"), because PyMuPDF's default keeps ligature glyphs, which would break exact-term matching for BM25 (FR-06).

**Consequences:** Phase 2 chunking reads `documents.jsonl` via `ingestion.pipeline.load_parsed_documents()` and derives chunk provenance with `provenance_from_document`-style copying plus the page number. Printed labels are only as good as the PDF's own label metadata; most judgment PDFs will have none.

**Reference:** SRS §24, NFR-03, FR-06, FR-12; D4.

---

## D12 — Ingestion failure policy

**Context:** Real corpora contain missing files, corrupt or password-protected PDFs, and scanned judgments with no text layer. One bad file should not block a 50-document ingestion, and a document that yields nothing usable must not be registered silently.

**Decision:** Per-document failures are reported, not raised: missing file (`MissingPDFError`), not a PDF / corrupt / password-protected (`UnreadablePDFError`), zero pages (`EmptyPDFError`), and no text on *any* page (`NoExtractableTextError` — OCR is out of Phase 1 scope). Failed documents leave no rows in either output file. Individual pages with no text, or a page whose extraction raises, are kept as empty pages (the latter with `extraction_error`) and logged as warnings. `scripts/ingest_corpus.py` exits 0 (all ok), 1 (some documents failed; the rest were written), or 2 (config/manifest/registry error; nothing written).

**Consequences:** Scanned judgments are rejected loudly instead of entering the corpus as empty documents; if they turn out to matter for coverage, OCR becomes an explicit future decision (see open decisions). A PDF with a few image-only pages *is* ingested — its empty pages are listed in the run summary for manual review.

**Reference:** SRS §37.1 Risk #2 (OCR/PDF parsing errors), NFR-02, NFR-06.

---

## D13 — Tests are isolated from the developer's `.env` and working directory

**Context:** `load_settings()` calls `load_dotenv()`, which finds the repo `.env` (which `docker-compose.yml` requires to exist) and writes it into `os.environ` for the rest of the test process. A developer `.env` with `LOG_LEVEL=DEBUG` failed `test_config`; running `pytest` from outside the repo root failed three config tests because of repo-relative paths.

**Decision:** `tests/conftest.py` adds an autouse fixture that `chdir`s to the repo root, unsets the documented override variables, and disables `.env` loading for in-process tests. The CLI subprocess test sets the override variables explicitly instead.

**Consequences:** The suite passes regardless of local `.env` contents or invocation directory. Production behaviour of `load_settings()` is unchanged.

**Reference:** `.env.example`, `common/config.py`; D5.

---

## D14 — `source_date` is required in the manifest for judgments and constitutional text (refines D8)

**Context:** The corpus deliberately mixes eras: a 1973 judgment such as Kesavananda Bharati sits beside the Constitution as it reads today. The text a judgment interpreted can differ from the current text — e.g. the right to property was a fundamental right under Articles 19(1)(f) and 31 until the 44th Amendment (1978) omitted both and moved it to Article 300A. Automatic good-law / precedent-status detection is out of scope (SRS §11, §44), so the system's only defence against blending eras is keeping each source's date visible in its provenance. Under D8, `source_date` was optional, so a judgment could enter the corpus with no date at all.

**Decision:** `ManifestEntry` rejects a `judgment` without `source_date` (the judgment date) and a `constitutional_text` without `source_date` (the "as on" date of the text; the title should state it too, e.g. "The Constitution of India (as on YYYY-MM-DD)"). `amendment` entries keep it optional, since that was the agreed scope; it can be tightened the same way if amendment material turns out to need it. `DocumentMetadata.source_date` stays nullable, because it mirrors SRS §24 exactly — the rule lives at the manifest (input) boundary, not in the shared schema.

**Consequences:** An undated judgment or constitution entry fails the whole manifest (exit code 2, nothing written), with an error naming the entry — consistent with D8's "manifest errors are fatal". Documents already ingested without a date get a new version the first time their manifest entry gains one (D10 treats a `source_date` change as a metadata change). Also clarifies a point easy to misread: `document_version` (D10) tracks re-ingestion of the *same source file*, not the legal history of the Constitution; historical states of the text are represented by `amendment` documents, not by versions of the `constitutional_text` document. Later phases must carry the date forward: chunk provenance (Phase 2), dated citation display and date-attributed generation — "In X (1973), the Court held…" (Phase 5, FR-12) — and a few time-sensitive exact-reference questions in the V1 evaluation set (§33.1).

**Reference:** SRS §9 ("relevant amendment material"), §11, §18, §24, §28.2, §44; D8, D10.

---

## D15 — Corpus-generic ingestion: `corpus_id`, per-corpus storage, generic interface (refines D4, D8, D9)

**Context:** The SRS v4.2 amendment (`docs/SRS_v4.2_AMENDMENT.md`) fixes V1 as a single curated corpus and moves user-uploaded documents and user collections to V2 (Phase 15). It also requires V1 ingestion to be generic enough that V2 needs no rewrite (FR-01a). The Phase 1 code was generic in its parser, but not in three places: output went to one flat `data/processed/documents.jsonl`, document IDs had no notion of which collection a document belongs to, and the only entry point was the curated manifest.

**Decision:**
1. **Corpus identity.** Every document belongs to exactly one corpus. `corpus_id` (a lower-case slug, validated because it is also a directory name) is a **required** field, with no default, on `DocumentMetadata`, `Provenance`, and `ParsedDocument`. A missing corpus fails loudly instead of defaulting into the curated corpus. V1 has one corpus, `CURATED_CORPUS_ID = "constitutional-core"`.
2. **IDs (refines D9):** `document_id` is derived from `(corpus_id, source_type, normalized source_url)`, so the same judgment in the curated corpus and in a V2 collection gets different IDs.
3. **Storage (refines D4):** `data/processed/<corpus_id>/{documents.jsonl, metadata.jsonl}`. Writing one corpus cannot rewrite another's files, so the curated snapshot that experiments A–D depend on (NFR-04) cannot be changed by V2 uploads. The same per-corpus rule is the Phase 3 contract for indices (FR-05a).
4. **Interface (refines D8):** document metadata is a generic `DocumentSpec`. The generic core is `ingest_documents([(path, spec), …], corpus_id, processed_root, base_dir)`, with `ingest_document(path, spec, corpus_id, processed_root)` as its single-file form (≈ `ingest(document_path, corpus_id)`). The manifest is now just the V1 *adapter*: `ManifestEntry = DocumentSpec + file`, and `ingest_corpus()` maps it onto the core.
5. **V1 exposure:** `scripts/ingest_corpus.py` always targets the curated corpus and has no `--corpus` option. The generic entry point exists for V2 and for tests; no V1 user-facing path reaches it.

**Consequences:** No V2 functionality is implemented. There is no upload path, no `user_document` source type, and no collection UI. The only behavioural change to Phase 1 is the output location. Any local `data/processed/` produced before this change has the old flat layout and no `corpus_id`: delete it and re-run ingestion, which takes seconds and is generated data (gitignored). Boundary tests pin the guarantees V2 relies on: another corpus written through the generic interface leaves the curated files byte-identical, IDs don't collide, and invalid corpus IDs are rejected before anything is written. The generic entry point is intentionally not a plugin system or abstract storage interface. It is one function with a corpus parameter.

**Reference:** SRS v4.2 amendment (FR-01a, FR-05a, FR-COL-01–06, §24 changes, conflicts C1–C6); SRS v4.1 §11, §24, NFR-04, NFR-07; D4, D8, D9, D14.

---

## D16 — Phase 2 chunking: structure-bounded, conservative detection, provenance on every chunk

**Context:** FR-03 asks for 400–600-token chunks with 50–100 overlap, "legal-aware", with full locational metadata, and no chunk above the configured maximum. Phase 1 output is page text only. The corpus mixes the Constitution (a very regular structure) with Supreme Court judgments whose formatting varies (SCR reports with reporter front matter and multiple opinions; modern eCourts judgments with their own headings). Judgment `section_type` classification (FR-JS-01) is V2.

**Decision:**
1. **Structure first, size second.** `chunking/structure.py` splits a document into sections, and `legal_chunker.py` packs each section separately. No chunk crosses a section boundary and overlap never spans two sections, so no chunk mixes two articles or two opinions.
2. **Constitution:** an article starts at a line of the form `N. Title.—`. The dash after the title separates body headings from table-of-contents entries, and article numbers must strictly increase (after footnote markers such as `1[21A.` are allowed). Also recognised: titles that wrap onto the next line, omitted articles `N. [Title.] Omitted …`, Parts and their titles, Chapters, subheadings (carried forward until the next subheading, Part or Chapter), the Preamble, top-level clauses `(1)`, `(2)`. After a SCHEDULE heading, article detection stops, because Schedule paragraphs use the same `N. Title.—` form. Text before the body is front matter.
3. **Judgments:** only explicit signals count. These are the "Judgment(s) of the Court … delivered" line, a spaced JUDGMENT/ORDER title, opinion author lines (`VERMA, CJI.`, `DELIVERED BY B.P. JEEVAN REDDY, J.`), and headings from a fixed vocabulary (Facts, Issues, Submissions, Analysis, Conclusion, HELD, …) that also pass layout checks. Headings are stored **verbatim** in `heading` and never mapped to a section_type category: that mapping is V2 (FR-JS-01). The text before the first opinion is labelled `division = front_matter`, so the SCR headnote ("HELD : …", written by the reporter) is never presented as the Court's own text. A document with no signals gets the unstructured fallback.
4. **Page furniture is the only text removed.** That means page numbers and "Page x of y" footers, uppercase running headers (OCR-tolerant: matched on their first 12 letters because OCR renders one header several ways, and removed anywhere on a page once seen at the edges of ≥3 pages), mixed-case lines repeated at page edges on ≥5 pages and ≥ half the pages, and lone SCR margin letters A–H. Everything else is kept verbatim: no de-hyphenation, and margin letters fused into a sentence ("C women in work places") are left in, because stripping them risks deleting real words.
5. **Tokens:** `count_tokens` counts words and punctuation marks. It is independent of any model, since no embedding model is chosen until Phase 3, and `ChunkingSettings` (`configs/v1.yaml`, default 500 target / 600 max / 75 overlap) is validated. A single line over the max is split by words.
6. **Chunk IDs:** `<document_id>@v<version>:<index>:<sha256(text)[:8]>`. These are deterministic, include the version, and change visibly if the text changes.
7. **Output:** `data/processed/<corpus_id>/chunks.jsonl`, regenerated atomically from the *current* version of each document. Superseded versions stay in `documents.jsonl` for provenance but aren't chunked, so retrieval can't return two versions of one source.
8. **FR-03 fields:** `page` → `page_start`/`page_end` (+ printed page labels when the PDF defines them). `case_name` is the judgment's manifest title. `article_number` is filled only for constitutional text. `paragraph_number` is always present and currently always null: SCR judgments aren't paragraph-numbered, and guessing from numbered lines would mislabel headnote points and guideline lists. There's no `case_id` because no `cases` table exists yet (§24). There's no `extraction_method` because Phase 1 doesn't record it yet (FR-02a, pending).
9. **File naming:** `ARCHITECTURE.md` reserves `chunking/section_detector.py` for V2 section_type tagging. V1 detection therefore lives in `structure.py`, so the V1/V2 boundary stays visible in the file layout.

**Consequences:** Chunks are only as structured as the source allows. Any document without signals (including amendment Acts, for now) is chunked by size at line boundaries (`chunking_method = fallback`), which is honest but coarse. Subheading carry-forward can go stale if a subheading line isn't recognised. Overlap is whole lines only, so it can be less than `overlap_tokens` when lines are long. The detectors were checked on synthetic bare-act and judgment fixtures and on two real SCR judgments; they haven't been run on the real Constitution PDF, which isn't in the corpus yet, and that run should be the first check once it is.

**Reference:** SRS v4.1 §21.1 (FR-03, FR-04), §19.2 (FR-JS-01 — V2), §24, NFR-03, NFR-04; SRS v4.2 amendment (FR-01a, corpus isolation); D4, D11, D15.

---

## Open decisions (not yet made — flagged for the next phase)

- **Embedding model choice** (BGE vs. alternative) — deferred to Phase 3 per D2; SRS §39 leaves it as "BGE or comparable."
- **LLM provider** (hosted vs. local Qwen/Llama/Gemma) — deferred to Phase 5; affects `.env.example`, which will need an API-key variable added at that point, not before.
- **OCR fallback implementation (now required by SRS v4.2 FR-02a, V1).** *Whether* to OCR is settled; *how* is open: an OCRmyPDF/Tesseract pre-processing step vs. OCR inside `pdf_parser.py`, plus per-page OCR-origin marking. Until then, image-only PDFs are still rejected with `NoExtractableTextError` (D12). Existing OCR text layers (e.g. the SCR scans checked so far) are already used.
- **Whether FastAPI is introduced at all** — per D3, contingent on whether the UI (Phase 7) ends up needing a separate backend process or can call pipeline functions directly from Streamlit.
