/**
 * retry-failed.js
 *
 * Retries all "noDownload" entries from a previous download run.
 * For each item it:
 *   1. Re-fetches the ETSI portal detail page (with login)
 *   2. Detects STOPPED work items and prints a warning
 *   3. Extracts WKI_ID → builds eWPM schedule link
 *   4. Parses the Milestone/Achieved table: last achieved + next 3 targets
 *   5. Falls back to direct ETSI delivery directory crawl
 *      (https://www.etsi.org/deliver/etsi_XX/NNNNNN/)
 *
 * Usage:
 *   npm run retry-failed
 *   npm run retry-failed -- --limit=10
 */

import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';
import * as cheerio from 'cheerio';
import { ETSIClient } from '../src/etsi-client.js';
import dotenv from 'dotenv';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(__dirname, '..', '.env') });

const DOWNLOAD_PATH = '../downloads/specs';
const BASE_URL     = 'https://portal.etsi.org';
const DELIVER_URL  = 'https://www.etsi.org/deliver';
const SCHEDULE_URL = 'https://portal.etsi.org/eWPM/index.html#/schedule';

// Map ETSI number prefix → deliver subdirectory name
const DELIVER_DIR = {
  EN:  'etsi_en',
  TS:  'etsi_ts',
  TR:  'etsi_tr',
  ES:  'etsi_es',
  EG:  'etsi_eg',
  SR:  'etsi_sr',
};

async function main() {
  console.log('\uD83D\uDD04 ETSI Retry-Failed Downloader');
  console.log('================================\n');

  const args = process.argv.slice(2);
  const limitArg = args.find(a => a.startsWith('--limit='));
  const limit = limitArg ? parseInt(limitArg.split('=')[1]) : null;

  // Load previous results
  const resultsPath = path.join(DOWNLOAD_PATH, '_download_results.json');
  const prev = JSON.parse(await fs.readFile(resultsPath, 'utf-8'));
  const candidates = [...(prev.noDownload || []), ...(prev.failed || [])];

  if (candidates.length === 0) {
    console.log('\u2705 Nothing to retry!');
    return;
  }

  const items = limit ? candidates.slice(0, limit) : candidates;
  console.log(`\uD83D\uDCCB ${candidates.length} items to retry${limit ? ` (limited to ${limit})` : ''}\n`);

  // Login
  const client = new ETSIClient();
  console.log('\uD83D\uDD10 Logging in...');
  const loggedIn = await client.login(process.env.ETSI_USERNAME, process.env.ETSI_PASSWORD);
  if (!loggedIn) { console.error('\u274C Login failed'); process.exit(1); }
  console.log('\u2705 Login successful!\n');

  await fs.mkdir(DOWNLOAD_PATH, { recursive: true });

  const results = { success: [], stopped: [], stillFailed: [] };

  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    console.log(`[${i + 1}/${items.length}] ${item.etsiNumber}`);

    const detailUrl = item.attemptedUrl || null;

    // ── 1. Fetch detail page ─────────────────────────────────────────────────
    let html = null;
    let wkiId = null;

    if (detailUrl && detailUrl !== '(no detail URL)') {
      // Extract WKI_ID from URL
      const wkiMatch = detailUrl.match(/WKI_ID=(\d+)/i);
      if (wkiMatch) wkiId = wkiMatch[1];

      try {
        const resp = await client.fetch(detailUrl, { headers: client.getDefaultHeaders() });
        if (resp.ok) html = await resp.text();
      } catch (e) {
        console.log(`    \u26A0\uFE0F  Could not fetch detail page: ${e.message}`);
      }
    }

    // ── 2. STOPPED detection ─────────────────────────────────────────────────
    let isStopped = false;
    if (html) {
      if (/STOPPED|Work Item Has Been STOPPED/i.test(html)) {
        isStopped = true;
        console.log('    \uD83D\uDED1 STOPPED work item');
      }
    }

    // ── 3. Schedule link ──────────────────────────────────────────────────────
    const scheduleLink = wkiId
      ? `${SCHEDULE_URL}?WKI_ID=${wkiId}`
      : null;
    if (scheduleLink) {
      console.log(`    \uD83D\uDCC5 Schedule: ${scheduleLink}`);
    }

    // ── 4. Milestone table (from detail page HTML) ───────────────────────────
    if (html) {
      const milestones = parseMilestones(html);
      if (milestones.length > 0) {
        printMilestones(milestones);
      }
    }

    // ── 5. Stopped items: skip download, record and continue ─────────────────
    if (isStopped) {
      results.stopped.push({ ...item, scheduleLink });
      await sleep(300);
      continue;
    }

    // ── 6. Direct delivery fallback ───────────────────────────────────────────
    console.log('    \uD83D\uDD0D Trying direct delivery fallback...');
    const downloaded = await tryDeliveryFallback(client, item);
    if (downloaded) {
      console.log(`    \u2705 Downloaded via fallback: ${downloaded}`);
      results.success.push({ etsiNumber: item.etsiNumber, filename: downloaded, scheduleLink });
    } else {
      console.log(`    \u274C Still no download available`);
      if (detailUrl && detailUrl !== '(no detail URL)') {
        console.log(`    \uD83D\uDD17 Portal: ${detailUrl}`);
      }
      if (scheduleLink) {
        console.log(`    \uD83D\uDD17 Schedule: ${scheduleLink}`);
      }
      results.stillFailed.push({ ...item, scheduleLink });
    }

    await sleep(500);
  }

  // ── Save updated results ──────────────────────────────────────────────────
  const retryResultsPath = path.join(DOWNLOAD_PATH, '_retry_results.json');
  await fs.writeFile(retryResultsPath, JSON.stringify(results, null, 2));

  console.log('\n\uD83D\uDCCA Retry Summary:');
  console.log(`   \u2705 Recovered:       ${results.success.length}`);
  console.log(`   \uD83D\uDED1 Stopped items:   ${results.stopped.length}`);
  console.log(`   \u274C Still failed:    ${results.stillFailed.length}`);
  console.log(`\n\uD83D\uDCBE Results saved to ${retryResultsPath}`);
}

