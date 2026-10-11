// ConstitutionLens UI wiring (Phase 8). State lives in state.js, DOM building in render.js,
// HTTP in api.js, decorative motion in motion.js. The backend is always authoritative: after
// every create / upload / build / delete the affected corpus is re-fetched from the API.

import { api, clearAccessToken, getAccessToken, onUnauthorized, setAccessToken } from "./api.js";
import {
  POLL_MAX_FAILURES, buildQueryBody, isReserved, pollDelay, queryBlockReason, queryTimeoutMs, retryDelay,
  shouldKeepPolling, slugify, state, statusInfo,
} from "./state.js";
import * as R from "./render.js";
import * as M from "./motion.js";

const $ = (id) => document.getElementById(id);
const STORAGE_KEY = "constitutionlens.lastCorpus";

const ui = {
  corpusList: $("corpusList"), corpusListMsg: $("corpusListMsg"),
  newCorpusBtn: $("newCorpusBtn"), createForm: $("createForm"), newName: $("newName"), newId: $("newId"),
  createError: $("createError"), createSubmit: $("createSubmit"), createCancel: $("createCancel"),
  corpusTitle: $("corpusTitle"), corpusMeta: $("corpusMeta"), corpusBadge: $("corpusBadge"), deleteBtn: $("deleteBtn"),
  corpusError: $("corpusError"),
  uploadSection: $("uploadSection"), limitsText: $("limitsText"), dropzone: $("dropzone"), fileInput: $("fileInput"),
  uploadError: $("uploadError"), fileResults: $("fileResults"), buildBtn: $("buildBtn"), buildHint: $("buildHint"),
  statusCard: $("statusCard"), statusText: $("statusText"), statusSub: $("statusSub"),
  askForm: $("askForm"), question: $("question"), askBtn: $("askBtn"), topK: $("topK"),
  generateWrap: $("generateWrap"), generate: $("generate"), generateNote: $("generateNote"),
  askDisabledMsg: $("askDisabledMsg"), searching: $("searching"), cancelBtn: $("cancelBtn"), queryError: $("queryError"),
  results: $("results"), live: $("live"),
  deleteDialog: $("deleteDialog"), deleteName: $("deleteName"), deleteId: $("deleteId"),
};
const panels = {
  answerPanel: $("answerPanel"), answerStatus: $("answerStatus"), answerKind: $("answerKind"), notices: $("notices"),
  answerBody: $("answerBody"), evidenceList: $("evidenceList"), evidenceCount: $("evidenceCount"), checkBody: $("checkBody"),
};

// ------------------------------------------------------------------ helpers

function remember(id) { try { if (id) localStorage.setItem(STORAGE_KEY, id); else localStorage.removeItem(STORAGE_KEY); } catch { /* storage unavailable */ } }
function recall() { try { return localStorage.getItem(STORAGE_KEY); } catch { return null; } }

function announce(message) {
  R.setText(ui.live, "");
  window.setTimeout(() => R.setText(ui.live, message), 30);
}

// Corpus-level alert: an indexing failure from the API wins; otherwise the last action error
// (kept in state so re-renders do not wipe it); otherwise a config load problem.
function setCorpusMessage(message) {
  state.corpusMessage = message || "";
  renderCorpusAlert();
}

function renderCorpusAlert() {
  const corpus = selected();
  const failed = corpus && corpus.status === "failed" ? `Indexing failed: ${corpus.error || "no reason was recorded."}` : "";
  R.showAlert(ui.corpusError, [failed, state.corpusMessage, state.configError].filter(Boolean).join(" "));
}

function selected() {
  return state.corpora.find((c) => c.corpus_id === state.selectedId) || null;
}

function replaceCorpus(corpus) {
  const i = state.corpora.findIndex((c) => c.corpus_id === corpus.corpus_id);
  const previous = i >= 0 ? state.corpora[i] : null;
  if (i >= 0) state.corpora[i] = corpus; else state.corpora.push(corpus);
  return previous;
}

// ------------------------------------------------------------------ rendering

