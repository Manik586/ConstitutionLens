// App state and pure helpers (no DOM, no fetch) — easy to reason about and to test.

export const state = {
  config: null,          // GET /config
  configError: "",
  corpusMessage: "",     // last corpus-level action error (create/upload/build/delete/poll)
  corpora: [],           // GET /corpora, as returned
  selectedId: null,
  query: { controller: null, inFlight: false },
  poll: null,            // { corpusId, timer, startedAt, failures }
};

// Corpus status -> what the UI may do. Statuses come from the API; this only interprets them.
const STATUS_INFO = {
  ready: { label: "Ready", icon: "✓", tone: "ok", queryable: true, busy: false },
  processing: { label: "Processing", icon: "◔", tone: "busy", queryable: false, busy: true },
  uploaded: { label: "Not indexed", icon: "○", tone: "idle", queryable: false, busy: false },
  failed: { label: "Failed", icon: "✕", tone: "bad", queryable: false, busy: false },
};

export function statusInfo(status) {
  return STATUS_INFO[status] || { label: String(status || "unknown"), icon: "?", tone: "idle", queryable: false, busy: false };
}

export function isReserved(corpus, config) {
  if (!corpus) return false;
  return Boolean(corpus.reserved) || Boolean(config && (config.reserved_corpus_ids || []).includes(corpus.corpus_id));
}

// Why the question box is disabled, or "" when a query may be sent.
export function queryBlockReason(corpus) {
  if (!corpus) return "Select a corpus to ask a question.";
  switch (corpus.status) {
    case "ready": return "";
    case "processing": return "Indexing is in progress. You can ask questions once the corpus is ready.";
    case "uploaded": return "This corpus is not indexed yet: upload PDFs if needed, then build the index before asking questions.";
    case "failed": return "The last indexing attempt failed. Fix the documents and build again.";
    default: return "This corpus cannot be queried right now.";
  }
}

// Polling cadence (§4.2): every 2 s for the first 30 s, then every 5 s.
export const POLL_FAST_MS = 2000;
export const POLL_SLOW_MS = 5000;
export const POLL_FAST_WINDOW_MS = 30000;
export const POLL_MAX_FAILURES = 5;

export function pollDelay(elapsedMs) {
  return elapsedMs < POLL_FAST_WINDOW_MS ? POLL_FAST_MS : POLL_SLOW_MS;
}

// Backoff after consecutive network failures: 2 s, 4 s, 8 s, 15 s, 15 s.
export function retryDelay(failures) {
  return Math.min(POLL_FAST_MS * 2 ** Math.max(0, failures - 1), 15000);
}

export function shouldKeepPolling(status) {
  return status === "processing";
}

export function slugify(name) {
  return String(name || "")
    .toLowerCase()
    .normalize("NFKD").replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64)
    .replace(/-+$/g, "");
}

// Only parameters the API supports (QueryRequest: question, mode, top_k, generate).
export function buildQueryBody(question, { topK, generate, config }) {
  const q = (config && config.query) || {};
  const body = { question, mode: q.default_mode || "hybrid", generate: Boolean(generate && config && config.generation_available) };
  const k = Number(topK);
  if (Number.isInteger(k) && (!q.top_k || (k >= q.top_k.min && k <= q.top_k.max))) body.top_k = k;
  return body;
}

// Client timeout: slightly above the server's worst case for a generated answer.
export function queryTimeoutMs(config) {
  const seconds = (config && config.query && config.query.timeout_seconds) || 120;
  return Math.round((seconds + 15) * 1000);
}

// Citation marker text -> evidence_id, exactly as the Answer declares it.
// citation_style "evidence_id": statements cite [E1]; "number": statements cite [1].
export function citationMap(answer) {
  const map = new Map();
  const evidenceIds = new Set((answer.evidence || []).map((e) => e.evidence_id));
  for (const c of answer.citations || []) {
    const key = answer.citation_style === "evidence_id" ? c.evidence_id : String(c.citation_id);
    if (evidenceIds.has(c.evidence_id)) map.set(key, c.evidence_id);
  }
  return map;
}

// Split a statement into text and citation segments. Only markers present in `map` become
// citations; anything else stays literal text.
export function segmentStatement(text, map) {
  const out = [];
  const re = /\[(E?\d+)\]/g;
  let last = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (!map.has(m[1])) continue;
    if (m.index > last) out.push({ type: "text", text: text.slice(last, m.index) });
    out.push({ type: "cite", key: m[1], evidenceId: map.get(m[1]) });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ type: "text", text: text.slice(last) });
  return out;
}

export function splitValidation(validation) {
  const claims = (validation && validation.claims) || [];
  return { kept: claims.filter((c) => c.kept), removed: claims.filter((c) => !c.kept) };
}

export function answerKind(answer) {
  if (answer.validation || answer.citation_style === "evidence_id") return "generated";
  return "extractive";
}
