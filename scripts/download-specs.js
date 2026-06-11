import fs from 'fs/promises';
import path from 'path';
import readline from 'readline';
import { fileURLToPath } from 'url';
import * as cheerio from 'cheerio';
import { ETSIClient } from '../src/etsi-client.js';
import {
  saveHeaders, loadHeaders, checkIntegrity, checkRemoteChanged,
  formatCacheInfo, formatBytes, headerCachePath
} from '../src/http-cache.js';
import dotenv from 'dotenv';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.join(__dirname, '..');
dotenv.config({ path: path.join(PROJECT_ROOT, '.env') });

const DOWNLOAD_PATH  = path.join(PROJECT_ROOT, 'downloads', 'specs');
const OVERVIEW_FILE  = path.join(PROJECT_ROOT, 'downloads', 'esi_overview.json');
const RESULTS_FILE   = path.join(DOWNLOAD_PATH, '_download_results.json');
const BASE_URL       = 'https://portal.etsi.org';
const PUBLIC_REPORT_BASE = 'https://portal.etsi.org/webapp/WorkProgram/Report_WorkItem.asp';

const SIDECAR_TTL_MS = 8 * 60 * 60 * 1000;

// ── Auth detection ─────────────────────────────────────────────────────────────────
const HAS_AUTH = Boolean(process.env.ETSI_USERNAME && process.env.ETSI_PASSWORD);

// ── CLI flags ────────────────────────────────────────────────────────────────────
const args               = process.argv.slice(2);
const limitArg           = args.find(a => a.startsWith('--limit='));
const LIMIT              = limitArg ? parseInt(limitArg.split('=')[1]) : null;
const PUBLISHED_ONLY     = args.includes('--published-only');
const HEADERS_ONLY       = args.includes('--headers-only');
const FORCE_UPDATE_CHECK = args.includes('--force-update-check');
const FORCE_DOWNLOAD     = args.includes('--force-download');
const REPAIR_WKI_IDS     = args.includes('--repair-wki-ids');
const YES_FLAG           = args.includes('--yes');
// When set: download DOCX from docbox for docs already cached as PDF.
// Only works with HAS_AUTH. Skips PDF download entirely.
const DOCX_ONLY          = args.includes('--docx-only');

// ── Usage / Copyright gate ───────────────────────────────────────────────────────
async function showUsageGate() {
  console.log('');
  console.log('━'.repeat(60));
  console.log(' 📥  ETSI Standards Downloader — Usage & Copyright Notice');
  console.log('━'.repeat(60));
  console.log('');
  console.log(' This tool downloads ETSI standards for local AI-assisted');
  console.log(' compliance research (Art. 3, Directive (EU) 2019/790 —');
  console.log(' TDM exception for scientific/technical research).');
  console.log('');
  console.log(' Intended use: STF 705 / EUDIW standards gap analysis.');
  console.log('');
  console.log(' ⚠️  Other users may need to contact ETSI for appropriate');
  console.log('     licensing before use: https://www.etsi.org/terms-of-use');
  console.log('');
  console.log(' No document content is redistributed by this tool.');
  console.log(' Auth credentials are never stored in the repository.');
  console.log('');
  if (HAS_AUTH) {
    if (DOCX_ONLY) {
      console.log(' 🔐 ETSI credentials detected → --docx-only mode');
      console.log('     DOCX fetched from docbox.etsi.org WorkItem pages only.');
      console.log('     PDF is NOT re-downloaded.');
    } else {
      console.log(' 🔐 ETSI credentials detected → PDF (deliver/) + DOCX (docbox, if linked in WorkItem)');
    }
  } else {
    console.log(' 🔓 No ETSI credentials → public PDFs only (deliver/)');
    console.log('    (Set ETSI_USERNAME + ETSI_PASSWORD in .env for DOCX access)');
    if (DOCX_ONLY) {
      console.log('');
      console.log(' ❌ --docx-only requires ETSI credentials. Aborting.');
      process.exit(1);
    }
  }
  console.log('');
  console.log('━'.repeat(60));
  console.log('');

  if (YES_FLAG) {
    console.log(' --yes flag detected, skipping confirmation.');
    console.log('');
    return;
  }

  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  const answer = await new Promise(resolve => {
    rl.question(' Do you confirm this intended use? [y/N]: ', ans => {
      rl.close();
      resolve(ans.trim().toLowerCase());
    });
  });

  if (answer !== 'y' && answer !== 'yes') {
    console.log('');
    console.log(' ❌ Aborted. No files were downloaded.');
    console.log('');
    process.exit(0);
  }
  console.log('');
}

