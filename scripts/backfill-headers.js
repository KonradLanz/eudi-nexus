/**
 * backfill-headers.js
 *
 * Finds all downloaded files that have no .headers.* sidecar and
 * backfills them via a HEAD request (or GET for auth-required files).
 *
 * URL resolution priority:
 *   1. _download_results.json  (url per filePath, most reliable)
 *   2. work_items.json → detailUrl → scrape download link (same as download-specs.js)
 *
 * Usage:
 *   npm run backfill-headers
 *   npm run backfill-headers:dry    # list only, no requests
 */

import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';
import * as cheerio from 'cheerio';
import { ETSIClient } from '../src/etsi-client.js';
import { formatBytes } from '../src/http-cache.js';
import dotenv from 'dotenv';

const __dirname    = path.dirname(fileURLToPath(import.meta.url));
const SPECS_PATH   = path.join(__dirname, '..', 'downloads', 'specs');
const RESULTS_FILE = path.join(SPECS_PATH, '_download_results.json');
const WORK_ITEMS   = path.join(__dirname, '..', 'downloads', 'work_items.json');

dotenv.config({ path: path.join(__dirname, '..', '.env') });

const dryRun = process.argv.includes('--dry-run');
if (dryRun) console.log('\uD83D\uDDFB  Dry-run mode \u2014 no requests will be made\n');

