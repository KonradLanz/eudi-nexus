import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';
import * as cheerio from 'cheerio';
import { ETSIClient } from '../src/etsi-client.js';
import {
  saveHeaders, loadHeaders, checkIntegrity, checkRemoteChanged,
  formatCacheInfo, formatBytes, headerCachePath
} from '../src/http-cache.js';
import dotenv from 'dotenv';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(__dirname, '..', '.env') });

const DOWNLOAD_PATH = '../downloads/specs';
const RESULTS_FILE  = path.join(DOWNLOAD_PATH, '_download_results.json');
const BASE_URL      = 'https://portal.etsi.org';

// Public work-item report — same content as the portal detail page but
// without navigation chrome, and accessible without login.
const PUBLIC_REPORT_BASE = 'https://portal.etsi.org/webapp/WorkProgram/Report_WorkItem.asp';

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Build the canonical public Report_WorkItem URL for a WKI_ID. */
function publicReportUrl(wkiId) {
  if (!wkiId) return null;
  return `${PUBLIC_REPORT_BASE}?WKI_ID=${wkiId}`;
}

/**
 * Strip query parameters whose value is empty/whitespace.
 * e.g. "...?WKI_ID=78824&action=" → "...?WKI_ID=78824"
 */
function cleanUrl(raw) {
  try {
    const u = new URL(raw.startsWith('http') ? raw : `${BASE_URL}${raw}`);
    for (const [k, v] of [...u.searchParams.entries()]) {
      if (!v || !v.trim()) u.searchParams.delete(k);
    }
    return u.toString();
  } catch {
    return raw;
  }
}

