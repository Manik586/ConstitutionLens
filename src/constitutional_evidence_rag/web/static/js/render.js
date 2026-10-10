// DOM rendering helpers. No network access and no HTML parsing: every piece of server-provided text is
// inserted with textContent / createTextNode, so markup in filenames, excerpts or answers
// is displayed as inert text.

import { answerKind, citationMap, isReserved, segmentStatement, splitValidation, statusInfo } from "./state.js";

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "hidden") node.hidden = true;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

export function setText(node, text) {
  node.textContent = text == null ? "" : String(text);
}

export function showAlert(node, message) {
  setText(node, message || "");
  node.hidden = !message;
}

// ------------------------------------------------------------------ corpora

export function badge(status, { small = false } = {}) {
  const info = statusInfo(status);
  return el("span", { class: `badge badge--${info.tone}${small ? " badge--small" : ""}`, dataset: { status: String(status) } },
    el("span", { class: "badge__icon", "aria-hidden": "true", text: info.icon }),
    el("span", { class: "badge__label", text: info.label }));
}

export function updateBadge(node, status) {
  const info = statusInfo(status);
  node.className = `badge badge--${info.tone}`;
  node.dataset.status = String(status);
  node.replaceChildren(el("span", { class: "badge__icon", "aria-hidden": "true", text: info.icon }),
    el("span", { class: "badge__label", text: info.label }));
  node.hidden = false;
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// The API's counts describe the built index, not files waiting to be indexed.
export function corpusCounts(corpus) {
  return `${plural(corpus.document_count, "indexed document")} · ${plural(corpus.chunk_count, "passage")}`;
}

export function formatDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime()) || d.getUTCFullYear() < 1971) return null;
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function renderCorpusList(listNode, corpora, selectedId, config) {
  clear(listNode);
  for (const c of corpora) {
    const selected = c.corpus_id === selectedId;
    const button = el("button", {
      type: "button", class: "corpus-item", dataset: { corpusId: c.corpus_id },
      "aria-current": selected ? "true" : null,
    },
    el("span", { class: "corpus-item__name", text: c.name }),
    badge(c.status, { small: true }),
    el("span", { class: "corpus-item__meta", text: isReserved(c, config) ? `Built-in · query only · ${corpusCounts(c)}` : corpusCounts(c) }));
    listNode.append(el("li", {}, button));
  }
}

export function corpusMetaText(corpus, config) {
  const parts = [isReserved(corpus, config) ? "Built-in corpus · query only" : `ID ${corpus.corpus_id}`, corpusCounts(corpus)];
  const created = formatDate(corpus.created_at);
  if (created && !isReserved(corpus, config)) parts.push(`created ${created}`);
  return parts.join(" · ");
}

export function limitsText(config) {
  const u = config && config.uploads;
  if (!u) return "PDFs with selectable text only. Scanned PDFs without a text layer are not supported (no OCR).";
  return `${u.allowed_extensions.join(", ")} only · up to ${u.max_file_size_mb} MB per file, ${u.max_documents_per_corpus} documents `
    + `and ${u.max_total_size_mb} MB per corpus, ${u.max_pages_per_document} pages per document. `
    + "PDFs need selectable text: scanned PDFs without a text layer are not supported (no OCR).";
}

export function renderFileResults(listNode, files) {
  clear(listNode);
  for (const f of files) {
    const detail = f.status === "rejected" ? (f.reason || "Rejected.")
      : f.status === "unchanged" ? "Already in this corpus; not added again."
        : `Added${f.pages ? ` · ${plural(f.pages, "page")}` : ""}`;
    const icon = f.status === "rejected" ? "✕" : f.status === "unchanged" ? "=" : "✓";
    listNode.append(el("li", { class: `file-result file-result--${f.status}` },
      el("span", { class: "file-result__icon", "aria-hidden": "true", text: icon }),
      el("span", { class: "file-result__name", text: f.filename }),
      el("span", { class: "file-result__status", text: f.status }),
      el("span", { class: "file-result__detail", text: detail })));
  }
}

export function renderPendingFiles(listNode, files) {
  clear(listNode);
  for (const f of files) {
    listNode.append(el("li", { class: "file-result file-result--pending" },
      el("span", { class: "file-result__icon", "aria-hidden": "true", text: "…" }),
      el("span", { class: "file-result__name", text: f.name }),
      el("span", { class: "file-result__status", text: "uploading" })));
  }
}

// ------------------------------------------------------------------ answer

