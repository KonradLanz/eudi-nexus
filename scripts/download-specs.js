import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';
import * as cheerio from 'cheerio';
import { ETSIClient } from '../src/etsi-client.js';
import {
  saveHeaders, loadHeaders, checkIntegrity, checkRemoteChanged,
  formatCacheInfo, formatBytes
} from '../src/http-cache.js';
import dotenv from 'dotenv';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(__dirname, '..', '.env') });

const DOWNLOAD_PATH = '../downloads/specs';
const RESULTS_FILE  = path.join(DOWNLOAD_PATH, '_download_results.json');
const BASE_URL = 'https://portal.etsi.org';

async function downloadLatestSpecs() {
  console.log('\uD83D\uDCE5 ETSI Specification Downloader');
  console.log('================================\n');

  const args          = process.argv.slice(2);
  const limitArg      = args.find(a => a.startsWith('--limit='));
  const limit         = limitArg ? parseInt(limitArg.split('=')[1]) : null;
  const publishedOnly = args.includes('--published-only');
  const headersOnly   = args.includes('--headers-only');

  if (headersOnly) console.log('\uD83D\uDC41\uFE0F  Mode: --headers-only (HEAD requests, no downloads)\n');

  const workItems      = JSON.parse(await fs.readFile('../downloads/esi_overview.json', 'utf-8'));
  const activeItems    = workItems.activeWorkItems;
  const publishedItems = workItems.publishedDocuments;
  console.log(`\uD83D\uDCCB Found ${activeItems.length} active + ${publishedItems.length} published items\n`);

  const client = new ETSIClient();
  console.log('\uD83D\uDD10 Logging in...');
  const loggedIn = await client.login(process.env.ETSI_USERNAME, process.env.ETSI_PASSWORD);
  if (!loggedIn) { console.error('\u274C Login failed'); process.exit(1); }
  console.log('\u2705 Login successful!\n');

  await fs.mkdir(DOWNLOAD_PATH, { recursive: true });

  const cachedByNumber = await loadCachedIndex();
  const diskIndex      = await buildDiskIndex(DOWNLOAD_PATH);

  console.log(`\uD83D\uDDC2\uFE0F  Already downloaded: ${cachedByNumber.size} files (${diskIndex.size} on disk)\n`);

  const results = { success: [], redownloaded: [], skipped: [], changed: [], failed: [], noDownload: [] };

  const allItems    = publishedOnly ? publishedItems : [...publishedItems, ...activeItems];
  const seen        = new Set();
  const uniqueItems = allItems.filter(item => {
    if (!item.etsiNumber || seen.has(item.etsiNumber)) return false;
    seen.add(item.etsiNumber); return true;
  });

  const itemsToProcess = limit ? uniqueItems.slice(0, limit) : uniqueItems;
  console.log(`\uD83D\uDCE6 Processing ${itemsToProcess.length} specifications${limit ? ` (limited to ${limit})` : ''}...\n`);

  for (let i = 0; i < itemsToProcess.length; i++) {
    const item     = itemsToProcess[i];
    const progress = `[${i + 1}/${uniqueItems.length}]`;
    console.log(`${progress} ${item.etsiNumber}`);

    let existingPath = cachedByNumber.get(item.etsiNumber) ?? null;
    if (!existingPath) existingPath = findInDiskIndex(diskIndex, item.etsiNumber);

    let needsDownload = false;
    let isRedownload  = false;

    if (existingPath) {
      const fileExists = await fs.stat(existingPath).then(() => true).catch(() => false);
      if (fileExists) {
        const cache     = await loadHeaders(existingPath);
        const integrity = await checkIntegrity(existingPath);
        const relPath   = path.relative(path.join(__dirname, '..'), existingPath);

        if (!integrity.ok && integrity.cachedSize !== null) {
          console.log(`    \u26A0\uFE0F  Integrity FAIL \u2014 disk: ${formatBytes(integrity.diskSize)}, expected: ${formatBytes(integrity.cachedSize)} \u2014 re-downloading`);
          console.log(`    \uD83D\uDCC4 ${relPath}`);
          needsDownload = true;
          isRedownload  = true;
        } else if (headersOnly) {
          await headersOnlyCheck(client, item, existingPath, cache, integrity, results);
          await sleep(200);
          continue;
        } else {
          const cacheInfo = formatCacheInfo(cache, integrity);
          const noHeaders = !cache ? ' \u26A0\uFE0F no HTTP headers cached \u2014 run npm run backfill-headers' : '';
          console.log(`    \u23ED\uFE0F  Cached: ${cacheInfo}${noHeaders}`);
          console.log(`    \uD83D\uDCC4 ${relPath}`);
          results.skipped.push({ etsiNumber: item.etsiNumber, file: existingPath });
          continue;
        }
      } else {
        needsDownload = true;
      }
    } else {
      needsDownload = true;
    }

    if (headersOnly) {
      console.log(`    \u23ED\uFE0F  Not cached \u2014 skipping (headers-only mode)`);
      continue;
    }

    if (!needsDownload) continue;

    // Always resolve fresh download URL from the work item detail page.
    // This ensures we get the correct file even on re-downloads where the
    // disk-index may have matched the wrong cached file.
    try {
      const downloadInfo = await fetchDownloadLink(client, item);

      if (downloadInfo?.url) {
        const result = await downloadFile(client, downloadInfo, item);
        if (result) {
          if (isRedownload) {
            results.redownloaded.push({ etsiNumber: item.etsiNumber, filePath: result.filePath, url: result.url });
            console.log(`    \uD83D\uDD04 Re-downloaded: ${result.label}`);
          } else {
            results.success.push({ etsiNumber: item.etsiNumber, filePath: result.filePath, url: result.url });
            console.log(`    \u2705 Downloaded: ${result.label}`);
          }
          console.log(`    \uD83D\uDCC4 ${path.relative(path.join(__dirname, '..'), result.filePath)}`);
        } else {
          results.failed.push({ etsiNumber: item.etsiNumber, reason: 'Download returned no data', url: downloadInfo.url });
          console.log(`    \u274C Download failed`);
          console.log(`    \uD83D\uDD17 URL: ${downloadInfo.url}`);
        }
      } else {
        const tried = resolveDetailUrl(item);
        results.noDownload.push({ etsiNumber: item.etsiNumber, reason: 'No download link found', tried });
        console.log(`    \u26A0\uFE0F  No download available`);
        console.log(`    \uD83D\uDD17 Tried: ${tried}`);
      }

      await sleep(500);
    } catch (error) {
      const tried = resolveDetailUrl(item);
      results.failed.push({ etsiNumber: item.etsiNumber, reason: error.message, tried });
      console.log(`    \u274C Error: ${error.message}`);
      console.log(`    \uD83D\uDD17 Tried: ${tried}`);
    }
  }

  await saveResults(results);

  console.log('\n\uD83D\uDCCA Download Summary:');
  if (headersOnly) {
    console.log(`   \u2705 Up-to-date:             ${results.skipped.length}`);
    console.log(`   \uD83D\uDD04 Changed on remote:      ${results.changed.length}`);
    console.log(`   \u26A0\uFE0F  No download available:  ${results.noDownload.length}`);
  } else {
    console.log(`   \u2705 Downloaded (new):       ${results.success.length}`);
    console.log(`   \uD83D\uDD04 Re-downloaded:          ${results.redownloaded.length}`);
    console.log(`   \u23ED\uFE0F  Skipped (cached):       ${results.skipped.length}`);
    console.log(`   \u274C Failed:                 ${results.failed.length}`);
    console.log(`   \u26A0\uFE0F  No download available:  ${results.noDownload.length}`);
  }
  console.log(`\n\uD83D\uDCBE Results saved to ${RESULTS_FILE}`);
}