// ── Helpers ──────────────────────────────────────────────────────────────────────

function publicReportUrl(wkiId) {
  if (!wkiId) return null;
  return `${PUBLIC_REPORT_BASE}?WKI_ID=${wkiId}`;
}

function cleanUrl(raw) {
  try {
    const u = new URL(raw.startsWith('http') ? raw : `${BASE_URL}${raw}`);
    for (const [k, v] of [...u.searchParams.entries()]) {
      if (!v || !v.trim()) u.searchParams.delete(k);
    }
    return u.toString();
  } catch { return raw; }
}

function extractWkiId(url) {
  if (!url) return null;
  const m = url.match(/WKI_ID=(\d+)/i);
  return m ? m[1] : null;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function isSidecarFresh(sidecarPath) {
  try {
    const raw        = JSON.parse(await fs.readFile(headerCachePath(sidecarPath), 'utf-8'));
    const downloaded = raw['x-downloaded-at'] ?? raw['x-checked-at'];
    if (!downloaded) return false;
    return (Date.now() - new Date(downloaded).getTime()) < SIDECAR_TTL_MS;
  } catch { return false; }
}

async function touchSidecarHeaders(sidecarPath) {
  try {
    const p = headerCachePath(sidecarPath);
    let existing = {};
    try { existing = JSON.parse(await fs.readFile(p, 'utf-8')); } catch { /* ok */ }
    existing['x-checked-at'] = new Date().toISOString();
    await fs.writeFile(p, JSON.stringify(existing, null, 2));
  } catch { /* non-fatal */ }
}

// ── Repair mode ───────────────────────────────────────────────────────────────────

async function repairWkiIds() {
  console.log('📥 ETSI Specification Downloader');
  console.log('================================\n');
  console.log('🔧 Mode: --repair-wki-ids (local only, no login, no downloads)\n');

  const wiDir = path.join(DOWNLOAD_PATH, '_workitems');
  let files;
  try { files = (await fs.readdir(wiDir)).filter(f => f.endsWith('.workitem.html')); }
  catch { console.log('⚠️  No _workitems directory found.'); process.exit(0); }

  const overviewIndex = new Map();
  try {
    const ov = JSON.parse(await fs.readFile(OVERVIEW_FILE, 'utf-8'));
    for (const item of [...(ov.activeWorkItems ?? []), ...(ov.publishedDocuments ?? [])]) {
      if (item.etsiNumber && item.detailUrl) overviewIndex.set(item.etsiNumber, item.detailUrl);
    }
  } catch { console.log('⚠️  Could not load esi_overview.json'); }

  const limited = LIMIT ? files.slice(0, LIMIT) : files;
  console.log(`📋 Found ${files.length} workitem sidecar(s)${LIMIT ? ` (limited to ${LIMIT})` : ''}\n`);

  let repaired = 0, alreadyOk = 0, noIdFound = 0, htmlFallback = 0;

  for (const file of limited) {
    const filePath = path.join(wiDir, file);
    const raw = await fs.readFile(filePath, 'utf-8');

    const headerMatch = raw.match(/<!-- wkiId: (\d+|unknown) -->/);
    const currentId   = headerMatch?.[1];
    if (currentId && currentId !== 'unknown') { alreadyOk++; continue; }

    const etsiNumber = file.replace('.workitem.html', '').replace(/_/g, '/');

    let foundId = null, source = null;
    const detailUrl = overviewIndex.get(etsiNumber);
    if (detailUrl) { foundId = extractWkiId(detailUrl); if (foundId) source = 'detailUrl'; }

    if (!foundId) {
      const bodyMatch = raw.match(/WKI_ID=(\d+)/i);
      if (bodyMatch) { foundId = bodyMatch[1]; source = 'html-body'; htmlFallback++;
        console.log(`  ⚠️  ${file} — not in overview, extracted WKI_ID from HTML body (unverified)`);
      }
    }
    if (!foundId) { console.log(`  ❓ ${file} — no WKI_ID found, skipping`); noIdFound++; continue; }

    const fixed = raw.replace(/<!-- wkiId: (\d+|unknown) -->/, `<!-- wkiId: ${foundId} -->`);
    await fs.writeFile(filePath, fixed, 'utf-8');
    const sourceLabel = source === 'detailUrl' ? '' : ' (from HTML body ⚠️)';
    console.log(`  🔧 ${file} — repaired: unknown → ${foundId}${sourceLabel}`);
    repaired++;
  }

  console.log(`\n📊 Repair Summary:`);
  console.log(`   🔧 Repaired:       ${repaired}`);
  if (htmlFallback) console.log(`   ⚠️  HTML fallback: ${htmlFallback}`);
  console.log(`   ✅ Already OK:   ${alreadyOk}`);
  console.log(`   ❓ No ID found:  ${noIdFound}`);
}

// ── Main ──────────────────────────────────────────────────────────────────────────

async function downloadLatestSpecs() {
  await showUsageGate();

  console.log('📥 ETSI Specification Downloader');
  console.log('================================\n');

  if (DOCX_ONLY)            console.log('📝 Mode: --docx-only (fetch DOCX from docbox WorkItem pages, skip PDF)\n');
  else if (FORCE_DOWNLOAD)  console.log('⚡ Mode: --force-download (full GET, ignores ETag/TTL)\n');
  else if (FORCE_UPDATE_CHECK) console.log('🔍 Mode: --force-update-check\n');
  else if (HEADERS_ONLY)    console.log('👁️  Mode: --headers-only\n');

  const workItems      = JSON.parse(await fs.readFile(OVERVIEW_FILE, 'utf-8'));
  const activeItems    = workItems.activeWorkItems;
  const publishedItems = workItems.publishedDocuments;
  console.log(`📋 Found ${activeItems.length} active + ${publishedItems.length} published items\n`);

  const client = new ETSIClient();

  // Auth is required for DOCX (docbox.etsi.org is always authenticated)
  if (HAS_AUTH) {
    console.log('🔐 Logging in...');
    const loggedIn = await client.login(process.env.ETSI_USERNAME, process.env.ETSI_PASSWORD);
    if (!loggedIn) { console.error('❌ Login failed'); process.exit(1); }
    console.log('✅ Login successful!\n');
  } else {
    console.log('🔓 No credentials — public PDF-only mode (deliver/)\n');
  }

  await fs.mkdir(DOWNLOAD_PATH, { recursive: true });

  const cachedByNumber = await loadCachedIndex();
  const diskIndex      = await buildDiskIndex(DOWNLOAD_PATH);
  console.log(`🗒️  Already downloaded: ${cachedByNumber.size} files (${diskIndex.size} on disk)\n`);

  const results = {
    success: [], redownloaded: [], skipped: [], stopped: [],
    changed: [], failed: [], noDownload: [],
  };

  const allItems    = PUBLISHED_ONLY ? publishedItems : [...publishedItems, ...activeItems];
  const seen        = new Set();
  const uniqueItems = allItems.filter(item => {
    if (!item.etsiNumber || seen.has(item.etsiNumber)) return false;
    seen.add(item.etsiNumber); return true;
  });
  const itemsToProcess = LIMIT ? uniqueItems.slice(0, LIMIT) : uniqueItems;
  console.log(`📦 Processing ${itemsToProcess.length} specifications${LIMIT ? ` (limited to ${LIMIT})` : ''}...\n`);

  for (let i = 0; i < itemsToProcess.length; i++) {
    const item     = itemsToProcess[i];
    const progress = `[${i + 1}/${uniqueItems.length}]`;
    console.log(`${progress} ${item.etsiNumber}`);

    // ── --docx-only mode: skip PDF logic entirely, just fetch DOCX ────────────
    if (DOCX_ONLY) {
      await fetchDocxOnly(client, item, results);
      await sleep(400);
      continue;
    }

    // ── Normal mode: PDF from deliver/ (idempotent) ──────────────────────
    let existingPath = cachedByNumber.get(item.etsiNumber) ?? null;
    if (!existingPath) existingPath = findInDiskIndex(diskIndex, item.etsiNumber);

    let needsDownload = false;
    let isRedownload  = false;

    if (existingPath) {
      const fileExists = await fs.stat(existingPath).then(() => true).catch(() => false);
      if (fileExists) {
        const cache     = await loadHeaders(existingPath);
        const integrity = await checkIntegrity(existingPath);
        const relPath   = path.relative(PROJECT_ROOT, existingPath);

        if (!integrity.ok && integrity.cachedSize !== null) {
          console.log(`    ⚠️  Integrity FAIL — re-downloading`);
          needsDownload = true; isRedownload = true;
        } else if (FORCE_DOWNLOAD) {
          console.log(`    🔄 --force-download — re-downloading`);
          needsDownload = true; isRedownload = true;
        } else if (HEADERS_ONLY || FORCE_UPDATE_CHECK) {
          await headersOnlyCheck(client, item, existingPath, cache, integrity, results);
          await sleep(200); continue;
        } else {
          const noHeaders = !cache ? ' ⚠️ no HTTP headers cached' : '';
          console.log(`    ⏭️  Cached: ${formatCacheInfo(cache, integrity)}${noHeaders}`);
          console.log(`    📄 ${relPath}`);
          results.skipped.push({ etsiNumber: item.etsiNumber, file: existingPath });
          continue;
        }
      } else { needsDownload = true; }
    } else { needsDownload = true; }

    if (HEADERS_ONLY) { console.log(`    ⏭️  Not cached — skipping (headers-only mode)`); continue; }
    if (!needsDownload) continue;

    try {
      const info = await fetchDetailPage(client, item);

      if (info) {
        // ── WorkItem sidecar (always save for all modes) ──────────────────
        await maybeUpdateWorkitemSidecar(info, item);
        const scPath = await saveScheduleSidecar(info.$, info.wkiId, item);
        if (scPath) await saveResponseHeaders(scPath, info.responseHeaders);

        if (info.status)    console.log(`    📌 Status: ${info.status}`);
        if (info.usedLogin) console.log(`    🔐 Used authenticated session`);
      }

      if (info?.stopped) {
        const scheduleUrl = info.wkiId
          ? `https://portal.etsi.org/eWPM/index.html#/schedule?WKI_ID=${info.wkiId}` : null;
        console.log(`    🛑 STOPPED work item — no file available`);
        if (scheduleUrl) console.log(`    📅 ${scheduleUrl}`);
        results.stopped.push({ etsiNumber: item.etsiNumber, wkiId: info.wkiId, scheduleUrl });
        await sleep(200); continue;
      }

      if (info?.url) {
        // ── PDF from deliver/ (always via public URL) ──────────────────
        const pdfResult = await downloadFile(client, info, item);

        // ── DOCX from docbox (only if link found in WorkItem HTML + auth) ────
        // Key rule: DOCX is NEVER derived from the deliver/ PDF URL.
        // It must be an explicit <a href="...docbox.etsi.org/..."> link
        // in the WorkItem page. If no such link exists → no DOCX.
        let docxResult = null;
        if (HAS_AUTH && info.docxUrl) {
          docxResult = await downloadFile(client, { ...info, url: info.docxUrl, type: 'docx' }, item);
        }

        if (pdfResult) {
          const entry = {
            etsiNumber: item.etsiNumber,
            filePath:   pdfResult.filePath,
            url:        pdfResult.url,
            status:     info.status ?? null,
            ...(docxResult ? { docxPath: docxResult.filePath, docxUrl: info.docxUrl } : {}),
          };
          if (isRedownload) { results.redownloaded.push(entry); console.log(`    🔄 Re-downloaded: ${pdfResult.label}`); }
          else              { results.success.push(entry);      console.log(`    ✅ Downloaded: ${pdfResult.label}`); }
          console.log(`    📄 ${path.relative(PROJECT_ROOT, pdfResult.filePath)}`);

          if (docxResult) {
            console.log(`    📝 DOCX: ${path.relative(PROJECT_ROOT, docxResult.filePath)}`);
            await saveFormatComparisonSidecar(pdfResult.filePath, docxResult.filePath, item);
          } else if (HAS_AUTH) {
            // Auth present but no docbox link found in WorkItem — that's fine,
            // not all documents have a DOCX on docbox.
            console.log(`    ℹ️  No DOCX link in WorkItem (docbox) — PDF only`);
          }
        } else {
          results.failed.push({ etsiNumber: item.etsiNumber, reason: 'Download returned no data', url: info.url });
          console.log(`    ❌ Download failed`);
        }
      } else {
        results.noDownload.push({ etsiNumber: item.etsiNumber, reason: 'No download link found' });
        console.log(`    ⚠️  No download link found`);
      }

      await sleep(500);
    } catch (error) {
      results.failed.push({ etsiNumber: item.etsiNumber, reason: error.message });
      console.log(`    ❌ Error: ${error.message}`);
    }
  }

  await saveResults(results);

  console.log('\n📊 Download Summary:');
  if (HEADERS_ONLY || FORCE_UPDATE_CHECK) {
    console.log(`   ✅ Up-to-date:            ${results.skipped.length}`);
    console.log(`   🔄 Changed on remote:     ${results.changed.length}`);
    console.log(`   ⚠️  No download:           ${results.noDownload.length}`);
  } else {
    console.log(`   ✅ Downloaded (new):      ${results.success.length}`);
    console.log(`   🔄 Re-downloaded:         ${results.redownloaded.length}`);
    console.log(`   ⏭️  Skipped (cached):      ${results.skipped.length}`);
    console.log(`   🛑 Stopped work items:    ${results.stopped.length}`);
    console.log(`   ❌ Failed:                ${results.failed.length}`);
    console.log(`   ⚠️  No download link:      ${results.noDownload.length}`);
  }
  console.log(`\n💾 Results saved to ${RESULTS_FILE}`);
}

// ── --docx-only mode: fetch missing DOCXs for already-cached documents ────────
//
// This mode is designed to be run AFTER a normal download pass.
// It reads existing WorkItem sidecars (or fetches fresh ones) to find
// docbox DOCX links, then downloads only the DOCX — no PDF re-fetch.

async function fetchDocxOnly(client, item, results) {
  // Check if DOCX already present (skip if .format-comparison.json says docx exists)
  const pdfPath = await findPdfPath(item);
  if (pdfPath) {
    const sidecarPath = pdfPath.replace(/\.[^.]+$/, '.format-comparison.json');
    try {
      const cmp = JSON.parse(await fs.readFile(sidecarPath, 'utf-8'));
      if (cmp.docxPath) {
        const docxAbs = path.join(PROJECT_ROOT, cmp.docxPath);
        const exists  = await fs.stat(docxAbs).then(() => true).catch(() => false);
        if (exists && !FORCE_DOWNLOAD) {
          console.log(`    ⏭️  DOCX already present: ${cmp.docxPath}`);
          results.skipped.push({ etsiNumber: item.etsiNumber });
          return;
        }
      }
    } catch { /* no sidecar yet — proceed */ }
  }

  // Fetch WorkItem page to get docbox DOCX link
  let info;
  try {
    info = await fetchDetailPage(client, item);
  } catch (err) {
    console.log(`    ❌ WorkItem fetch error: ${err.message}`);
    results.failed.push({ etsiNumber: item.etsiNumber, reason: err.message });
    return;
  }

  if (!info?.docxUrl) {
    console.log(`    ℹ️  No DOCX link in WorkItem (docbox) — skipping`);
    results.noDownload.push({ etsiNumber: item.etsiNumber, reason: 'No docbox DOCX link in WorkItem' });
    return;
  }

  // Always update WorkItem sidecar in this mode (we just fetched it)
  await maybeUpdateWorkitemSidecar(info, item);

  const docxResult = await downloadFile(client, { ...info, url: info.docxUrl, type: 'docx' }, item);
  if (!docxResult) {
    console.log(`    ❌ DOCX download failed`);
    results.failed.push({ etsiNumber: item.etsiNumber, reason: 'DOCX download returned no data', url: info.docxUrl });
    return;
  }

  console.log(`    📝 DOCX: ${path.relative(PROJECT_ROOT, docxResult.filePath)}`);

  // Update / create format-comparison sidecar
  if (pdfPath) {
    await saveFormatComparisonSidecar(pdfPath, docxResult.filePath, item);
  } else {
    console.log(`    ⚠️  No paired PDF found on disk — format-comparison sidecar not written`);
  }

  results.success.push({ etsiNumber: item.etsiNumber, docxPath: docxResult.filePath, docxUrl: info.docxUrl });
}

async function findPdfPath(item) {
  const diskIndex = await buildDiskIndex(DOWNLOAD_PATH);
  return findInDiskIndex(diskIndex, item.etsiNumber) ?? null;
}

// ── Cache index helpers ────────────────────────────────────────────────────────

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
    const re   = new RegExp(`(?<![0-9])0*${num}${part ? part.replace('-', '-0*') : ''}(?![0-9])`);
    for (const [name, filePath] of diskIndex) { if (re.test(name)) return filePath; }
    if (part) {
      const reFb = new RegExp(`(?<![0-9])0*${num}(?![0-9])`);
      for (const [name, filePath] of diskIndex) { if (reFb.test(name)) return filePath; }
    }
  }
  const allDigits = etsiNumber.replace(/[^0-9]/g, '');
  for (const len of [7, 6]) {
    const digits = allDigits.slice(0, len);
    if (digits.length < len) continue;
    const re = new RegExp(`(?<![0-9])0*${digits.replace(/^0+/, '')}(?![0-9])`);
    for (const [name, filePath] of diskIndex) { if (re.test(name)) return filePath; }
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

// ── Headers-only / force-update-check ───────────────────────────────────────────

async function headersOnlyCheck(client, item, existingPath, cache, integrity, results) {
  const info    = await fetchDetailPage(client, item).catch(() => null);
  const relPath = path.relative(PROJECT_ROOT, existingPath);
  if (!info?.url) {
    console.log(`    ⏭️  Cached (no URL to HEAD): ${formatCacheInfo(cache, integrity)}`);
    console.log(`    📄 ${relPath}`);
    results.skipped.push({ etsiNumber: item.etsiNumber, file: existingPath });
    return;
  }
  const check = await checkRemoteChanged(info.url, existingPath);
  if (check.changed === true) {
    console.log(`    🔄 CHANGED: ${check.reason}`);
    results.changed.push({ etsiNumber: item.etsiNumber, reason: check.reason, url: info.url, filePath: existingPath });
  } else if (check.changed === false) {
    await touchSidecarHeaders(existingPath);
    console.log(`    ✅ Up-to-date: ${formatCacheInfo(cache, integrity)}`);
    results.skipped.push({ etsiNumber: item.etsiNumber, file: existingPath });
  } else {
    console.log(`    ❓ Unverifiable: ${check.reason}`);
    results.skipped.push({ etsiNumber: item.etsiNumber, file: existingPath, note: check.reason });
  }
  console.log(`    📄 ${relPath}`);
}

// ── Network helpers ──────────────────────────────────────────────────────────────────

async function fetchDetailPage(client, item) {
  const wkiId     = extractWkiId(item.detailUrl) ?? extractWkiId(item.wkiId);
  const publicUrl = publicReportUrl(wkiId);
  const portalUrl = item.detailUrl ? cleanUrl(item.detailUrl) : null;

  // Try public WorkItem report first (no auth needed for PDF link discovery)
  if (publicUrl) {
    try {
      const res = await fetch(publicUrl, { headers: { 'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html' }, redirect: 'follow' });
      if (res.ok) {
        const responseHeaders = snapshotHeaders(res);
        const html   = await res.text();
        const parsed = parseDetailHtml(html, wkiId, responseHeaders, false);
        // For docbox links we need the authenticated portal page, not the public report.
        // If we don't need DOCX (no auth) or already found a docbox link, we're done.
        if (parsed.url || parsed.stopped) {
          if (!HAS_AUTH || parsed.docxUrl) return parsed;
          // Has auth + no docbox link in public report → fall through to portal
        }
      }
    } catch { /* fall through */ }
  }

  if (!portalUrl) return null;
  // Authenticated portal page: used for docbox DOCX link discovery
  const res = await client.fetch(portalUrl, { headers: client.getDefaultHeaders() });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const responseHeaders = snapshotHeaders(res);
  const html = await res.text();
  return parseDetailHtml(html, wkiId, responseHeaders, true);
}

function parseDetailHtml(html, wkiId, responseHeaders, usedLogin) {
  const $      = cheerio.load(html);
  const status = extractStatus(html);

  if (/\bSTOPPED\b/.test(html)) return { stopped: true, $, html, responseHeaders, status, wkiId, usedLogin };

  let downloadUrl = null, downloadType = null, docxUrl = null;

  // ── Primary: published PDF from www.etsi.org/deliver (always public) ──────
  $('a[href*="www.etsi.org/deliver"]').each((_, el) => {
    const href = $(el).attr('href');
    if (href?.includes('.pdf')) { downloadUrl = href; downloadType = 'pdf'; }
  });

  // ── DOCX: only from docbox.etsi.org (authenticated, explicit link) ────────
  // IMPORTANT: DOCX is NEVER derived from the deliver/ PDF URL.
  // It must be an explicit <a href="docbox.etsi.org/..."> in the WorkItem page.
  // The docbox link format is: https://docbox.etsi.org/ESI/.../...v010101.docx
  if (HAS_AUTH) {
    $('a[href*="docbox.etsi.org"]').each((_, el) => {
      const href = $(el).attr('href')?.trim();
      if (!href) return;
      if ((href.endsWith('.docx') || href.endsWith('.doc')) && !docxUrl) {
        docxUrl = href;
        return false; // first match only
      }
    });
  }
  // NOTE: No URL-derive fallback (deliver/ .pdf → .docx).
  // That URL never exists — removed to avoid misleading 404 attempts.

  // ── Fallback sources for PDF ────────────────────────────────────────────
  if (!downloadUrl) $('a[href*="pda.etsi.org"]').each((_, el) => {
    const href = $(el).attr('href');
    if (href) { downloadUrl = href; downloadType = 'pda'; }
  });
  if (!downloadUrl) $('a').each((_, el) => {
    const href = $(el).attr('href') || '';
    if (!downloadUrl && href.includes('.pdf') && href.includes('etsi')) { downloadUrl = href; downloadType = 'pdf'; }
    else if (!downloadUrl && href.includes('.zip') && href.includes('etsi')) { downloadUrl = href; downloadType = 'zip'; }
  });
  // Draft fallback: docbox PDF only when no other source found (not DOCX here)
  if (!downloadUrl) $('a[href*="docbox.etsi.org"]').each((_, el) => {
    const href = $(el).attr('href')?.trim();
    if (!href) return;
    if (href.endsWith('.pdf'))  { downloadUrl = href; downloadType = 'draft-pdf'; return false; }
    if (href.endsWith('.zip'))  { downloadUrl = href; downloadType = 'zip'; }
  });

  return { url: downloadUrl ?? null, type: downloadType, docxUrl, $, html, responseHeaders, status, wkiId, usedLogin };
}

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
  for (const h of tracked) { const v = response.headers.get(h); if (v) snap[h] = v; }
  return snap;
}