const SECTION_LABELS = {
  answer: null,
  constitutional_source: "Constitutional text",
  judicial_interpretation: "Judicial interpretation",
  other_evidence: "Other evidence",
};
const ANSWER_STATUS = {
  answered: { label: "Answered", icon: "✓", tone: "ok" },
  partial: { label: "Partial answer", icon: "◐", tone: "warn" },
  insufficient_evidence: { label: "Insufficient evidence", icon: "?", tone: "warn" },
  generation_failed: { label: "Generation failed", icon: "✕", tone: "bad" },
};
const SUPPORT = {
  supported: { label: "Supported", icon: "✓", tone: "ok" },
  contradicted: { label: "Contradicted", icon: "✕", tone: "bad" },
  insufficient: { label: "Insufficient support", icon: "?", tone: "warn" },
};
const ROLE_LABELS = { constitutional_text: "Constitutional text", judicial: "Judgment", user_document: "Uploaded document", other: "Source" };

function toneBadge(info, extraClass = "") {
  return el("span", { class: `badge badge--${info.tone} ${extraClass}`.trim() },
    el("span", { class: "badge__icon", "aria-hidden": "true", text: info.icon }),
    el("span", { class: "badge__label", text: info.label }));
}

function citeButton(key, evidenceId) {
  return el("button", { type: "button", class: "cite", dataset: { evidenceId }, "aria-label": `Show evidence ${evidenceId}` }, `[${key}]`);
}

function pageRange(chunk) {
  const a = chunk.page_label_start || chunk.page_start;
  const b = chunk.page_label_end || chunk.page_end;
  return a === b ? String(a) : `${a}–${b}`;
}

function pagesText(chunk) {
  const range = pageRange(chunk);
  return range.includes("–") ? `pages ${range}` : `page ${range}`;
}

export function renderAnswer(nodes, answer) {
  const map = citationMap(answer);
  const hasEvidence = (answer.evidence || []).length > 0;
  const status = ANSWER_STATUS[answer.status] || { label: answer.status, icon: "?", tone: "idle" };
  const kind = answerKind(answer);

  nodes.answerStatus.className = `badge badge--${status.tone}`;
  nodes.answerStatus.dataset.status = String(answer.status);
  nodes.answerStatus.replaceChildren(el("span", { class: "badge__icon", "aria-hidden": "true", text: status.icon }),
    el("span", { class: "badge__label", text: status.label }));
  setText(nodes.answerKind, kind === "generated"
    ? "Generated by the LLM from the evidence below; every statement cites the evidence it relies on."
    : "Quoted passages (no LLM): verbatim excerpts selected from the evidence below.");

  clear(nodes.notices);
  for (const n of answer.notices || []) nodes.notices.append(el("li", { class: "notice", text: n }));

  clear(nodes.answerBody);
  if (!hasEvidence) {
    nodes.answerBody.append(el("p", { class: "empty-state", text: "No relevant evidence was found in this corpus." }));
  } else if (!(answer.claims || []).length) {
    const text = answer.status === "generation_failed"
      ? "No answer was generated. The evidence that was retrieved is listed below."
      : "The retrieved evidence was not sufficient to answer this question. The closest passages are listed below.";
    nodes.answerBody.append(el("p", { class: "empty-state", text }));
  } else {
    let currentSection = null;
    let list = null;
    for (const claim of answer.claims) {
      if (claim.section !== currentSection) {
        currentSection = claim.section;
        const label = SECTION_LABELS[claim.section];
        if (label) nodes.answerBody.append(el("h4", { class: "answer-section", text: label }));
        list = el("ul", { class: "claims" });
        nodes.answerBody.append(list);
      }
      const p = el("p", { class: "claim__text" });
      for (const seg of segmentStatement(claim.statement, map)) {
        p.append(seg.type === "cite" ? citeButton(seg.key, seg.evidenceId) : document.createTextNode(seg.text));
      }
      if (claim.basis === "inference") p.append(" ", el("span", { class: "tag", title: "Synthesized from the cited evidence rather than stated in it", text: "inference" }));
      list.append(el("li", { class: "claim", dataset: { claimId: claim.claim_id } }, p));
    }
  }
  return map;
}