// ── Cache index helpers ──────────────────────────────────────────────────────

async function loadCachedIndex() {
  const index = new Map();
  try {
    const raw = JSON.parse(await fs.readFile(RESULTS_FILE, 'utf-8'));
    for (const entry of (raw.success ?? [])) {
      if (entry.etsiNumber && entry.filePath) index.set(entry.etsiNumber, entry.filePath);
    }
    for (const entry of (raw.redownloaded ?? [])) {
      if (entry.etsiNumber && entry.filePath) index.set(entry.etsiNumber, entry.filePath);
    }
    for (const entry of (raw.skipped ?? [])) {
      if (entry.etsiNumber && (entry.file || entry.filePath))
        index.set(entry.etsiNumber, entry.file ?? entry.filePath);
    }
  } catch { /* no results file yet */ }
  return index;
}

async function buildDiskIndex(basePath) {
  const index = new Map();
  try {
    const dirs = await fs.readdir(basePath, { withFileTypes: true });
    for (const entry of dirs) {
      if (!entry.isDirectory()) continue;
      const subDir = path.join(basePath, entry.name);
      const files  = await fs.readdir(subDir);
      for (const file of files) {
        if (file.startsWith('.')) continue;
        index.set(file.toLowerCase(), path.join(subDir, file));
      }
    }
  } catch { /* not yet created */ }
  return index;
}

