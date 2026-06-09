/**
 * local-ai.js — Multi-provider local AI client.
 *
 * Supported providers (checked in order):
 *   1. LM Studio  http://localhost:1234  (OpenAI-compatible, runs MLX models on Apple Silicon)
 *   2. Ollama     http://localhost:11434 (native Ollama API)
 *
 * The first available provider wins.  Both are checked in parallel so startup
 * is fast even if one is not running.
 *
 * Usage:
 *   import { isAvailable, suggestShortTitle, bestModel, activeProvider }
 *     from '../src/local-ai.js';
 *
 *   if (await isAvailable()) {
 *     console.log(await activeProvider());   // 'lmstudio' | 'ollama'
 *     const title = await suggestShortTitle(fullTitle);
 *   }
 */

import dotenv from 'dotenv';
import path   from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(__dirname, '..', '.env') });

// ── config ────────────────────────────────────────────────────────────────────

const TIMEOUT_MS = 15_000;
const PROBE_MS   =  3_000;

// LM Studio — OpenAI-compatible REST API
const LMS_BASE = process.env.LMSTUDIO_BASE_URL ?? 'http://localhost:1234';

// Ollama — native API
const OLLAMA_BASE      = process.env.OLLAMA_BASE_URL ?? 'http://localhost:11434';
const OLLAMA_PREFERRED = ['llama3.2', 'llama3', 'mistral', 'phi3', 'gemma2'];

// ── state (module-level cache, reset per process) ─────────────────────────────

/** @type {null | 'lmstudio' | 'ollama' | false} */
let _provider    = null;   // null = not yet probed
let _model       = null;

// ── provider detection ────────────────────────────────────────────────────────

async function probeLmStudio() {
  try {
    const res = await fetchWithTimeout(`${LMS_BASE}/v1/models`, { method: 'GET' }, PROBE_MS);
    if (!res.ok) return null;
    const data   = await res.json();
    const models = (data.data ?? []).map(m => m.id);
    // LM Studio lists the currently loaded model; use the first available
    const model  = models[0] ?? null;
    return model ? { provider: 'lmstudio', model } : null;
  } catch {
    return null;
  }
}

async function probeOllama() {
  try {
    const res = await fetchWithTimeout(`${OLLAMA_BASE}/api/tags`, { method: 'GET' }, PROBE_MS);
    if (!res.ok) return null;
    const data   = await res.json();
    const models = (data.models ?? []).map(m => m.name.split(':')[0].toLowerCase());
    const model  = OLLAMA_PREFERRED.find(p => models.includes(p)) ?? models[0] ?? null;
    return model ? { provider: 'ollama', model } : null;
  } catch {
    return null;
  }
}

/**
 * Detect the first available local AI provider.
 * LM Studio is tried first (better for MLX / Apple Silicon).
 * Both probes run in parallel; whichever resolves first with a result wins.
 * Result is cached for the lifetime of the process.
 */
async function detect() {
  if (_provider !== null) return _provider !== false;

  // Race: first successful probe wins
  const [lms, ollama] = await Promise.all([probeLmStudio(), probeOllama()]);

  // Priority: LM Studio > Ollama
  const winner = lms ?? ollama ?? null;
  if (winner) {
    _provider = winner.provider;
    _model    = winner.model;
  } else {
    _provider = false;
  }
  return _provider !== false;
}

// ── public API ────────────────────────────────────────────────────────────────

/** Returns true if any local AI provider is reachable. */
export async function isAvailable() {
  return detect();
}

/** Returns the model identifier selected during detection, or null. */
export async function bestModel() {
  await detect();
  return _model;
}

/** Returns 'lmstudio', 'ollama', or null. */
export async function activeProvider() {
  await detect();
  return _provider || null;
}

// ── core generate ─────────────────────────────────────────────────────────────

async function generateLmStudio(prompt, model) {
  const res = await fetchWithTimeout(`${LMS_BASE}/v1/chat/completions`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      messages: [{ role: 'user', content: prompt }],
      temperature:  0.2,
      max_tokens:   24,
      stream:       false,
    }),
  }, TIMEOUT_MS);
  if (!res.ok) throw new Error(`LM Studio HTTP ${res.status}`);
  const data = await res.json();
  return (data.choices?.[0]?.message?.content ?? '').trim();
}

async function generateOllama(prompt, model) {
  const res = await fetchWithTimeout(`${OLLAMA_BASE}/api/generate`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      prompt,
      stream:  false,
      options: { temperature: 0.2, num_predict: 24 },
    }),
  }, TIMEOUT_MS);
  if (!res.ok) throw new Error(`Ollama HTTP ${res.status}`);
  const data = await res.json();
  return (data.response ?? '').trim();
}

async function generate(prompt) {
  await detect();
  if (!_provider || !_model) throw new Error('No local AI provider available');
  return _provider === 'lmstudio'
    ? generateLmStudio(prompt, _model)
    : generateOllama(prompt, _model);
}

// ── public task helpers ───────────────────────────────────────────────────────

/**
 * Suggest a short title (≤4 words) for an ETSI standard.
 * Returns null if AI is unavailable or the response fails sanity checks.
 *
 * @param {string} fullTitle  — full ETSI document title
 * @param {string} [hint]     — optional context, e.g. the ETSI number
 * @returns {Promise<string|null>}
 */
export async function suggestShortTitle(fullTitle, hint = '') {
  if (!(await isAvailable())) return null;

  const context = hint ? `\nDocument: ${hint}` : '';
  const prompt =
`You are a technical standards editor. Given the full title of an ETSI standard, \
respond with a short title of at most four words that captures its core topic.
Respond with ONLY the short title — no punctuation, no explanation, no quotes.${context}

Full title: "${fullTitle.replace(/"/g, "'")}"

Short title:`;

  try {
    const raw     = await generate(prompt);
    const cleaned = raw.replace(/^["'\s]+|["'\s]+$/g, '').replace(/\n.*/s, '').trim();
    const words   = cleaned.split(/\s+/).filter(Boolean);
    if (words.length === 0 || words.length > 8) return null;
    return words.slice(0, 4).join(' ');
  } catch {
    return null;
  }
}

/**
 * Compare two title strings; return a human-readable inconsistency string
 * or null when the titles are close enough (Jaccard ≥ 0.80).
 */
export function detectTitleInconsistency(titleA, titleB, labelA = 'WorkItem', labelB = 'PDF') {
  if (!titleA || !titleB) return null;
  const norm  = s => s.toLowerCase().replace(/[^a-z0-9]/g, ' ').replace(/\s+/g, ' ').trim();
  const nA = norm(titleA), nB = norm(titleB);
  if (nA === nB) return null;

  const setA  = new Set(nA.split(' '));
  const setB  = new Set(nB.split(' '));
  const inter = [...setA].filter(w => setB.has(w)).length;
  const union = new Set([...setA, ...setB]).size;
  const sim   = inter / union;

  if (sim >= 0.80) return null;
  return `${labelA}: "${titleA.slice(0, 80)}" vs ${labelB}: "${titleB.slice(0, 80)}"`;
}

// ── internal ──────────────────────────────────────────────────────────────────

function fetchWithTimeout(url, options, ms) {
  const ctrl  = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  return fetch(url, { ...options, signal: ctrl.signal }).finally(() => clearTimeout(timer));
}