function renderList({ animate = false } = {}) {
  R.renderCorpusList(ui.corpusList, state.corpora, state.selectedId, state.config);
  if (!state.corpora.length) R.showAlert(ui.corpusListMsg, "No corpora yet. Create one to upload PDFs.");
  else ui.corpusListMsg.hidden = true;
  if (animate) M.listIn([...ui.corpusList.children]);
}

function renderMain() {
  const corpus = selected();
  const reserved = isReserved(corpus, state.config);

  if (!corpus) {
    R.setText(ui.corpusTitle, "No corpus selected");
    R.setText(ui.corpusMeta, "Choose a corpus from the list, or create a new one.");
    ui.corpusBadge.hidden = true;
    ui.deleteBtn.hidden = true;
    ui.uploadSection.hidden = true;
    ui.statusCard.hidden = true;
  } else {
    R.setText(ui.corpusTitle, corpus.name);
    R.setText(ui.corpusMeta, R.corpusMetaText(corpus, state.config));
    const before = ui.corpusBadge.dataset.status;
    R.updateBadge(ui.corpusBadge, corpus.status);
    if (before && before !== corpus.status) M.pulse(ui.corpusBadge);
    ui.deleteBtn.hidden = reserved; // the API refuses to delete reserved corpora; never offer it

    ui.uploadSection.hidden = reserved; // constitutional-core is query-only
    if (!reserved) renderBuildControls(corpus);

    ui.statusCard.hidden = corpus.status !== "processing";
    if (corpus.status === "processing") {
      R.setText(ui.statusText, "Processing documents…");
      if (!state.poll || !state.poll.failures) R.setText(ui.statusSub, "Reading, chunking and indexing. This page checks the status automatically.");
    }
  }
  renderCorpusAlert();
  renderAskState();
}

function renderBuildControls(corpus) {
  const busy = corpus.status === "processing";
  // document_count counts *indexed* documents (it changes only when a build finishes), so it
  // cannot tell whether uploads are waiting. Build stays available; the API refuses an empty corpus.
  ui.buildBtn.disabled = busy || ui.buildBtn.dataset.pending === "1";
  R.setText(ui.buildBtn, corpus.status === "ready" ? "Rebuild index" : "Build index");
  const hint = busy ? "Indexing in progress."
    : corpus.status === "uploaded" ? "Uploaded PDFs are not searchable until the index is built."
      : corpus.status === "failed" ? "Upload corrected PDFs if needed, then build again."
        : "The index is up to date. Adding documents requires a rebuild.";
  R.setText(ui.buildHint, hint);
  ui.fileInput.disabled = busy;
  ui.dropzone.classList.toggle("is-disabled", busy);
}

function renderAskState() {
  const reason = queryBlockReason(selected());
  ui.askBtn.disabled = Boolean(reason) || state.query.inFlight;
  R.setText(ui.askDisabledMsg, reason);
  ui.askDisabledMsg.hidden = !reason;
}

function renderConfig() {
  const cfg = state.config;
  R.setText(ui.limitsText, R.limitsText(cfg));
  R.clear(ui.topK);
  const k = (cfg && cfg.query && cfg.query.top_k) || { min: 1, max: 20, default: 6 };
  const values = [...new Set([3, 5, k.default, 8, 10, 15, k.max])].filter((v) => v >= k.min && v <= k.max).sort((a, b) => a - b);
  for (const v of values) ui.topK.append(R.el("option", { value: String(v), text: v === k.default ? `${v} (default)` : String(v) }));
  ui.topK.value = String(k.default);
  const generation = Boolean(cfg && cfg.generation_available);
  ui.generateWrap.hidden = !generation;
  ui.generateNote.hidden = generation;
  if (!generation) ui.generate.checked = false;
}

function clearResults() {
  ui.results.hidden = true;
  R.showAlert(ui.queryError, "");
}

// ------------------------------------------------------------------ corpora