/**
 * Match ETSI number against on-disk filenames.
 *
 * Uses exact digit-boundary matching to avoid false positives like
 * "TS 101 536" (digits: 101536) matching "tr_10153302v..." via substring.
 *
 * Strategy:
 *   1. ESI draft names (ESI-0019412) → numeric key after ESI-
 *   2. Standard numbers → require digit sequence to appear at a word boundary
 *      in the filename (preceded/followed by non-digit or start/end of token).
 */
function findInDiskIndex(diskIndex, etsiNumber) {
  // ESI draft names like "DTS/ESI-0019172-2"
  const esiMatch = etsiNumber.match(/ESI-00?(\d{5,7})/);
  if (esiMatch) {
    const key = esiMatch[1].replace(/^0+/, '');
    for (const [name, filePath] of diskIndex) {
      // Boundary check: key must not be surrounded by more digits
      const re = new RegExp(`(?<![0-9])0*${key}(?![0-9])`);
      if (re.test(name)) return filePath;
    }
  }

  // Standard ETSI numbers: "EN 319 403", "SR 003 091", "TS 101 536"
  const allDigits = etsiNumber.replace(/[^0-9]/g, '');
  for (const len of [7, 6]) {
    const digits = allDigits.slice(0, len);
    if (digits.length < len) continue;
    // Require the digit sequence to be at a non-digit boundary in the filename
    const re = new RegExp(`(?<![0-9])0*${digits.replace(/^0+/, '')}(?![0-9])`);
    for (const [name, filePath] of diskIndex) {
      if (re.test(name)) return filePath;
    }
  }
  return null;
}

async function saveResults(newResults) {
  let existing = { success: [], redownloaded: [], skipped: [], changed: [], failed: [], noDownload: [] };
  try { existing = JSON.parse(await fs.readFile(RESULTS_FILE, 'utf-8')); } catch { /* first run */ }
  const successMap = new Map([
    ...(existing.success     ?? []).map(e => [e.etsiNumber, e]),
    ...(existing.redownloaded ?? []).map(e => [e.etsiNumber, e]),
  ]);
  for (const e of [...(newResults.success ?? []), ...(newResults.redownloaded ?? [])])
    successMap.set(e.etsiNumber, e);
  // Split back by whether they are re-downloads
  const redownloadedNums = new Set((newResults.redownloaded ?? []).map(e => e.etsiNumber));
  existing.success      = [...successMap.values()].filter(e => !redownloadedNums.has(e.etsiNumber));
  existing.redownloaded = (newResults.redownloaded ?? []);
  existing.failed       = newResults.failed;
  existing.noDownload   = newResults.noDownload;
  existing.changed      = newResults.changed;
  await fs.writeFile(RESULTS_FILE, JSON.stringify(existing, null, 2));
}

// ── Headers-only check ───────────────────────────────────────────────────────

async function headersOnlyCheck(client, item, existingPath, cache, integrity, results) {
  const downloadInfo = await fetchDownloadLink(client, item).catch(() => null);
  const relPath = path.relative(path.join(__dirname, '..'), existingPath);
  if (!downloadInfo?.url) {
    console.log(`    \u23ED\uFE0F  Cached (no URL to HEAD): ${formatCacheInfo(cache, integrity)}`);
    console.log(`    \uD83D\uDCC4 ${relPath}`);
    results.skipped.push({ etsiNumber: item.etsiNumber, file: existingPath });
    return;
  }
  const check = await checkRemoteChanged(downloadInfo.url, existingPath);
  if (check.changed === true) {
    console.log(`    \uD83D\uDD04 CHANGED: ${check.reason}`);
    console.log(`    \uD83D\uDCC4 ${relPath}`);
    console.log(`    \uD83D\uDD17 ${downloadInfo.url}`);
    results.changed.push({ etsiNumber: item.etsiNumber, reason: check.reason, url: downloadInfo.url, filePath: existingPath });
  } else if (check.changed === false) {
    console.log(`    \u2705 Up-to-date: ${formatCacheInfo(cache, integrity)}`);
    console.log(`    \uD83D\uDCC4 ${relPath}`);
    results.skipped.push({ etsiNumber: item.etsiNumber, file: existingPath });
  } else {
    console.log(`    \u2753 Unverifiable: ${check.reason} | ${formatCacheInfo(cache, integrity)}`);
    console.log(`    \uD83D\uDCC4 ${relPath}`);
    results.skipped.push({ etsiNumber: item.etsiNumber, file: existingPath, note: check.reason });
  }
}

