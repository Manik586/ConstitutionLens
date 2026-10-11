// ConstitutionLens API client (Phase 8). The only module that calls fetch().
// Every failure becomes an ApiError whose `message` is safe to show: it is either the
// server's {error, message} text (already user-safe by the API's contract) or a generic
// sentence chosen here. Raw response bodies are never surfaced.

const metaBase = document.querySelector('meta[name="api-base"]');
export const API_BASE = (metaBase && metaBase.content ? metaBase.content : "").replace(/\/$/, "");

// ngrok's free plan answers browser requests with a warning page unless this header is sent.
const NGROK = /\.ngrok(-free)?\.(app|dev|io)$/i.test((() => { try { return new URL(API_BASE).hostname; } catch { return ""; } })());

// Optional shared access token (D33). Kept for this tab by default; on this device only if the
// user asks. Wrapped in try/catch: storage can be unavailable.
const TOKEN_KEY = "constitutionlens.accessToken";
export function getAccessToken() {
  try { return sessionStorage.getItem(TOKEN_KEY) || localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}
export function setAccessToken(token, remember) {
  try {
    sessionStorage.setItem(TOKEN_KEY, token);
    if (remember) localStorage.setItem(TOKEN_KEY, token); else localStorage.removeItem(TOKEN_KEY);
  } catch { /* storage unavailable: the token lasts until the page is reloaded */ }
  memoryToken = token;
}
export function clearAccessToken() {
  memoryToken = "";
  try { sessionStorage.removeItem(TOKEN_KEY); localStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
}
let memoryToken = "";

// Called once per 401 so the UI can ask for a token; requests still fail with the 401 error.
let unauthorizedHandler = null;
export function onUnauthorized(handler) { unauthorizedHandler = handler; }

export class ApiError extends Error {
  constructor(message, { status = 0, code = null, network = false, aborted = false, timeout = false, body = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.network = network;
    this.aborted = aborted;
    this.timeout = timeout;
    this.body = body;
  }
}

function genericMessage(status) {
  if (status === 401) return "This server requires an access token.";
  if (status === 404) return "Not found. It may have been deleted; refresh the corpus list.";
  if (status === 409) return "That action conflicts with the corpus's current state.";
  if (status === 413) return "The upload is too large.";
  if (status === 422) return "The request was not accepted.";
  if (status === 503) return "The server is temporarily unable to handle this request.";
  if (status >= 500) return "The server had a problem handling this request.";
  return `The request failed (HTTP ${status}).`;
}

// Merge an optional caller signal with an optional timeout into one AbortSignal.
function combinedSignal(signal, timeoutMs) {
  const controller = new AbortController();
  let timedOut = false;
  let timer = null;
  const onAbort = () => controller.abort();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", onAbort, { once: true });
  }
  if (timeoutMs) timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  return {
    signal: controller.signal,
    timedOut: () => timedOut,
    done: () => { clearTimeout(timer); if (signal) signal.removeEventListener("abort", onAbort); },
  };
}

async function request(method, path, { json, form, signal, timeoutMs, acceptBodyOnError = false } = {}) {
  const init = { method, headers: { Accept: "application/json" } };
  const token = memoryToken || getAccessToken();
  if (token) init.headers["X-Access-Token"] = token;
  if (NGROK) init.headers["ngrok-skip-browser-warning"] = "1";
  if (json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  } else if (form) {
    init.body = form; // the browser sets the multipart boundary; never set Content-Type by hand
  }
  const guard = combinedSignal(signal, timeoutMs);
  init.signal = guard.signal;

  let response;
  try {
    response = await fetch(API_BASE + path, init);
  } catch (err) {
    guard.done();
    if (guard.timedOut()) throw new ApiError("The request timed out. The server may still be working; try again shortly.", { timeout: true });
    if (err && err.name === "AbortError") throw new ApiError("Cancelled.", { aborted: true });
    throw new ApiError("Cannot reach the server. Check that it is running and that you are online.", { network: true });
  }

  let body = null;
  try {
    if (response.status !== 204) {
      const type = response.headers.get("content-type") || "";
      if (type.includes("application/json")) body = await response.json();
    }
  } catch {
    body = null; // unreadable body: fall back to a generic message below
  } finally {
    guard.done();
  }

  if (response.ok) return { status: response.status, data: body };
  if (acceptBodyOnError && body && typeof body === "object" && !("error" in body)) return { status: response.status, data: body };
  if (response.status === 401 && unauthorizedHandler) unauthorizedHandler();
  const message = body && typeof body.message === "string" && body.message ? body.message : genericMessage(response.status);
  throw new ApiError(message, { status: response.status, code: body && typeof body.error === "string" ? body.error : null, body });
}

const enc = encodeURIComponent;

export const api = {
  health: () => request("GET", "/health").then((r) => r.data),
  config: () => request("GET", "/config").then((r) => r.data),
  listCorpora: () => request("GET", "/corpora").then((r) => r.data.corpora),
  getCorpus: (id, { signal } = {}) => request("GET", `/corpora/${enc(id)}`, { signal, timeoutMs: 15000 }).then((r) => r.data),
  createCorpus: (corpus_id, name) => request("POST", "/corpora", { json: name ? { corpus_id, name } : { corpus_id } }).then((r) => r.data),
  deleteCorpus: (id) => request("DELETE", `/corpora/${enc(id)}`).then(() => true),
  // 413 (every file too large) still carries per-file results, so its body is returned, not thrown.
  uploadDocuments: (id, files) => {
    const form = new FormData();
    for (const f of files) form.append("files", f, f.name);
    return request("POST", `/corpora/${enc(id)}/documents`, { form, acceptBodyOnError: true }).then((r) => r.data);
  },
  startBuild: (id) => request("POST", `/corpora/${enc(id)}/build`).then((r) => r.data),
  query: (id, body, { signal, timeoutMs } = {}) =>
    request("POST", `/corpora/${enc(id)}/query`, { json: body, signal, timeoutMs }).then((r) => r.data),
};