async function loadCorpora({ animate = false } = {}) {
  try {
    state.corpora = await api.listCorpora();
  } catch (err) {
    R.showAlert(ui.corpusListMsg, `Could not load corpora: ${err.message}`);
    return false;
  }
  if (state.selectedId && !state.corpora.some((c) => c.corpus_id === state.selectedId)) {
    stopPolling();
    state.selectedId = null;
    remember(null);
    clearResults();
  }
  renderList({ animate });
  renderMain();
  return true;
}

function selectCorpus(id) {
  if (id === state.selectedId) return;
  stopPolling();
  if (state.query.controller) state.query.controller.abort(); // results belong to the old corpus
  state.selectedId = id;
  state.corpusMessage = "";
  remember(id);
  R.clear(ui.fileResults);
  R.showAlert(ui.uploadError, "");
  clearResults();
  renderList();
  renderMain();
  M.swapIn([document.querySelector(".corpus-head"), ui.uploadSection, ui.statusCard, $("askSection")].filter((n) => n && !n.hidden));
  const corpus = selected();
  if (!corpus) return;
  if (shouldKeepPolling(corpus.status)) { startPolling(corpus.corpus_id); return; }
  // The list may be stale: show the API's current status for the corpus just opened.
  refreshCorpus(corpus.corpus_id).then((fresh) => {
    if (fresh && state.selectedId === fresh.corpus.corpus_id && shouldKeepPolling(fresh.corpus.status) && !state.poll) {
      startPolling(fresh.corpus.corpus_id);
    }
  });
}

async function refreshCorpus(id) {
  try {
    const corpus = await api.getCorpus(id);
    const previous = replaceCorpus(corpus);
    renderList();
    renderMain();
    return { corpus, previous };
  } catch (err) {
    if (err.status === 404) await loadCorpora();
    else setCorpusMessage(`Could not refresh the corpus: ${err.message}`);
    return null;
  }
}

// ------------------------------------------------------------------ polling (§4.2)

function startPolling(corpusId) {
  stopPolling();
  state.poll = { corpusId, startedAt: Date.now(), failures: 0, timer: null, controller: null };
  schedulePoll(pollDelay(0));
}

function stopPolling() {
  const p = state.poll;
  if (!p) return;
  clearTimeout(p.timer);
  if (p.controller) p.controller.abort();
  state.poll = null;
}

function schedulePoll(ms) {
  const p = state.poll;
  if (!p) return;
  clearTimeout(p.timer);
  if (document.hidden) return; // resumed by the visibilitychange handler
  p.timer = setTimeout(() => pollOnce(p), ms);
}

async function pollOnce(p) {
  if (state.poll !== p) return;
  p.controller = new AbortController();
  try {
    const corpus = await api.getCorpus(p.corpusId, { signal: p.controller.signal });
    if (state.poll !== p) return;
    p.failures = 0;
    const previous = replaceCorpus(corpus);
    renderList();
    renderMain();
    if (shouldKeepPolling(corpus.status)) {
      schedulePoll(pollDelay(Date.now() - p.startedAt));
      return;
    }
    stopPolling();
    if (previous && previous.status !== corpus.status) {
      announce(corpus.status === "ready"
        ? `${corpus.name} is ready: ${R.corpusCounts(corpus)} indexed.`
        : `Indexing ${corpus.name} ${statusInfo(corpus.status).label.toLowerCase()}.`);
    }
  } catch (err) {
    if (state.poll !== p || err.aborted) return;
    if (err.status === 404) { stopPolling(); await loadCorpora(); return; }
    p.failures += 1;
    if (p.failures >= POLL_MAX_FAILURES) {
      stopPolling();
      R.setText(ui.statusSub, "");
      setCorpusMessage("Lost contact with the server while checking the indexing status. Indexing may still be running: reload the page or select the corpus again to check.");
      return;
    }
    R.setText(ui.statusSub, `Reconnecting… (attempt ${p.failures + 1} of ${POLL_MAX_FAILURES})`);
    schedulePoll(retryDelay(p.failures));
  }
}

document.addEventListener("visibilitychange", () => {
  if (state.poll && !document.hidden) schedulePoll(0);
  else if (state.poll) clearTimeout(state.poll.timer);
});