// ── Network helpers ──────────────────────────────────────────────────────────

async function fetchDownloadLink(client, item) {
  if (!item.detailUrl) return null;
  const response = await client.fetch(item.detailUrl, { headers: client.getDefaultHeaders() });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const html = await response.text();
  const $    = cheerio.load(html);

  let downloadUrl = null, downloadType = null;
  $('a[href*="www.etsi.org/deliver"]').each((_, el) => {
    const href = $(el).attr('href');
    if (href?.includes('.pdf')) { downloadUrl = href; downloadType = 'pdf'; }
  });
  if (!downloadUrl) $('a[href*="pda.etsi.org"]').each((_, el) => {
    const href = $(el).attr('href');
    if (href) { downloadUrl = href; downloadType = 'pda'; }
  });
  if (!downloadUrl) $('a').each((_, el) => {
    const href = $(el).attr('href') || '';
    if (href.includes('.pdf') && href.includes('etsi'))   { downloadUrl = href; downloadType = 'pdf'; }
    else if (href.includes('.zip') && !downloadUrl)        { downloadUrl = href; downloadType = 'zip'; }
  });
  if (!downloadUrl) $('a[href*="docbox.etsi.org"]').each((_, el) => {
    const href = $(el).attr('href')?.trim();
    if (href && (href.includes('.docx') || href.includes('.doc') || href.includes('.pdf'))) {
      downloadUrl = href;
      downloadType = href.includes('.pdf') ? 'draft-pdf' : 'draft-docx';
    }
  });
  return downloadUrl ? { url: downloadUrl, type: downloadType } : null;
}

async function downloadFile(client, downloadInfo, item) {
  try {
    const isPublicDelivery = downloadInfo.url.includes('www.etsi.org/deliver');
    const fetchFn = isPublicDelivery ? fetch : client.fetch.bind(client);
    const response = await fetchFn(downloadInfo.url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Accept': 'application/pdf,application/zip,application/octet-stream,*/*'
      }
    });
    if (!response.ok) return null;

    let filename = null;
    const cd = response.headers.get('content-disposition');
    if (cd) {
      const m = cd.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
      if (m) filename = m[1].replace(/['"]/g, '');
    }
    if (!filename) filename = path.basename(new URL(downloadInfo.url).pathname);
    if (!filename || filename === '' || filename === '/') {
      const safe = item.etsiNumber.replace(/[^a-zA-Z0-9-_]/g, '_');
      const ext  = { pdf: '.pdf', zip: '.zip', 'draft-docx': '.docx', 'draft-pdf': '.pdf' }[downloadInfo.type] || '.bin';
      filename = `${safe}${ext}`;
    }
    filename = filename.replace(/[<>:"/\\|?*]/g, '_');

    const buffer = Buffer.from(await response.arrayBuffer());
    if (buffer.length < 1000) {
      console.log(`    \u26A0\uFE0F File too small (${buffer.length} bytes) \u2014 skipping`);
      return null;
    }

    const typeMatch = item.etsiNumber?.match(/^(EN|TS|TR|ES|EG)/i);
    const subDir    = typeMatch ? typeMatch[1].toUpperCase() : 'Other';
    const targetDir = path.join(DOWNLOAD_PATH, subDir);
    await fs.mkdir(targetDir, { recursive: true });
    const filePath  = path.join(targetDir, filename);
    await fs.writeFile(filePath, buffer);
    await saveHeaders(filePath, response);
    await saveUrlSidecar(filePath, downloadInfo.url);

    return { label: `${subDir}/${filename} (${formatBytes(buffer.length)})`, filePath, url: downloadInfo.url };
  } catch (error) {
    console.error(`    Download error: ${error.message}`);
    return null;
  }
}

/** Write a .url.<filename> sidecar recording where the file came from. */
async function saveUrlSidecar(filePath, url) {
  const source = url.includes('www.etsi.org/deliver') ? 'etsi-delivery'
               : url.includes('docbox.etsi.org')       ? 'docbox'
               : url.includes('pda.etsi.org')           ? 'pda'
               : 'portal';
  await fs.writeFile(
    path.join(path.dirname(filePath), `.url.${path.basename(filePath)}`),
    JSON.stringify({ url, source, savedAt: new Date().toISOString() }, null, 2)
  );
}

function resolveDetailUrl(item) {
  if (!item.detailUrl) return '(no detail URL)';
  return item.detailUrl.startsWith('http') ? item.detailUrl : `${BASE_URL}${item.detailUrl}`;
}

function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

downloadLatestSpecs().catch(console.error);
