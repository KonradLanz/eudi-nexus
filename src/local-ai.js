/**
 * local-ai.js — Multi-provider local AI client.
 *
 * Supported providers (checked in order):
 *   1. LM Studio  http://localhost:1234  (OpenAI-compatible)
 *   2. Ollama     http://localhost:11434 (native Ollama API)
 *
 * Configuration via .env:
 *   LMSTUDIO_BASE_URL=http://localhost:1234
 *   LMSTUDIO_MODEL=google/gemma-4-31b-qat       ← preferred model id (exact match)
 *   LMSTUDIO_CONTEXT=2048                        ← num_ctx passed to LM Studio
 *   LMSTUDIO_MAX_TOKENS=300                      ← max_tokens for completion (default 300)
 *                                                  Thinking models (Gemma 4 QAT) use
 *                                                  ~150-200 reasoning tokens before output —
 *                                                  set this high enough to leave room for output.
 *   OLLAMA_BASE_URL=http://localhost:11434
 *   OLLAMA_MODEL=llama3.2                        ← preferred Ollama model
 *
 * Model selection priority (LM Studio):
 *   1. LMSTUDIO_MODEL env var (exact id match against /v1/models list)
 *   2. First model whose id contains 'gemma-4'
 *   3. First model whose id contains 'gemma'
 *   4. First model in list
 */

import dotenv from 'dotenv';
import path   from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(__dirname, '..', '.env') });

// ── config ────────────────────────────────────────────────────────────────────

const TIMEOUT_MS = 60_000;  // raised: thinking models need more time
const PROBE_MS   =  3_000;

const LMS_BASE       = process.env.LMSTUDIO_BASE_URL  ?? 'http://localhost:1234';
const LMS_PREFERRED  = process.env.LMSTUDIO_MODEL     ?? null;   // exact model id
const LMS_CONTEXT    = parseInt(process.env.LMSTUDIO_CONTEXT    ?? '2048', 10);
const LMS_MAX_TOKENS = parseInt(process.env.LMSTUDIO_MAX_TOKENS ?? '300',  10);
// ^ 300 = ~166 reasoning tokens (Gemma 4 QAT typical) + headroom for output

const OLLAMA_BASE      = process.env.OLLAMA_BASE_URL ?? 'http://localhost:11434';
const OLLAMA_PREFERRED = process.env.OLLAMA_MODEL
  ? [process.env.OLLAMA_MODEL]
  : ['llama3.2', 'llama3', 'mistral', 'phi3', 'gemma2'];

// ── system prompt (shared across providers) ───────────────────────────────────

const SYSTEM_PROMPT =
`You are a technical standards editor specialising in ETSI and eIDAS standards.

Your task: given the full title of an ETSI standard, output a short title of
AT MOST FOUR WORDS that captures the document's core topic.

Rules:
- Maximum 4 words. Fewer is better.
- Use official abbreviations where widely known (e.g. TSP, PKI, eID, QSCD, PSD2).
- Never include the ETSI number, version, or "Part N" in the short title.
- Respond with ONLY the short title — no punctuation, no explanation, no quotes.
- Do not start with "A", "An", "The".

Examples:
  "Electronic Signatures and Infrastructures (ESI); Certificate Profiles" → Certificate Profiles ESI
  "Electronic Signatures and Infrastructures (ESI); Trust Service Provider (TSP) — Part 1" → TSP Conformity Assessment
  "Quantum-Safe Cryptography; Hybrid Key Exchange Mechanisms" → Quantum-Safe Hybrid KEM`;

// ── state ─────────────────────────────────────────────────────────────────────

/** @type {null | 'lmstudio' | 'ollama' | false} */
let _provider = null;
let _model    = null;

// ── provider detection ────────────────────────────────────────────────────────

function pickLmsModel(ids) {
  if (!ids.length) return null;
  // 1. exact env match
  if (LMS_PREFERRED && ids.includes(LMS_PREFERRED)) return LMS_PREFERRED;
  // 2. partial env match (case-insensitive)
  if (LMS_PREFERRED) {
    const pref = LMS_PREFERRED.toLowerCase();
    const hit  = ids.find(id => id.toLowerCase().includes(pref));
    if (hit) return hit;
  }
  // 3. prefer gemma-4 variants
  const g4 = ids.find(id => id.toLowerCase().includes('gemma-4'));
  if (g4) return g4;
  // 4. prefer any gemma
  const g  = ids.find(id => id.toLowerCase().includes('gemma'));
  if (g) return g;
  // 5. fallback: first in list
  return ids[0];
}

