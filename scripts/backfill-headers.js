/**
 * backfill-headers.js
 *
 * Finds all downloaded files that have no .headers.* sidecar and
 * backfills them via a HEAD request to their known URL.
 *
 * URL sources (in priority order):
 *   1. _download_results.json  (url field per entry, keyed by filePath)
 *   2. guessUrl()              reconstruct from ETSI delivery filename pattern
 *
 * Usage:
 *   npm run backfill-headers
 *   npm run backfill-headers:dry    # list only, no requests
 */

import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';
import { formatBytes } from '../src/http-cache.js';

const __dirname   = path.dirname(fileURLToPath(import.meta.url));
const SPECS_PATH  = path.join(__dirname, '..', 'downloads', 'specs');
const RESULTS_FILE = path.join(SPECS_PATH, '_download_results.json');

const dryRun = process.argv.includes('--dry-run');
if (dryRun) console.log('\uD83D\uDDFB  Dry-run mode \u2014 no requests will be made\n');

async function main() {
  console.log('\uD83D\uDD27 Header Backfill');
  console.log('================\n');

  // Build URL map: absoluteFilePath → url  (from previous successful downloads)
  const urlByFile = new Map();
  try {
    const raw = JSON.parse(await fs.readFile(RESULTS_FILE, 'utf-8'));
    for (const entry of (raw.success ?? [])) {
      if (entry.filePath && entry.url) urlByFile.set(entry.filePath, entry.url);
    }
  } catch { /* no results yet — first run */ }

  // Collect all files missing a .headers.* sidecar
  const missing = [];
  const dirs = await fs.readdir(SPECS_PATH, { withFileTypes: true });
  for (const dir of dirs) {
    if (!dir.isDirectory()) continue;
    const subDir = path.join(SPECS_PATH, dir.name);
    const files  = await fs.readdir(subDir);
    for (const file of files) {
      if (file.startsWith('.') || file.startsWith('_')) continue;
      const filePath   = path.join(subDir, file);
      const headerPath = path.join(subDir, `.headers.${file}`);
      const hasHeaders = await fs.stat(headerPath).then(() => true).catch(() => false);
      if (!hasHeaders) missing.push(filePath);
    }
  }

  console.log(`\uD83D\uDD0D Found ${missing.length} file(s) without HTTP header sidecar:\n`);
  if (missing.length === 0) { console.log('\u2705 All files have headers. Nothing to do.'); return; }

  let ok = 0, failed = 0, skipped = 0;

  for (const filePath of missing) {
    const rel = path.relative(path.join(__dirname, '..'), filePath);
    const url = urlByFile.get(filePath) ?? guessUrl(filePath);

    if (!url) {
      console.log(`  \u2753 ${rel}`);
      console.log(`     \u2715 No URL \u2014 will be resolved on next npm run download`);
      skipped++;
      continue;
    }

    console.log(`  \uD83D\uDCC4 ${rel}`);
    console.log(`     \uD83D\uDD17 ${url}`);

    if (dryRun) { skipped++; continue; }

    try {
      const resp = await fetch(url, {
        method: 'HEAD',
        headers: {
          'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
          'Accept': '*/*'
        }
      });

      if (resp.ok || resp.status === 304) {
        const stat = await fs.stat(filePath);
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
        ok++;
      } else {
        console.log(`     \u26A0\uFE0F HTTP ${resp.status} \u2014 headers not saved`);
        failed++;
      }
    } catch (e) {
      console.log(`     \u274C ${e.message}`);
      failed++;
    }

    await sleep(300);
  }

  console.log(`\n\uD83D\uDCCA Backfill Summary:`);
  console.log(`   \u2705 Written:  ${ok}`);
  console.log(`   \u274C Failed:   ${failed}`);
  console.log(`   \u2753 Skipped:  ${skipped}${skipped > 0 ? '  \u2190 re-run after: npm run download' : ''}`);
}

/**
 * Reconstruct the ETSI public delivery URL from the filename.
 *
 * Handles:
 *   en_319403v020202p.pdf   → etsi_en/319400_319499/319403/02.02.02_60/...
 *   ts_10153301v010301p.pdf → etsi_ts/1015330_1015399/1015330/...  (7-digit)
 *   sr_003091v010102p.pdf   → etsi_sr/3000_3099/3091/01.01.02_60/...
 *   tr_10153302v010301p.pdf → etsi_tr/1015330_1015399/1015332/...
 */
function guessUrl(filePath) {
  const file = path.basename(filePath).toLowerCase();
  // Match: <type>_<num 6-7 digits>v<MM><mm><pp>p.pdf
  const m = file.match(/^(en|ts|tr|es|eg|sr)_(\d{6,7})v(\d{2})(\d{2})(\d{2})p\.pdf$/);
  if (!m) return null;

  const [, type, num, major, minor, patch] = m;
  const numInt = parseInt(num);

  // Range bucket: round down to nearest 100 for 6-digit, nearest 10 for 7-digit
  const bucket   = num.length === 7 ? 10 : 100;
  const rangeStart = Math.floor(numInt / bucket) * bucket;
  const rangeEnd   = rangeStart + (bucket - 1);
  const rangeStr   = `${rangeStart}_${rangeEnd}`;

  const verPad = [
    String(parseInt(major)).padStart(2, '0'),
    String(parseInt(minor)).padStart(2, '0'),
    String(parseInt(patch)).padStart(2, '0')
  ].join('.');

  return `https://www.etsi.org/deliver/etsi_${type}/${rangeStr}/${num}/${verPad}_60/${file}`;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

main().catch(console.error);