async function saveResponseHeaders(filePath, headerSnapshot) {
  if (!headerSnapshot) return;
  await fs.writeFile(headerCachePath(filePath), JSON.stringify(headerSnapshot, null, 2));
}

// ── WorkItem sidecar helpers ───────────────────────────────────────────────────

async function maybeUpdateWorkitemSidecar(info, item) {
  if (!info?.html) return;
  const safe    = item.etsiNumber.replace(/[^a-zA-Z0-9-_]/g, '_');
  const wiDir   = path.join(DOWNLOAD_PATH, '_workitems');
  const wiPath  = path.join(wiDir, `${safe}.workitem.html`);
  await fs.mkdir(wiDir, { recursive: true });

  const wiExists = await fs.stat(wiPath).then(() => true).catch(() => false);
  const fresh    = wiExists && !FORCE_UPDATE_CHECK && !FORCE_DOWNLOAD
                   ? await isSidecarFresh(wiPath) : false;

  if (fresh) {
    console.log(`    ⏭️  WorkItem sidecar fresh`);
  } else if (FORCE_UPDATE_CHECK && wiExists) {
    const wiUrl = publicReportUrl(info.wkiId);
    if (wiUrl) {
      const check = await checkRemoteChanged(wiUrl, wiPath).catch(() => ({ changed: null }));
      if (check.changed === true) {
        console.log(`    🔄 WorkItem changed — refreshing sidecar`);
        await writeWorkitemSidecar(wiPath, info.html, info.wkiId, item);
        await saveResponseHeaders(wiPath, info.responseHeaders);
      } else {
        await touchSidecarHeaders(wiPath);
      }
    }
  } else if (FORCE_DOWNLOAD || !wiExists) {
    await writeWorkitemSidecar(wiPath, info.html, info.wkiId, item);
    await saveResponseHeaders(wiPath, info.responseHeaders);
  } else {
    if (!wiExists) {
      await writeWorkitemSidecar(wiPath, info.html, info.wkiId, item);
      await saveResponseHeaders(wiPath, info.responseHeaders);
    }
  }
}