async function probeLmStudio() {
  try {
    const res = await fetchWithTimeout(`${LMS_BASE}/v1/models`, { method: 'GET' }, PROBE_MS);
    if (!res.ok) return null;
    const data  = await res.json();
    const ids   = (data.data ?? []).map(m => m.id);
    const model = pickLmsModel(ids);
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

async function detect() {
  if (_provider !== null) return _provider !== false;
  const [lms, ollama] = await Promise.all([probeLmStudio(), probeOllama()]);
  const winner = lms ?? ollama ?? null;
  if (winner) { _provider = winner.provider; _model = winner.model; }
  else        { _provider = false; }
  return _provider !== false;
}

// ── public API ────────────────────────────────────────────────────────────────

export async function isAvailable()   { return detect(); }
export async function bestModel()     { await detect(); return _model; }
export async function activeProvider(){ await detect(); return _provider || null; }

// ── core generate ─────────────────────────────────────────────────────────────

async function generateLmStudio(systemPrompt, userPrompt, model) {
  const body = {
    model,
    messages: [
      { role: 'system', content: systemPrompt },
      { role: 'user',   content: userPrompt   },
    ],
    temperature: 0.15,
    max_tokens:  LMS_MAX_TOKENS,
    stream:      false,
  };
  // Pass context window size if configured
  if (LMS_CONTEXT) body.num_ctx = LMS_CONTEXT;

  const res = await fetchWithTimeout(`${LMS_BASE}/v1/chat/completions`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  }, TIMEOUT_MS);
  if (!res.ok) throw new Error(`LM Studio HTTP ${res.status}`);
  const data = await res.json();
  return (data.choices?.[0]?.message?.content ?? '').trim();
}

async function generateOllama(systemPrompt, userPrompt, model) {
  const res = await fetchWithTimeout(`${OLLAMA_BASE}/api/generate`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      system:  systemPrompt,
      prompt:  userPrompt,
      stream:  false,
      options: { temperature: 0.15, num_predict: LMS_MAX_TOKENS, num_ctx: LMS_CONTEXT },
    }),
  }, TIMEOUT_MS);
  if (!res.ok) throw new Error(`Ollama HTTP ${res.status}`);
  const data = await res.json();
  return (data.response ?? '').trim();
}

async function generate(systemPrompt, userPrompt) {
  await detect();
  if (!_provider || !_model) throw new Error('No local AI provider available');
  return _provider === 'lmstudio'
    ? generateLmStudio(systemPrompt, userPrompt, _model)
    : generateOllama(systemPrompt, userPrompt, _model);
}

// ── public task helpers ───────────────────────────────────────────────────────

/**
 * Suggest a short title (≤4 words) for an ETSI standard.
 *
 * Thinking models (e.g. Gemma 4 QAT) emit reasoning tokens before the actual
 * answer. LM Studio surfaces these as <think>...</think> in the content field
 * or as invisible reasoning_tokens in usage. We strip any <think> block before
 * parsing the result.
 *
 * @param {string} fullTitle   — full ETSI document title
 * @param {string} [hint]      — optional context, e.g. ETSI number / scope
 * @returns {Promise<string|null>}
 */
export async function suggestShortTitle(fullTitle, hint = '') {
  if (!(await isAvailable())) return null;

  const context    = hint ? `\nDocument reference: ${hint}` : '';
  const userPrompt =
`Full title: "${fullTitle.replace(/"/g, "'")}"
Short title (max 4 words):${context}`;

  try {
    const raw = await generate(SYSTEM_PROMPT, userPrompt);

    // Strip <think>...</think> blocks emitted by thinking models (Gemma 4 QAT)
    const withoutThinking = raw.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();

    const cleaned = withoutThinking
      .split('\n')[0]
      .replace(/^[#*\-•>\s"']+/, '')
      .replace(/["'\s]+$/, '')
      .trim();

    const words = cleaned.split(/\s+/).filter(Boolean);
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
