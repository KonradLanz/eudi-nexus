/**
 * local-ai-core.js — Low-level generate() function extracted from local-ai.js.
 *
 * local-ai.js re-exports everything from here plus the public helpers
 * (suggestShortTitle etc.).  ai-tasks/* import only this file to avoid
 * circular dependencies.
 *
 * This module is intentionally minimal — just provider detection + one
 * generate() call.  All prompt engineering lives in the task modules.
 */

import dotenv from 'dotenv';
import path   from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(__dirname, '..', '.env') });

const TIMEOUT_MS = 60_000;   // generation can take a while on slow hardware
const PROBE_MS   =  3_000;

const LMS_BASE         = process.env.LMSTUDIO_BASE_URL  ?? 'http://localhost:1234';
const OLLAMA_BASE      = process.env.OLLAMA_BASE_URL    ?? 'http://localhost:11434';
const OLLAMA_PREFERRED = (process.env.OLLAMA_PREFERRED_MODELS ?? 'llama3.2,llama3,mistral,phi3,gemma2').split(',');

/** @type {null | 'lmstudio' | 'ollama' | false} */
let _provider = null;
let _model    = null;

async function probeLmStudio() {
  try {
    const res    = await fetchWithTimeout(`${LMS_BASE}/v1/models`, {}, PROBE_MS);
    if (!res.ok) return null;
    const models = (await res.json()).data ?? [];
    const model  = models[0]?.id ?? null;
    return model ? { provider: 'lmstudio', model } : null;
  } catch { return null; }
}

async function probeOllama() {
  try {
    const res    = await fetchWithTimeout(`${OLLAMA_BASE}/api/tags`, {}, PROBE_MS);
    if (!res.ok) return null;
    const names  = ((await res.json()).models ?? []).map(m => m.name.split(':')[0].toLowerCase());
    const model  = OLLAMA_PREFERRED.find(p => names.includes(p)) ?? names[0] ?? null;
    return model ? { provider: 'ollama', model } : null;
  } catch { return null; }
}

async function detect() {
  if (_provider !== null) return _provider !== false;
  const [lms, ollama] = await Promise.all([probeLmStudio(), probeOllama()]);
  const winner = lms ?? ollama ?? null;
  if (winner) { _provider = winner.provider; _model = winner.model; }
  else _provider = false;
  return _provider !== false;
}

export async function isAvailable()    { return detect(); }
export async function bestModel()      { await detect(); return _model; }
export async function activeProvider() { await detect(); return _provider || null; }

// ── Raw generation ────────────────────────────────────────────────────────────

export async function generate(prompt, opts = {}) {
  await detect();
  if (!_provider || !_model) throw new Error('No local AI provider available');

  const maxTokens = opts.maxTokens ?? 256;
  const temp      = opts.temperature ?? 0.2;

  if (_provider === 'lmstudio') {
    const res = await fetchWithTimeout(`${LMS_BASE}/v1/chat/completions`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model:       _model,
        messages:    [{ role: 'user', content: prompt }],
        temperature: temp,
        max_tokens:  maxTokens,
        stream:      false,
      }),
    }, TIMEOUT_MS);
    if (!res.ok) throw new Error(`LM Studio HTTP ${res.status}`);
    return ((await res.json()).choices?.[0]?.message?.content ?? '').trim();
  }

  // Ollama
  const res = await fetchWithTimeout(`${OLLAMA_BASE}/api/generate`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model:   _model,
      prompt,
      stream:  false,
      options: { temperature: temp, num_predict: maxTokens },
    }),
  }, TIMEOUT_MS);
  if (!res.ok) throw new Error(`Ollama HTTP ${res.status}`);
  return ((await res.json()).response ?? '').trim();
}

function fetchWithTimeout(url, options, ms) {
  const ctrl  = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  return fetch(url, { ...options, signal: ctrl.signal }).finally(() => clearTimeout(timer));
}