async function main() {
  console.log('\uD83D\uDD27 Header Backfill');
  console.log('================\n');

  // ─ URL map from previous download results (filePath → url) ─
  const urlByFile = new Map();
  try {
    const raw = JSON.parse(await fs.readFile(RESULTS_FILE, 'utf-8'));
    for (const e of (raw.success ?? []))
      if (e.filePath && e.url) urlByFile.set(e.filePath, e.url);
  } catch { /* first run */ }

  // ─ Disk index: filename (lowercase) → filePath, for matching work items ─
  const diskByName = new Map();
  const missing    = [];
  const dirs = await fs.readdir(SPECS_PATH, { withFileTypes: true });
  for (const dir of dirs) {
    if (!dir.isDirectory()) continue;
    const subDir = path.join(SPECS_PATH, dir.name);
    for (const file of await fs.readdir(subDir)) {
      if (file.startsWith('.') || file.startsWith('_')) continue;
      const filePath   = path.join(subDir, file);
      const headerPath = path.join(subDir, `.headers.${file}`);
      diskByName.set(file.toLowerCase(), filePath);
      const hasHeaders = await fs.stat(headerPath).then(() => true).catch(() => false);
      if (!hasHeaders) missing.push(filePath);
    }
  }

  console.log(`\uD83D\uDD0D Found ${missing.length} file(s) without HTTP header sidecar:\n`);
  if (missing.length === 0) { console.log('\u2705 All files have headers. Nothing to do.'); return; }

  // ─ Load work_items.json and build digit-heuristic → detailUrl map ─
  const detailUrlByDigits = new Map(); // digit-key → { detailUrl, etsiNumber }
  try {
    const items = JSON.parse(await fs.readFile(WORK_ITEMS, 'utf-8'));
    for (const item of items) {
      if (!item.detailUrl || !item.etsiNumber) continue;
      const digits = item.etsiNumber.replace(/[^0-9]/g, '');
      for (const len of [7, 6, 5]) {
        const key = digits.slice(0, len);
        if (key.length === len) detailUrlByDigits.set(key, { detailUrl: item.detailUrl, etsiNumber: item.etsiNumber });
      }
    }
  } catch (e) {
    console.warn(`\u26A0\uFE0F Could not load work_items.json: ${e.message}`);
  }

  // Login once (needed for docbox / portal URLs)
  const client = new ETSIClient();
  if (!dryRun) {
    process.stdout.write('\uD83D\uDD10 Logging in... ');
    const ok = await client.login(process.env.ETSI_USERNAME, process.env.ETSI_PASSWORD);
    console.log(ok ? '\u2705' : '\u26A0\uFE0F  Login failed (portal URLs may not work)');
    console.log();
  }

  let written = 0, failed = 0, noUrl = 0;

  for (const filePath of missing) {
    const rel      = path.relative(path.join(__dirname, '..'), filePath);
    const basename = path.basename(filePath).toLowerCase();

    // Resolve URL: results JSON → work_items heuristic
    let downloadUrl = urlByFile.get(filePath) ?? null;
    if (!downloadUrl) {
      const allDigits = basename.replace(/[^0-9]/g, '');
      for (const len of [7, 6, 5]) {
        const key   = allDigits.slice(0, len);
        const match = detailUrlByDigits.get(key);
        if (match) {
          if (!dryRun) {
            downloadUrl = await resolveDownloadUrl(client, match.detailUrl).catch(() => null);
          } else {
            downloadUrl = `(would resolve from ${match.etsiNumber} detailUrl)`;
          }
          break;
        }
      }
    }

    if (!downloadUrl) {
      console.log(`  \u2753 ${rel}`);
      console.log(`     \u2715 No URL found in results or work_items.json`);
      noUrl++;
      continue;
    }

    console.log(`  \uD83D\uDCC4 ${rel}`);
    console.log(`     \uD83D\uDD17 ${downloadUrl}`);

    if (dryRun) { noUrl++; continue; }

    try {
      const isPublic = downloadUrl.includes('www.etsi.org/deliver');
      const resp = await (isPublic ? fetch : client.fetch.bind(client))(downloadUrl, {
        method: 'HEAD',
        headers: {
          'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
          'Accept': '*/*'
        }
      });

      if (resp.ok || resp.status === 304) {
        const stat  = await fs.stat(filePath);
        const cache = { 'x-downloaded-at': stat.mtime.toISOString() };
        for (const h of ['etag', 'last-modified', 'content-length', 'content-type', 'cache-control']) {
          const v = resp.headers.get(h);
          if (v) cache[h] = v;
        }
        await fs.writeFile(
          path.join(path.dirname(filePath), `.headers.${path.basename(filePath)}`),
          JSON.stringify(cache, null, 2)
        );
        const size = cache['content-length'] ? formatBytes(parseInt(cache['content-length'])) : '?';
        const etag = cache['etag'] ? ` | ETag: ${cache['etag']}` : '';
        console.log(`     \u2705 ${resp.status} \u2014 Size: ${size}${etag}`);
        written++;
      } else {
        console.log(`     \u26A0\uFE0F HTTP ${resp.status}`);
        failed++;
      }
    } catch (e) {
      console.log(`     \u274C ${e.message}`);
      failed++;
    }

    await sleep(300);
  }

  console.log(`\n\uD83D\uDCCA Backfill Summary:`);
  console.log(`   \u2705 Written:  ${written}`);
  console.log(`   \u274C Failed:   ${failed}`);
  console.log(`   \u2753 No URL:   ${noUrl}`);
  if (noUrl > 0) console.log(`\n   \u2192 For remaining files, run: npm run download`);
}

/** Scrape a ETSI portal detail page and return the first download URL found. */
async function resolveDownloadUrl(client, detailUrl) {
  const resp = await client.fetch(detailUrl, { headers: client.getDefaultHeaders() });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const $ = cheerio.load(await resp.text());

  let url = null;
  $('a[href*="www.etsi.org/deliver"]').each((_, el) => {
    const h = $(el).attr('href');
    if (h?.includes('.pdf') && !url) url = h;
  });
  if (!url) $('a[href*="pda.etsi.org"]').each((_, el) => {
    if (!url) url = $(el).attr('href');
  });
  if (!url) $('a[href*="docbox.etsi.org"]').each((_, el) => {
    const h = $(el).attr('href')?.trim();
    if (h && !url) url = h;
  });
  if (!url) $('a').each((_, el) => {
    const h = $(el).attr('href') || '';
    if (!url && h.includes('.pdf') && h.includes('etsi')) url = h;
    if (!url && h.includes('.zip')) url = h;
  });
  return url;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

main().catch(console.error);