async function writeWorkitemSidecar(outPath, html, wkiId, item) {
  await fs.writeFile(outPath, [
    `<!-- etsiNumber: ${item.etsiNumber} -->`,
    `<!-- wkiId: ${wkiId ?? 'unknown'} -->`,
    `<!-- savedAt: ${new Date().toISOString()} -->`,
    '', html,
  ].join('\n'), 'utf-8');
}

// ── Format comparison sidecar ────────────────────────────────────────────────────
//
// Written next to the PDF when both formats downloaded successfully.
// Recommendation heuristic: DOCX if ≥ 20 KB, else PDF.

async function saveFormatComparisonSidecar(pdfPath, docxPath, item) {
  try {
    const [pdfStat, docxStat] = await Promise.all([
      fs.stat(pdfPath).catch(() => null),
      fs.stat(docxPath).catch(() => null),
    ]);
    const pdfBytes  = pdfStat?.size  ?? 0;
    const docxBytes = docxStat?.size ?? 0;
    const recommendation = docxBytes >= 20_000 ? 'docx' : pdfBytes > 0 ? 'pdf' : 'unknown';

    const payload = {
      etsiNumber:     item.etsiNumber,
      pdfPath:        path.relative(PROJECT_ROOT, pdfPath),
      docxPath:       path.relative(PROJECT_ROOT, docxPath),
      pdfBytes,
      docxBytes,
      downloadedAt:   new Date().toISOString(),
      recommendation,
    };

    const sidecarPath = pdfPath.replace(/\.[^.]+$/, '.format-comparison.json');
    await fs.writeFile(sidecarPath, JSON.stringify(payload, null, 2));
    console.log(`    📊 Format: ${recommendation === 'docx' ? '✅ DOCX preferred' : '⚠️  PDF fallback'} (DOCX ${formatBytes(docxBytes)} / PDF ${formatBytes(pdfBytes)})`);
  } catch (err) {
    console.log(`    ⚠️  Could not write format-comparison sidecar: ${err.message}`);
  }
}