export function renderEvidence(nodes, answer, citedIds) {
  clear(nodes.evidenceList);
  const items = answer.evidence || [];
  setText(nodes.evidenceCount, items.length ? `${plural(items.length, "item")}` : "");
  if (!items.length) {
    nodes.evidenceList.append(el("li", { class: "empty-state", text: "No relevant evidence was found in this corpus." }));
    return;
  }
  for (const ev of items) {
    const chunk = ev.chunk || {};
    const cited = citedIds.has(ev.evidence_id);
    const locator = [chunk.article_number ? `Article ${chunk.article_number}` : null, pagesText(chunk)].filter(Boolean).join(" · ");
    const summary = el("summary", { class: "evidence__summary" },
      el("span", { class: "evidence__id", text: ev.evidence_id }),
      el("span", { class: "evidence__main" },
        el("span", { class: "evidence__title", text: ev.document_title }),
        el("span", { class: "evidence__loc", text: `${locator} · ${ROLE_LABELS[ev.role] || ev.role}` })),
      cited ? el("span", { class: "tag tag--cited", text: "cited" }) : null);
    const body = el("div", { class: "evidence__body" },
      el("dl", { class: "evidence__facts" },
        el("dt", { text: "Source" }), el("dd", { text: ev.document_title }),
        el("dt", { text: pageRange(chunk).includes("–") ? "Pages" : "Page" }), el("dd", { text: pageRange(chunk) }),
        el("dt", { text: "Corpus" }), el("dd", { text: chunk.corpus_id || "" })),
      el("blockquote", { class: "evidence__excerpt", text: chunk.text || "" }));
    if (/^https?:\/\//i.test(chunk.source_url || "")) {
      body.append(el("p", { class: "evidence__link" }, el("a", { href: chunk.source_url, target: "_blank", rel: "noopener noreferrer", text: "Original source" })));
    }
    nodes.evidenceList.append(el("li", { class: "evidence", id: `evidence-${ev.evidence_id}`, tabindex: "-1" },
      el("details", { class: "evidence__details" }, summary, body)));
  }
}

function checkItem(v) {
  const info = SUPPORT[v.status] || { label: v.status, icon: "?", tone: "idle" };
  const cites = el("span", { class: "check__cites" }, "Cites: ");
  (v.citation_ids || []).forEach((id, i) => {
    if (i) cites.append(" ");
    cites.append(citeButton(id, id));
  });
  if (v.supporting_ids && v.supporting_ids.length) {
    cites.append(el("span", { class: "check__supporting", text: ` · supported by ${v.supporting_ids.join(", ")}` }));
  }
  return el("li", { class: `check-item check-item--${v.status}`, dataset: { claimId: v.claim_id } },
    toneBadge(info, "check-item__badge"),
    el("div", { class: "check-item__body" },
      el("p", { class: "check-item__text", text: v.text }),
      el("p", { class: "check-item__reason", text: v.reason }),
      cites));
}

export function renderCheck(nodes, answer) {
  clear(nodes.checkBody);
  if (!answer.validation) {
    const text = answerKind(answer) === "extractive"
      ? "Not run: this answer consists of verbatim quotes, and the check applies to LLM-generated answers only."
      : "No evidence-support results were returned for this answer.";
    nodes.checkBody.append(el("p", { class: "empty-state", text }));
    return;
  }
  const { kept, removed } = splitValidation(answer.validation);
  const counts = { supported: 0, contradicted: 0, insufficient: 0 };
  for (const v of answer.validation.claims) if (v.status in counts) counts[v.status] += 1;
  nodes.checkBody.append(el("p", { class: "check__summary",
    text: `${answer.validation.claims.length} claims checked (${answer.validation.method}): ${counts.supported} supported, `
      + `${counts.insufficient} insufficient, ${counts.contradicted} contradicted.` }));

  nodes.checkBody.append(el("h4", { class: "check__group", text: `Kept in the answer (${kept.length})` }));
  nodes.checkBody.append(kept.length ? el("ol", { class: "check-list" }, kept.map(checkItem))
    : el("p", { class: "empty-state", text: "No claims were kept." }));

  nodes.checkBody.append(el("h4", { class: "check__group check__group--removed", text: `Removed or flagged (${removed.length})` }));
  if (removed.length) {
    nodes.checkBody.append(el("p", { class: "hint", text: "These claims were written by the model but are not shown in the answer above, so the answer may be shorter than what the model originally wrote." }));
    nodes.checkBody.append(el("ol", { class: "check-list check-list--removed" }, removed.map(checkItem)));
  } else {
    nodes.checkBody.append(el("p", { class: "empty-state", text: "No claims were removed." }));
  }
}