function extractWkiId(url) {
  if (!url) return null;
  const m = url.match(/WKI_ID=(\d+)/i);
  return m ? m[1] : null;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── Main ─────────────────────────────────────────────────────────────────────

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

  const results = {
    success: [], redownloaded: [], skipped: [], stopped: [],
    changed: [], failed: [], noDownload: [],
  };

  const allItems    = publishedOnly ? publishedItems : [...publishedItems, ...activeItems];
  const seen        = new Set();
  const uniqueItems = allItems.filter(item => {
    if (!item.etsiNumber || seen.has(item.etsiNumber)) return false;
    seen.add(item.etsiNumber);
    return true;
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

    try {
      const info = await fetchDetailPage(client, item);

      if (info) {
        // Sidecars — skip silently if already on disk
        const wiPath = await saveWorkitemSidecar(info.html, info.wkiId, item);
        if (wiPath) await saveResponseHeaders(wiPath, info.responseHeaders);

        const scPath = await saveScheduleSidecar(info.$, info.wkiId, item);
        if (scPath) await saveResponseHeaders(scPath, info.responseHeaders);

        if (info.status)    console.log(`    \uD83D\uDCCC Status: ${info.status}`);
        if (info.usedLogin) console.log(`    \uD83D\uDD10 Used authenticated session`);
      }

      if (info?.stopped) {
        const scheduleUrl = info.wkiId
          ? `https://portal.etsi.org/eWPM/index.html#/schedule?WKI_ID=${info.wkiId}`
          : null;
        console.log(`    \uD83D\uDED1 STOPPED work item \u2014 no file available`);
        if (scheduleUrl) console.log(`    \uD83D\uDCC5 ${scheduleUrl}`);
        results.stopped.push({
          etsiNumber: item.etsiNumber,
          wkiId:      info.wkiId,
          scheduleUrl,
          detailUrl:  item.detailUrl,
          status:     info.status ?? null,
        });
        await sleep(200);
        continue;
      }

      if (info?.url) {
        const result = await downloadFile(client, info, item);
        if (result) {
          const entry = { etsiNumber: item.etsiNumber, filePath: result.filePath, url: result.url, status: info.status ?? null };
          if (isRedownload) {
            results.redownloaded.push(entry);
            console.log(`    \uD83D\uDD04 Re-downloaded: ${result.label}`);
          } else {
            results.success.push(entry);
            console.log(`    \u2705 Downloaded: ${result.label}`);
          }
          console.log(`    \uD83D\uDCC4 ${path.relative(path.join(__dirname, '..'), result.filePath)}`);
        } else {
          results.failed.push({ etsiNumber: item.etsiNumber, reason: 'Download returned no data', url: info.url });
          console.log(`    \u274C Download failed`);
        }
      } else {
        results.noDownload.push({
          etsiNumber: item.etsiNumber,
          reason:     'No download link found',
          status:     info?.status ?? null,
        });
        console.log(`    \u26A0\uFE0F  No download link found`);
      }

      await sleep(500);
    } catch (error) {
      results.failed.push({ etsiNumber: item.etsiNumber, reason: error.message });
      console.log(`    \u274C Error: ${error.message}`);
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
    console.log(`   \uD83D\uDED1 Stopped work items:     ${results.stopped.length}`);
    console.log(`   \u274C Failed:                 ${results.failed.length}`);
    console.log(`   \u26A0\uFE0F  No download available:  ${results.noDownload.length}`);
  }
  console.log(`\n\uD83D\uDCBE Results saved to ${RESULTS_FILE}`);
}

// ── Cache index helpers ───────────────────────────────────────────────────────

async function loadCachedIndex() {
  const index = new Map();
  try {
    const raw = JSON.parse(await fs.readFile(RESULTS_FILE, 'utf-8'));
    for (const entry of [...(raw.success ?? []), ...(raw.redownloaded ?? [])]) {
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

function findInDiskIndex(diskIndex, etsiNumber) {
  const esiMatch = etsiNumber.match(/ESI-0*(\d{5,7})(-\d+)?/);
  if (esiMatch) {
    const num  = esiMatch[1].replace(/^0+/, '');
    const part = esiMatch[2] ?? '';
    const reStr = `(?<![0-9])0*${num}${part ? part.replace('-', '-0*') : ''}(?![0-9])`;
    const re    = new RegExp(reStr);
    for (const [name, filePath] of diskIndex) {
      if (re.test(name)) return filePath;
    }
    if (part) {
      const reFallback = new RegExp(`(?<![0-9])0*${num}(?![0-9])`);
      for (const [name, filePath] of diskIndex) {
        if (reFallback.test(name)) return filePath;
      }
    }
  }
  const allDigits = etsiNumber.replace(/[^0-9]/g, '');
  for (const len of [7, 6]) {
    const digits = allDigits.slice(0, len);
    if (digits.length < len) continue;
    const re = new RegExp(`(?<![0-9])0*${digits.replace(/^0+/, '')}(?![0-9])`);
    for (const [name, filePath] of diskIndex) {
      if (re.test(name)) return filePath;
    }
  }
  return null;
}

async function saveResults(newResults) {
  let existing = { success: [], redownloaded: [], skipped: [], stopped: [], changed: [], failed: [], noDownload: [] };
  try { existing = JSON.parse(await fs.readFile(RESULTS_FILE, 'utf-8')); } catch { /* first run */ }

  const successMap = new Map([
    ...(existing.success      ?? []).map(e => [e.etsiNumber, e]),
    ...(existing.redownloaded ?? []).map(e => [e.etsiNumber, e]),
  ]);
  for (const e of [...(newResults.success ?? []), ...(newResults.redownloaded ?? [])])
    successMap.set(e.etsiNumber, e);
  const redownloadedNums = new Set((newResults.redownloaded ?? []).map(e => e.etsiNumber));
  existing.success      = [...successMap.values()].filter(e => !redownloadedNums.has(e.etsiNumber));
  existing.redownloaded = newResults.redownloaded ?? [];

  const stoppedMap = new Map((existing.stopped ?? []).map(e => [e.etsiNumber, e]));
  for (const e of (newResults.stopped ?? [])) stoppedMap.set(e.etsiNumber, e);
  existing.stopped    = [...stoppedMap.values()];
  existing.failed     = newResults.failed;
  existing.noDownload = newResults.noDownload;
  existing.changed    = newResults.changed;

  await fs.writeFile(RESULTS_FILE, JSON.stringify(existing, null, 2));
}

// ── Headers-only check ───────────────────────────────────────────────────────

async function headersOnlyCheck(client, item, existingPath, cache, integrity, results) {
  const info    = await fetchDetailPage(client, item).catch(() => null);
  const relPath = path.relative(path.join(__dirname, '..'), existingPath);
  if (!info?.url) {
    console.log(`    \u23ED\uFE0F  Cached (no URL to HEAD): ${formatCacheInfo(cache, integrity)}`);
    console.log(`    \uD83D\uDCC4 ${relPath}`);
    results.skipped.push({ etsiNumber: item.etsiNumber, file: existingPath });
    return;
  }
  const check = await checkRemoteChanged(info.url, existingPath);
  if (check.changed === true) {
    console.log(`    \uD83D\uDD04 CHANGED: ${check.reason}`);
    console.log(`    \uD83D\uDCC4 ${relPath}`);
    results.changed.push({ etsiNumber: item.etsiNumber, reason: check.reason, url: info.url, filePath: existingPath });
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

// ── Network helpers ───────────────────────────────────────────────────────────

/**
 * Fetch the work-item detail page and extract all useful data in one pass.
 *
 * Strategy:
 *   1. Try public Report_WorkItem.asp?WKI_ID=... (no login, cleaner HTML)
 *      → found download link or STOPPED  →  done
 *      → no download link                →  fall through
 *   2. Authenticated portal detailUrl
 *      → may expose draft .docx / .zip links
 *
 * Returns:
 *   { url, type, $, html, responseHeaders, status, wkiId, usedLogin, stopped? }
 *   null  — no WKI_ID and no detailUrl
 */
async function fetchDetailPage(client, item) {
  const wkiId     = extractWkiId(item.detailUrl) ?? extractWkiId(item.wkiId);
  const publicUrl = publicReportUrl(wkiId);
  const portalUrl = item.detailUrl ? cleanUrl(item.detailUrl) : null;

  // 1. Public URL
  if (publicUrl) {
    try {
      const res = await fetch(publicUrl, {
        headers: { 'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html' },
        redirect: 'follow',
      });
      if (res.ok) {
        const responseHeaders = snapshotHeaders(res);
        const html   = await res.text();
        const parsed = parseDetailHtml(html, wkiId, responseHeaders, false);
        if (parsed.url || parsed.stopped) return parsed;
        // no download link found on public page → try authenticated
      }
    } catch { /* network error — fall through */ }
  }

  // 2. Authenticated portal fallback
  if (!portalUrl) return null;
  const res = await client.fetch(portalUrl, { headers: client.getDefaultHeaders() });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const responseHeaders = snapshotHeaders(res);
  const html = await res.text();
  return parseDetailHtml(html, wkiId, responseHeaders, true);
}

/**
 * Parse a work-item detail HTML page.
 * Extracts: status, STOPPED flag, best download URL+type.
 */
function parseDetailHtml(html, wkiId, responseHeaders, usedLogin) {
  const $      = cheerio.load(html);
  const status = extractStatus(html);

  if (/\bSTOPPED\b/.test(html)) {
    return { stopped: true, $, html, responseHeaders, status, wkiId, usedLogin };
  }

  let downloadUrl = null, downloadType = null;

  // Published PDF on etsi.org/deliver (public CDN, preferred)
  $('a[href*="www.etsi.org/deliver"]').each((_, el) => {
    const href = $(el).attr('href');
    if (href?.includes('.pdf')) { downloadUrl = href; downloadType = 'pdf'; }
  });

  // PDA download
  if (!downloadUrl) $('a[href*="pda.etsi.org"]').each((_, el) => {
    const href = $(el).attr('href');
    if (href) { downloadUrl = href; downloadType = 'pda'; }
  });

  // Generic .pdf or .zip on any etsi domain
  if (!downloadUrl) $('a').each((_, el) => {
    const href = $(el).attr('href') || '';
    if (!downloadUrl && href.includes('.pdf') && href.includes('etsi'))
      { downloadUrl = href; downloadType = 'pdf'; }
    else if (!downloadUrl && href.includes('.zip') && href.includes('etsi'))
      { downloadUrl = href; downloadType = 'zip'; }
  });

  // Docbox drafts: .docx / .doc / .pdf / .zip
  if (!downloadUrl) $('a[href*="docbox.etsi.org"]').each((_, el) => {
    const href = $(el).attr('href')?.trim();
    if (!href) return;
    if (href.includes('.docx') || href.includes('.doc'))
      { downloadUrl = href; downloadType = 'draft-docx'; return false; }
    if (href.includes('.pdf'))
      { downloadUrl = href; downloadType = 'draft-pdf'; return false; }
    if (href.includes('.zip'))
      { downloadUrl = href; downloadType = 'zip'; }
  });

  return { url: downloadUrl ?? null, type: downloadType, $, html, responseHeaders, status, wkiId, usedLogin };
}

/**
 * Extract work item status from raw HTML.
 * Text immediately after the "<!-- Status Last Update -->" comment.
 * e.g. "Work item adopted (2026-06-03)"
 */
function extractStatus(html) {
  const after = html.split('<!-- Status Last Update -->')[1];
  if (!after) return null;
  const win = after.slice(0, 500);
  const m   = win.match(/<(?:b|nobr)[^>]*>([^<]+)<\/(?:b|nobr)>/);
  if (m) return m[1].trim();
  const a   = win.match(/<a[^>]*>[\s\S]*?<b[^>]*><nobr>([^<]+)<\/nobr>/);
  if (a) return a[1].trim();
  return null;
}

function snapshotHeaders(response) {
  const tracked = ['etag', 'last-modified', 'content-length', 'content-type', 'cache-control'];
  const snap    = { 'x-downloaded-at': new Date().toISOString() };
  for (const h of tracked) {
    const v = response.headers.get(h);
    if (v) snap[h] = v;
  }
  return snap;
}

async function saveResponseHeaders(filePath, headerSnapshot) {
  if (!headerSnapshot) return;
  await fs.writeFile(headerCachePath(filePath), JSON.stringify(headerSnapshot, null, 2));
}

// ── Sidecar writers ────────────────────────────────────────────────────────────

/** Save full work item HTML → _workitems/<safe>.workitem.html */
async function saveWorkitemSidecar(html, wkiId, item) {
  if (!html) return null;
  try {
    const dir     = path.join(DOWNLOAD_PATH, '_workitems');
    await fs.mkdir(dir, { recursive: true });
    const safe    = item.etsiNumber.replace(/[^a-zA-Z0-9-_]/g, '_');
    const outPath = path.join(dir, `${safe}.workitem.html`);
    if (await fs.stat(outPath).then(() => true).catch(() => false)) return outPath;
    await fs.writeFile(outPath, html, 'utf-8');
    return outPath;
  } catch (err) {
    console.log(`    \u26A0\uFE0F  Could not save workitem sidecar: ${err.message}`);
    return null;
  }
}

/** Save schedule/milestone table → _schedules/<safe>.schedule.html */
async function saveScheduleSidecar($, wkiId, item) {
  if (!$) return null;
  try {
    const dir     = path.join(DOWNLOAD_PATH, '_schedules');
    await fs.mkdir(dir, { recursive: true });
    const safe    = item.etsiNumber.replace(/[^a-zA-Z0-9-_]/g, '_');
    const outPath = path.join(dir, `${safe}.schedule.html`);
    if (await fs.stat(outPath).then(() => true).catch(() => false)) return outPath;

    let scheduleTable = null;
    $('table').each((_, tbl) => {
      const text = $(tbl).text().toLowerCase();
      if (text.includes('milestone') || text.includes('stage') || text.includes('target'))
        scheduleTable = $(tbl);
    });

    const stoppedCtx = [];
    $('*').each((_, el) => {
      const txt = $(el).children().length === 0 ? $(el).text().trim() : '';
      if (txt.toUpperCase().includes('STOPPED')) stoppedCtx.push($(el).parent().html()?.trim() ?? txt);
    });

    const tableHtml = scheduleTable ? scheduleTable.html() : null;
    const payload = [
      `<!-- etsiNumber: ${item.etsiNumber} -->`,
      `<!-- wkiId: ${wkiId ?? 'unknown'} -->`,
      `<!-- detailUrl: ${item.detailUrl ?? ''} -->`,
      `<!-- savedAt: ${new Date().toISOString()} -->`,
      '',
      ...(stoppedCtx.length
        ? ['<!-- === STOPPED CONTEXT === -->', stoppedCtx.map(s => `<div class="stopped-ctx">${s}</div>`).join('\n'), '']
        : []),
      '<!-- === SCHEDULE TABLE === -->',
      tableHtml ? `<table class="schedule-table">${tableHtml}</table>` : '<!-- no schedule table found -->',
    ].join('\n');

    await fs.writeFile(outPath, payload, 'utf-8');
    return outPath;
  } catch (err) {
    console.log(`    \u26A0\uFE0F  Could not save schedule sidecar: ${err.message}`);
    return null;
  }
}

// ── File download ─────────────────────────────────────────────────────────────

async function downloadFile(client, info, item) {
  try {
    // Public ETSI delivery CDN needs no auth; everything else uses the session
    const isPublic = info.url.includes('www.etsi.org/deliver');
    const fetchFn  = isPublic ? fetch : client.fetch.bind(client);
    const response = await fetchFn(info.url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Accept':     'application/pdf,application/zip,application/octet-stream,*/*',
      },
    });
    if (!response.ok) return null;

    // Filename: content-disposition → URL path → safe fallback
    let filename = null;
    const cd = response.headers.get('content-disposition');
    if (cd) {
      const m = cd.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
      if (m) filename = m[1].replace(/["']/g, '');
    }
    if (!filename) filename = path.basename(new URL(info.url).pathname);
    if (!filename || filename === '' || filename === '/') {
      const safe = item.etsiNumber.replace(/[^a-zA-Z0-9-_]/g, '_');
      const ext  = { pdf: '.pdf', zip: '.zip', 'draft-docx': '.docx', 'draft-pdf': '.pdf', pda: '.pdf' }[info.type] || '.bin';
      filename   = `${safe}${ext}`;
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
    const filePath = path.join(targetDir, filename);
    await fs.writeFile(filePath, buffer);
    await saveHeaders(filePath, response);
    await saveUrlSidecar(filePath, info.url, info.type);

    return {
      label:    `${subDir}/${filename} (${formatBytes(buffer.length)})`,
      filePath,
      url:      info.url,
    };
  } catch (error) {
    console.error(`    Download error: ${error.message}`);
    return null;
  }
}

async function saveUrlSidecar(filePath, url, type) {
  const source = url.includes('www.etsi.org/deliver') ? 'etsi-delivery'
               : url.includes('docbox.etsi.org')       ? 'docbox'
               : url.includes('pda.etsi.org')           ? 'pda'
               : 'portal';
  await fs.writeFile(
    path.join(path.dirname(filePath), `.url.${path.basename(filePath)}`),
    JSON.stringify({ url, source, type, savedAt: new Date().toISOString() }, null, 2),
  );
}

downloadLatestSpecs().catch(console.error);
