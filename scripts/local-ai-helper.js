#!/usr/bin/env node
/**
 * local-ai-helper.js — CLI for AI-assisted dev tasks.
 *
 * Sub-commands:
 *   audit          scan project structure + describe key files with local AI
 *   audit --save   additionally writes audit.json to project root
 *   audit --no-ai  structure check only, no AI descriptions
 *
 * Future sub-commands (see docs/local-ai-stack-TODO.md):
 *   classify       classify all ETSI sidecars by topic
 *   summarise      generate 2-3 sentence abstracts from sidecars
 *   tags           keyword tags for each standard
 *   report         combined HTML report
 *
 * Usage:
 *   node scripts/local-ai-helper.js audit
 *   node scripts/local-ai-helper.js audit --save --no-ai
 */

import path            from 'path';
import fs              from 'fs/promises';
import { fileURLToPath } from 'url';

import { isAvailable, bestModel, activeProvider } from '../src/local-ai-core.js';
import { runProjectAudit, printAuditReport }       from '../src/ai-tasks/project-audit.js';

const __dirname    = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.join(__dirname, '..');

const args      = process.argv.slice(2);
const subCmd    = args[0];
const SAVE      = args.includes('--save');
const NO_AI     = args.includes('--no-ai');

async function main() {
  if (!subCmd || subCmd === 'help') {
    console.log([
      '',
      '\uD83E\uDD16  local-ai-helper — dev-time AI assistant',
      '',
      'Commands:',
      '  audit           check project structure + AI file descriptions',
      '  audit --save    also write audit.json to project root',
      '  audit --no-ai   structure check only (no AI calls)',
      '',
      'Requires: LM Studio or Ollama running locally.',
      'See docs/local-ai-stack-TODO.md for the full roadmap.',
      '',
    ].join('\n'));
    process.exit(0);
  }

  if (subCmd === 'audit') {
    console.log('\uD83E\uDD16  local-ai-helper  →  audit');
    console.log('-'.repeat(40));

    // AI status
    const aiOk     = !NO_AI && await isAvailable();
    const model    = aiOk ? await bestModel()      : null;
    const provider = aiOk ? await activeProvider() : null;

    if (NO_AI) {
      console.log('\u26A0\uFE0F   --no-ai: structure check only');
    } else if (aiOk) {
      const label = provider === 'lmstudio' ? 'LM Studio' : 'Ollama';
      console.log(`\u2705  ${label} — model: ${model}`);
    } else {
      console.log('\u26A0\uFE0F   No local AI found — running structure check only');
      console.log('    Start LM Studio or Ollama to enable AI descriptions.');
    }

    const report = await runProjectAudit(PROJECT_ROOT, aiOk);
    printAuditReport(report);

    if (SAVE) {
      const outPath = path.join(PROJECT_ROOT, 'audit.json');
      await fs.writeFile(outPath, JSON.stringify(report, null, 2));
      console.log(`\uD83D\uDCBE  Saved → audit.json`);
    }

    // Exit with error code if anything is missing
    process.exit(report.summary.missing > 0 ? 1 : 0);
  }

  console.error(`\u274C  Unknown command: ${subCmd}`);
  console.error('    Run: node scripts/local-ai-helper.js help');
  process.exit(1);
}

main().catch(err => { console.error(err); process.exit(1); });