// ------------------------------------------------------------------ create

let idEdited = false;

function toggleCreate(open) {
  ui.createForm.hidden = !open;
  ui.newCorpusBtn.setAttribute("aria-expanded", String(open));
  if (open) {
    idEdited = false;
    ui.createForm.reset();
    R.showAlert(ui.createError, "");
    M.expand(ui.createForm);
    ui.newName.focus();
  } else {
    ui.newCorpusBtn.focus();
  }
}

ui.newCorpusBtn.addEventListener("click", () => toggleCreate(ui.createForm.hidden));
ui.createCancel.addEventListener("click", () => toggleCreate(false));
ui.createForm.addEventListener("keydown", (e) => { if (e.key === "Escape") toggleCreate(false); });
ui.newName.addEventListener("input", () => { if (!idEdited) ui.newId.value = slugify(ui.newName.value); });
ui.newId.addEventListener("input", () => { idEdited = true; });

ui.createForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = ui.newName.value.trim();
  const id = ui.newId.value.trim();
  if (!name || !id) { R.showAlert(ui.createError, "Enter a name and a corpus ID."); return; }
  ui.createSubmit.disabled = true;
  try {
    const created = await api.createCorpus(id, name);
    R.showAlert(ui.createError, "");
    ui.createForm.hidden = true;
    ui.newCorpusBtn.setAttribute("aria-expanded", "false");
    await loadCorpora();
    selectCorpus(created.corpus_id);
    announce(`Created ${created.name}. Upload PDFs next.`);
    ui.fileInput.focus();
  } catch (err) {
    R.showAlert(ui.createError, err.message);
  } finally {
    ui.createSubmit.disabled = false;
  }
});

ui.corpusList.addEventListener("click", (e) => {
  const item = e.target.closest(".corpus-item");
  if (item) selectCorpus(item.dataset.corpusId);
});

// ------------------------------------------------------------------ delete

ui.deleteBtn.addEventListener("click", () => {
  const corpus = selected();
  if (!corpus || isReserved(corpus, state.config)) return;
  R.setText(ui.deleteName, corpus.name);
  R.setText(ui.deleteId, corpus.corpus_id);
  ui.deleteDialog.returnValue = "";
  ui.deleteDialog.dataset.corpusId = corpus.corpus_id;
  ui.deleteDialog.showModal();
  M.dialogIn(ui.deleteDialog);
});

// The browser returns focus to the Delete button when the modal closes. The close event can
// arrive late (it is a queued task), so focus is only moved if it is still stranded: never
// pull it away from wherever the user has gone since.
function focusIfStranded(target) {
  const active = document.activeElement;
  if (!active || active === document.body || ui.deleteDialog.contains(active) || (active.hidden || active.offsetParent === null)) {
    target.focus();
  }
}

ui.deleteDialog.addEventListener("close", async () => {
  const id = ui.deleteDialog.dataset.corpusId;
  if (ui.deleteDialog.returnValue !== "confirm" || !id) { focusIfStranded(ui.deleteBtn); return; }
  try {
    await api.deleteCorpus(id);
    if (state.selectedId === id) { stopPolling(); state.selectedId = null; remember(null); clearResults(); }
    await loadCorpora();
    announce("Corpus deleted.");
    focusIfStranded(ui.newCorpusBtn); // the Delete button is gone with the corpus
  } catch (err) {
    await loadCorpora();
    setCorpusMessage(`Could not delete the corpus: ${err.message}`);
    focusIfStranded(ui.deleteBtn);
  }
});

// ------------------------------------------------------------------ upload and build

