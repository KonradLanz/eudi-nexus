/**
 * ai-tasks/project-audit.js — POC: AI-assisted project structure audit.
 *
 * What it does:
 *   1. Scans a list of expected paths (files + dirs) and reports which exist.
 *   2. For small text files (< 8 KB) it reads the content and asks the local
 *      AI to give a one-sentence description of what the file does.
 *   3. Returns a structured JSON audit report.
 *
 * This is the simplest possible "local AI as dev helper" use case:
 * the AI does not need to be smart — it just needs to summarise tiny files
 * so a human (or another script) can get a quick overview without opening
 * every file manually.
 *
 * Usage (via local-ai-helper.js):
 *   node scripts/local-ai-helper.js audit
 *   node scripts/local-ai-helper.js audit --save   # writes audit.json
 */

import fs   from 'fs/promises';
import path from 'path';
import { generate } from '../local-ai-core.js';

const MAX_CONTENT_BYTES = 8_000;  // files larger than this are not sent to AI
const AI_TIMEOUT_MS     = 20_000;

// ── Expected project structure ────────────────────────────────────────────────
// Add or remove entries here to extend the audit.
const EXPECTED = [
  // directories
  { type: 'dir',  path: 'src' },
  { type: 'dir',  path: 'scripts' },
  { type: 'dir',  path: 'downloads' },
  { type: 'dir',  path: 'docs' },
  { type: 'dir',  path: 'test' },
  { type: 'dir',  path: 'downloads/specs' },
  { type: 'dir',  path: 'downloads/specs/_workitems' },
  { type: 'dir',  path: 'downloads/specs/_titles' },
  // key config / entry files
  { type: 'file', path: 'package.json',          describe: true },
  { type: 'file', path: '.env.example',           describe: true },
  { type: 'file', path: 'README.md',              describe: true },
  { type: 'file', path: 'src/local-ai.js',        describe: true },
  { type: 'file', path: 'src/etsi-client.js',     describe: true },
  { type: 'file', path: 'src/http-cache.js',      describe: true },
  { type: 'file', path: 'scripts/download-specs.js',  describe: false },
  { type: 'file', path: 'scripts/enrich-titles.js',   describe: false },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

async function exists(absPath) {
  return fs.stat(absPath).then(() => true).catch(() => false);
}

async function readSmall(absPath) {
  try {
    const { size } = await fs.stat(absPath);
    if (size > MAX_CONTENT_BYTES) return null;  // too large
    return await fs.readFile(absPath, 'utf-8');
  } catch {
    return null;
  }
}

/**
 * Ask the local AI to describe a file in one sentence.
 * Returns null if AI is unavailable or times out.
 */
async function describeFile(filePath, content) {
  const trimmed = content.slice(0, 3_000);  // send at most 3k chars
  const prompt  =
`You are a code reviewer. Describe what the following file does in ONE sentence (max 20 words).
File: ${filePath}

---
${trimmed}
---

One-sentence description:`;

  try {
    const raw = await withTimeout(generate(prompt), AI_TIMEOUT_MS);
    return raw.replace(/\n.*/s, '').replace(/^["'\s]+|["'\s]+$/g, '').trim() || null;
  } catch {
    return null;
  }
}

function withTimeout(promise, ms) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error('AI timeout')), ms)),
  ]);
}

// ── Main audit ────────────────────────────────────────────────────────────────

/**
 * Run the project structure audit.
 *
 * @param {string}  projectRoot   Absolute path to the project root.
 * @param {boolean} useAI         Whether to call the local AI for descriptions.
 * @returns {Promise<AuditReport>}
 */
export async function runProjectAudit(projectRoot, useAI = true) {
  const startedAt = new Date().toISOString();
  const results   = [];

  for (const entry of EXPECTED) {
    const absPath = path.join(projectRoot, entry.path);
    const found   = await exists(absPath);

    const item = {
      type:        entry.type,
      path:        entry.path,
      exists:      found,
      description: null,
      aiUsed:      false,
    };

    if (found && entry.type === 'file' && entry.describe && useAI) {
      const content = await readSmall(absPath);
      if (content !== null) {
        item.description = await describeFile(entry.path, content);
        item.aiUsed      = item.description !== null;
      } else {
        item.description = '(file too large to describe)';
      }
    }

    results.push(item);
  }

  const missing = results.filter(r => !r.exists);
  const present = results.filter(r =>  r.exists);

  return {
    projectRoot,
    startedAt,
    summary: {
      total:   results.length,
      present: present.length,
      missing: missing.length,
    },
    results,
  };
}

/** Pretty-print an audit report to stdout. */
export function printAuditReport(report) {
  const { summary, results } = report;
  const tick  = '\u2705';
  const cross = '\u274C';
  const bot   = '\uD83E\uDD16';

  console.log('\n\uD83D\uDD0D Project Audit Report');
  console.log('='.repeat(50));
  console.log(`   Present : ${summary.present} / ${summary.total}`);
  if (summary.missing > 0)
    console.log(`   Missing : ${summary.missing}`);
  console.log();

  for (const r of results) {
    const icon  = r.exists ? tick : cross;
    const label = r.type === 'dir' ? `[dir]  ${r.path}` : `[file] ${r.path}`;
    console.log(`  ${icon}  ${label}`);
    if (r.description)
      console.log(`       ${bot}  ${r.description}`);
  }

  if (summary.missing > 0) {
    console.log('\n\u26A0\uFE0F  Missing paths:');
    for (const r of results.filter(x => !x.exists))
      console.log(`   - ${r.type === 'dir' ? 'dir ' : 'file'} ${r.path}`);
  }

  console.log();
}