// ─────────────────────────────────────────────────────────────────────────────
// Milestone parser
// Looks for a table with columns containing "Milestone" and "Achieved"
// Returns array of { milestone, target, achieved }
// ─────────────────────────────────────────────────────────────────────────────
function parseMilestones(html) {
  const $ = cheerio.load(html);
  const rows = [];

  $('table').each((_, table) => {
    const headers = [];
    $(table).find('tr').first().find('th, td').each((_, th) => {
      headers.push($(th).text().trim().toLowerCase());
    });

    const milestoneIdx = headers.findIndex(h => h.includes('milestone'));
    const targetIdx    = headers.findIndex(h => h.includes('target') || h.includes('planned'));
    const achievedIdx  = headers.findIndex(h => h.includes('achieved') || h.includes('actual'));

    if (milestoneIdx === -1 || achievedIdx === -1) return;

    $(table).find('tr').slice(1).each((_, tr) => {
      const cells = $(tr).find('td');
      const milestone = cells.eq(milestoneIdx).text().trim();
      const target    = targetIdx >= 0 ? cells.eq(targetIdx).text().trim() : '';
      const achieved  = cells.eq(achievedIdx).text().trim();
      if (milestone) rows.push({ milestone, target, achieved });
    });
  });

  return rows;
}

function printMilestones(rows) {
  // Find last achieved row
  const achievedRows = rows.filter(r => r.achieved && r.achieved !== '-' && r.achieved !== '');
  const lastAchieved = achievedRows[achievedRows.length - 1];

  // Find next 3 pending rows (no achieved date)
  const pendingRows = rows.filter(r => !r.achieved || r.achieved === '-' || r.achieved === '');
  const next3 = pendingRows.slice(0, 3);

  if (lastAchieved) {
    console.log(`    \u2714\uFE0F  Last achieved: ${lastAchieved.milestone} → ${lastAchieved.achieved}`);
  }
  for (const r of next3) {
    console.log(`    \uD83D\uDCC6 Next: ${r.milestone}${r.target ? ' (target: ' + r.target + ')' : ''}`);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Direct ETSI delivery fallback
// Constructs https://www.etsi.org/deliver/etsi_XX/NNNNNN/
// then crawls the directory listing for the latest PDF.
// ─────────────────────────────────────────────────────────────────────────────
async function tryDeliveryFallback(client, item) {
  const num = item.etsiNumber || '';

  // Parse prefix and number, e.g. "ES 201 862" → prefix=ES, number=201862
  // Also handles "TS 119 461", "EN 319 401", etc.
  const match = num.match(/^([A-Z]+)\s+([\d\s]+(?:-[\d]+)*)$/i);
  if (!match) return null;

  const prefix = match[1].toUpperCase();
  const rawNum = match[2].replace(/\s+/g, '').replace(/-/g, '_');
  const deliverDir = DELIVER_DIR[prefix];
  if (!deliverDir) return null;

  // Pad to 6 digits for the directory name
  const numDigits = rawNum.replace(/_/g, '');
  const dirNum = numDigits.padStart(6, '0');
  const dirUrl = `${DELIVER_URL}/${deliverDir}/${dirNum}/`;

  console.log(`    \uD83D\uDD17 Delivery dir: ${dirUrl}`);

  try {
    // Fetch the directory listing (no auth needed for etsi.org/deliver)
    const resp = await fetch(dirUrl, {
      headers: { 'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html' }
    });
    if (!resp.ok) return null;

    const html = await resp.text();
    const $ = cheerio.load(html);

    // Directory listing: find version subdirectories (e.g. 020101_020199/)
    const versionDirs = [];
    $('a[href]').each((_, el) => {
      const href = $(el).attr('href');
      if (href && /^\d+_\d+\/$/.test(href)) {
        versionDirs.push(href);
      }
    });

    if (versionDirs.length === 0) return null;

    // Take the last (highest/latest) version directory
    const latestVersionDir = versionDirs[versionDirs.length - 1];
    const versionUrl = `${dirUrl}${latestVersionDir}`;

    const vResp = await fetch(versionUrl, {
      headers: { 'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html' }
    });
    if (!vResp.ok) return null;

    const vHtml = await vResp.text();
    const $v = cheerio.load(vHtml);

    // Find PDF files in this version directory
    let pdfUrl = null;
    $v('a[href]').each((_, el) => {
      const href = $v(el).attr('href');
      if (href && href.toLowerCase().endsWith('.pdf')) {
        pdfUrl = href.startsWith('http') ? href : `${versionUrl}${href}`;
      }
    });

    if (!pdfUrl) return null;

    console.log(`    \uD83D\uDCC4 Found PDF: ${pdfUrl}`);

    // Download it
    const dlResp = await fetch(pdfUrl, {
      headers: {
        'User-Agent': 'Mozilla/5.0',
        'Accept': 'application/pdf,*/*'
      }
    });
    if (!dlResp.ok) return null;

    const buffer = Buffer.from(await dlResp.arrayBuffer());
    if (buffer.length < 1000) return null;

    // Save file
    const filename = path.basename(new URL(pdfUrl).pathname);
    const safeFilename = filename.replace(/[<>:"/\\|?*]/g, '_');
    const subDir = prefix;
    const targetDir = path.join(DOWNLOAD_PATH, subDir);
    await fs.mkdir(targetDir, { recursive: true });
    await fs.writeFile(path.join(targetDir, safeFilename), buffer);

    return `${subDir}/${safeFilename} (${formatBytes(buffer.length)})`;

  } catch (e) {
    console.log(`    \u26A0\uFE0F  Delivery fallback error: ${e.message}`);
    return null;
  }
}

function formatBytes(bytes) {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

main().catch(console.error);