async function uploadFiles(fileList) {
  const corpus = selected();
  if (!corpus || isReserved(corpus, state.config) || corpus.status === "processing") return;
  const files = [...fileList];
  if (!files.length) return;
  R.showAlert(ui.uploadError, "");

  // Convenience pre-checks only; the server re-checks everything and is authoritative.
  const limits = state.config && state.config.uploads;
  const maxBytes = limits ? limits.max_file_size_mb * 1024 * 1024 : Infinity;
  const exts = limits ? limits.allowed_extensions.map((x) => x.toLowerCase()) : [".pdf"];
  const local = [];
  const send = [];
  for (const f of files) {
    const ext = f.name.includes(".") ? f.name.slice(f.name.lastIndexOf(".")).toLowerCase() : "";
    if (!exts.includes(ext)) local.push({ filename: f.name, status: "rejected", reason: `Not uploaded: only ${exts.join(", ")} files are accepted.` });
    else if (f.size > maxBytes) local.push({ filename: f.name, status: "rejected", reason: `Not uploaded: larger than ${limits.max_file_size_mb} MB.` });
    else send.push(f);
  }
  if (!send.length) { R.renderFileResults(ui.fileResults, local); M.listIn([...ui.fileResults.children]); return; }

  R.renderPendingFiles(ui.fileResults, send);
  ui.fileInput.disabled = true;
  try {
    const result = await api.uploadDocuments(corpus.corpus_id, send);
    R.renderFileResults(ui.fileResults, [...(result.files || []), ...local]);
    M.listIn([...ui.fileResults.children]);
    const accepted = (result.files || []).filter((f) => f.status === "accepted").length;
    const rejected = (result.files || []).filter((f) => f.status === "rejected").length + local.length;
    announce(`${accepted} added, ${rejected} rejected.${accepted ? " Build the index to make them searchable." : ""}`);
  } catch (err) {
    R.clear(ui.fileResults);
    R.showAlert(ui.uploadError, `Upload failed: ${err.message}`);
  } finally {
    ui.fileInput.disabled = false;
    await refreshCorpus(corpus.corpus_id);
  }
}

ui.fileInput.addEventListener("change", () => { uploadFiles(ui.fileInput.files); ui.fileInput.value = ""; });
["dragenter", "dragover"].forEach((t) => ui.dropzone.addEventListener(t, (e) => { e.preventDefault(); ui.dropzone.classList.add("is-over"); }));
["dragleave", "drop"].forEach((t) => ui.dropzone.addEventListener(t, (e) => { e.preventDefault(); ui.dropzone.classList.remove("is-over"); }));
ui.dropzone.addEventListener("drop", (e) => uploadFiles(e.dataTransfer.files));
// A PDF dropped outside the drop zone must not navigate away from the app.
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());

ui.buildBtn.addEventListener("click", async () => {
  const corpus = selected();
  if (!corpus) return;
  ui.buildBtn.dataset.pending = "1";
  ui.buildBtn.disabled = true;
  setCorpusMessage("");
  try {
    await api.startBuild(corpus.corpus_id);
  } catch (err) {
    setCorpusMessage(`Could not start indexing: ${err.message}`);
  } finally {
    delete ui.buildBtn.dataset.pending;
  }
  const refreshed = await refreshCorpus(corpus.corpus_id);
  if (refreshed && shouldKeepPolling(refreshed.corpus.status)) {
    announce("Indexing started.");
    startPolling(corpus.corpus_id);
  }
});

// ------------------------------------------------------------------ query

function setQueryRunning(running) {
  state.query.inFlight = running;
  ui.searching.hidden = !running;
  ui.cancelBtn.hidden = !running;
  ui.askForm.setAttribute("aria-busy", String(running));
  renderAskState();
}

ui.askForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (state.query.inFlight) return; // duplicate submits are ignored
  const corpus = selected();
  const blocked = queryBlockReason(corpus);
  if (blocked) { R.showAlert(ui.queryError, blocked); return; }
  const question = ui.question.value.trim();
  if (!question) { R.showAlert(ui.queryError, "Type a question first."); ui.question.focus(); return; }

  const body = buildQueryBody(question, { topK: ui.topK.value, generate: ui.generate.checked, config: state.config });
  const controller = new AbortController();
  state.query.controller = controller;
  R.showAlert(ui.queryError, "");
  ui.results.hidden = true;
  setQueryRunning(true);
  try {
    const answer = await api.query(corpus.corpus_id, body, { signal: controller.signal, timeoutMs: queryTimeoutMs(state.config) });
    if (state.query.controller !== controller || state.selectedId !== corpus.corpus_id) return;
    showAnswer(answer);
  } catch (err) {
    if (err.aborted) { if (state.selectedId === corpus.corpus_id) announce("Query cancelled."); return; }
    R.showAlert(ui.queryError, err.message);
    if (err.status === 409 || err.status === 503 || err.status === 404) await refreshCorpus(corpus.corpus_id);
  } finally {
    if (state.query.controller === controller) state.query.controller = null;
    setQueryRunning(false);
  }
});

