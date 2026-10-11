// Vercel build for the ConstitutionLens frontend (D33). No dependencies; Node 18+.
//
// The backend (FastAPI) runs elsewhere — e.g. on your laptop behind a tunnel — and the UI
// calls it cross-origin. This script copies the static UI and bakes in where the API lives:
//   - <meta name="api-base" content="API_BASE">       (read by js/api.js)
//   - a Content-Security-Policy <meta> whose connect-src allows exactly that origin
//
// Environment:
//   API_BASE   required. The backend's public URL, e.g. https://your-name.ngrok-free.app
//   OUT_DIR    optional output directory (default: vercel-dist)
//
// The backend must list this site's origin in CORS_ALLOWED_ORIGINS.

import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const staticDir = join(repo, "src", "constitutional_evidence_rag", "web", "static");
const outDir = resolve(repo, process.env.OUT_DIR || "vercel-dist");

function fail(message) {
  console.error(`vercel/build.mjs: ${message}`);
  process.exit(1);
}

const raw = (process.env.API_BASE || "").trim().replace(/\/+$/, "");
if (!raw) fail("set API_BASE to the backend's public URL (Vercel: Project Settings > Environment Variables).");
let url;
try {
  url = new URL(raw);
} catch {
  fail(`API_BASE is not a valid URL: ${JSON.stringify(raw)}`);
}
const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
if (url.protocol !== "https:" && !(url.protocol === "http:" && local)) {
  fail("API_BASE must use https:// (an HTTPS page cannot call a plain-HTTP API); http:// is allowed only for localhost testing.");
}
if (url.pathname !== "/" || url.search || url.hash) fail("API_BASE must be just the origin, e.g. https://example.ngrok-free.app");
const origin = url.origin;

if (!existsSync(join(staticDir, "index.html"))) fail(`static UI not found at ${staticDir}`);
rmSync(outDir, { recursive: true, force: true });
mkdirSync(outDir, { recursive: true });
cpSync(staticDir, join(outDir, "static"), { recursive: true });

const csp = [
  "default-src 'self'", "script-src 'self'", "style-src 'self'", "img-src 'self'",
  `connect-src 'self' ${origin}`, "font-src 'self'", "object-src 'none'", "base-uri 'none'", "form-action 'self'",
].join("; ");

let html = readFileSync(join(staticDir, "index.html"), "utf8");
const metaRe = /<meta name="api-base" content="[^"]*">/;
if (!metaRe.test(html)) fail('index.html has no <meta name="api-base" content="..."> tag');
html = html.replace(metaRe, `<meta name="api-base" content="${origin}">`);
html = html.replace('<meta charset="utf-8">', `<meta charset="utf-8">\n  <meta http-equiv="Content-Security-Policy" content="${csp}">`);
writeFileSync(join(outDir, "index.html"), html, "utf8");

console.log(`ConstitutionLens frontend built into ${outDir}\n  API: ${origin}\n  Remember: CORS_ALLOWED_ORIGINS on the backend must include this site's https:// origin.`);