// ── Schedule sidecar ────────────────────────────────────────────────────────────────

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
      ...(stoppedCtx.length ? ['<!-- === STOPPED CONTEXT === -->', stoppedCtx.map(s => `<div class="stopped-ctx">${s}</div>`).join('\n'), ''] : []),
      '<!-- === SCHEDULE TABLE === -->',
      tableHtml ? `<table class="schedule-table">${tableHtml}</table>` : '<!-- no schedule table found -->',
    ].join('\n');
    await fs.writeFile(outPath, payload, 'utf-8');
    return outPath;
  } catch (err) {
    console.log(`    ⚠️  Could not save schedule sidecar: ${err.message}`);
    return null;
  }
}

// ── File download ───────────────────────────────────────────────────────────────────

async function downloadFile(client, info, item) {
  try {
    // deliver/ PDFs are public; docbox requires the authenticated session
    const isPublicDelivery = info.url.includes('www.etsi.org/deliver');
    const fetchFn = isPublicDelivery ? fetch : client.fetch.bind(client);
    const response = await fetchFn(info.url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Accept':     'application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/zip,application/octet-stream,*/*',
      },
    });
    if (!response.ok) return null;

    let filename = null;
    const cd = response.headers.get('content-disposition');
    if (cd) { const m = cd.match(/filename[^;=\n]*=((['"']).*?\2|[^;\n]*)/); if (m) filename = m[1].replace(/["']/g, ''); }
    if (!filename) filename = path.basename(new URL(info.url).pathname);
    if (!filename || filename === '' || filename === '/') {
      const safe = item.etsiNumber.replace(/[^a-zA-Z0-9-_]/g, '_');
      const ext  = { pdf: '.pdf', zip: '.zip', 'draft-docx': '.docx', docx: '.docx', 'draft-pdf': '.pdf', pda: '.pdf' }[info.type] || '.bin';
      filename   = `${safe}${ext}`;
    }
    filename = filename.replace(/[<>:"/\\|?*]/g, '_');

    const buffer = Buffer.from(await response.arrayBuffer());
    if (buffer.length < 1000) { console.log(`    ⚠️ File too small (${buffer.length} bytes) — skipping`); return null; }

    const typeMatch = item.etsiNumber?.match(/^(EN|TS|TR|ES|EG)/i);
    const subDir    = typeMatch ? typeMatch[1].toUpperCase() : 'Other';
    const targetDir = path.join(DOWNLOAD_PATH, subDir);
    await fs.mkdir(targetDir, { recursive: true });
    const filePath = path.join(targetDir, filename);
    await fs.writeFile(filePath, buffer);
    await saveHeaders(filePath, response);
    await saveUrlSidecar(filePath, info.url, info.type);

    return { label: `${subDir}/${filename} (${formatBytes(buffer.length)})`, filePath, url: info.url };
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

// ── Entry point ────────────────────────────────────────────────────────────────────

if (REPAIR_WKI_IDS) {
  repairWkiIds().catch(console.error);
} else {
  downloadLatestSpecs().catch(console.error);
}
