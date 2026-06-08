/**
 * local-ai.js — Ollama client with graceful fallback.
 *
 * Usage:
 *   import { isAvailable, suggestShortTitle, bestModel } from '../src/local-ai.js';
 *
 *   if (await isAvailable()) {
 *     const title = await suggestShortTitle(fullTitle);
 *   }
 */

const OLLAMA_BASE   = 'http://localhost:11434';
const TIMEOUT_MS    = 15_000;
const PREFERRED     = ['llama3.2', 'llama3', 'mistral', 'phi3', 'gemma2'];

// ── availability ─────────────────────────────────────────────────────────────

let _availableCache = null;   // null = not yet checked
let _modelCache     = null;

export async function isAvailable() {
  if (_availableCache !== null) return _availableCache;
  try {
    const res = await fetchWithTimeout(`${OLLAMA_BASE}/api/tags`, { method: 'GET' }, 3_000);
    _availableCache = res.ok;
    if (res.ok) {
      const data   = await res.json();
      const models = (data.models ?? []).map(m => m.name.split(':')[0].toLowerCase());
      _modelCache  = PREFERRED.find(p => models.includes(p)) ?? models[0] ?? null;
    }
  } catch {
    _availableCache = false;
  }
  return _availableCache;
}

export async function bestModel() {
  await isAvailable();
  return _modelCache;
}

// ── core generate ─────────────────────────────────────────────────────────────

async function generate(prompt, model) {
  const res = await fetchWithTimeout(`${OLLAMA_BASE}/api/generate`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      prompt,
      stream: false,
      options: { temperature: 0.2, num_predict: 24 },
    }),
  }, TIMEOUT_MS);
  if (!res.ok) throw new Error(`Ollama HTTP ${res.status}`);
  const data = await res.json();
  return (data.response ?? '').trim();
}

// ── public helpers ────────────────────────────────────────────────────────────

/**
 * Suggest a short title (≤4 words) for an ETSI standard.
 * Returns null if AI is unavailable or the call fails.
 *
 * @param {string} fullTitle  — e.g. "Electronic Signatures … Part 1: Building blocks"
 * @param {string} [hint]     — optional extra context, e.g. the ETSI number
 * @returns {Promise<string|null>}
 */
export async function suggestShortTitle(fullTitle, hint = '') {
  if (!(await isAvailable())) return null;
  const model = await bestModel();
  if (!model) return null;

  const context = hint ? `\nDocument: ${hint}` : '';
  const prompt  =
`You are a technical standards editor. Given the full title of an ETSI standard, \
respond with a short title of at most four words that captures its core topic.
Respond with ONLY the short title — no punctuation, no explanation, no quotes.${context}

Full title: "${fullTitle.replace(/"/g, "'")}"

Short title:`;

  try {
    const raw = await generate(prompt, model);
    // strip any accidental quotes / newlines the model may add
    const cleaned = raw.replace(/^["'\s]+|["'\s]+$/g, '').replace(/\n.*/s, '').trim();
    // sanity: must be 1–6 words (allow slight overcount, we trim in UI)
    const words = cleaned.split(/\s+/).filter(Boolean);
    if (words.length === 0 || words.length > 8) return null;
    return words.slice(0, 4).join(' ');
  } catch {
    return null;
  }
}

/**
 * Compare two title strings and return a human-readable inconsistency
 * description, or null if they are equivalent enough.
 */
export function detectTitleInconsistency(titleA, titleB, labelA = 'WorkItem', labelB = 'PDF') {
  if (!titleA || !titleB) return null;
  const norm = s => s.toLowerCase().replace(/[^a-z0-9]/g, ' ').replace(/\s+/g, ' ').trim();
  const nA = norm(titleA), nB = norm(titleB);
  if (nA === nB) return null;

  // Jaccard similarity on word sets
  const setA  = new Set(nA.split(' '));
  const setB  = new Set(nB.split(' '));
  const inter = [...setA].filter(w => setB.has(w)).length;
  const union = new Set([...setA, ...setB]).size;
  const sim   = inter / union;

  if (sim >= 0.80) return null;   // close enough — likely just formatting
  return `${labelA}: "${titleA.slice(0, 80)}" vs ${labelB}: "${titleB.slice(0, 80)}"`;
}

// ── internal ──────────────────────────────────────────────────────────────────

function fetchWithTimeout(url, options, ms) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  return fetch(url, { ...options, signal: ctrl.signal }).finally(() => clearTimeout(timer));
}