ui.cancelBtn.addEventListener("click", () => { if (state.query.controller) state.query.controller.abort(); });

ui.question.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ui.askForm.requestSubmit(); }
});

function showAnswer(answer) {
  const map = R.renderAnswer(panels, answer);
  R.renderEvidence(panels, answer, new Set(map.values()));
  R.renderCheck(panels, answer);
  ui.results.hidden = false;
  M.revealPanels([panels.answerPanel, $("evidencePanel"), $("checkPanel")]);
  const label = panels.answerStatus.querySelector(".badge__label");
  const n = (answer.evidence || []).length;
  announce(`${label ? label.textContent : "Done"}. ${n} evidence item${n === 1 ? "" : "s"}.`);
}

// Citation buttons (answer and check sections) open and focus the matching evidence card.
ui.results.addEventListener("click", (e) => {
  const cite = e.target.closest(".cite");
  if (!cite) return;
  const card = document.getElementById(`evidence-${cite.dataset.evidenceId}`);
  if (!card) return;
  const details = card.querySelector("details");
  if (details) details.open = true;
  card.scrollIntoView({ block: "center", behavior: M.motionEnabled ? "smooth" : "auto" });
  card.focus({ preventScroll: true });
  M.flash(card);
});

// ------------------------------------------------------------------ boot

// ------------------------------------------------------------------ access token (D33)
// Only used when the server sets API_ACCESS_TOKEN: any 401 opens this dialog, and the app
// reloads its data once a token is entered. The token is never shown or logged.

const tokenUi = { dialog: $("tokenDialog"), input: $("tokenInput"), remember: $("tokenRemember"), error: $("tokenError") };
let tokenPrompt = null;

function askForToken(message) {
  if (tokenPrompt) return tokenPrompt;
  tokenPrompt = new Promise((resolve) => {
    R.showAlert(tokenUi.error, message);
    tokenUi.input.value = "";
    tokenUi.dialog.returnValue = "";
    tokenUi.dialog.addEventListener("close", () => {
      const value = tokenUi.input.value.trim();
      tokenUi.input.value = "";
      tokenPrompt = null;
      if (tokenUi.dialog.returnValue === "confirm" && value) {
        setAccessToken(value, tokenUi.remember.checked);
        resolve(true);
      } else {
        resolve(false);
      }
    }, { once: true });
    tokenUi.dialog.showModal();
    M.dialogIn(tokenUi.dialog);
  });
  return tokenPrompt;
}

onUnauthorized(() => {
  const hadToken = Boolean(getAccessToken());
  clearAccessToken();
  askForToken(hadToken ? "That token was not accepted. Check it and try again." : "").then((entered) => {
    if (entered) loadApp({ animate: false });
  });
});

async function loadApp({ animate }) {
  state.configError = "";
  try {
    state.config = await api.config();
  } catch (err) {
    state.config = null;
    state.configError = err.status === 401 ? "" : `Could not load the server settings: ${err.message} Upload limits and LLM options are unavailable.`;
  }
  renderConfig();
  const ok = await loadCorpora({ animate });
  if (!ok) return;
  const last = recall();
  const fallback = state.config && state.config.default_corpus_id;
  const pick = [last, fallback].find((id) => id && state.corpora.some((c) => c.corpus_id === id))
    || (state.corpora[0] && state.corpora[0].corpus_id);
  if (pick) selectCorpus(pick);
}

function boot() {
  M.intro();
  loadApp({ animate: true });
}

boot();
