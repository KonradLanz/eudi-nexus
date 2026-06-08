import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';
import * as cheerio from 'cheerio';
import { ETSIClient } from '../src/etsi-client.js';
import { saveHeaders, loadHeaders, conditionalHeaders, formatCacheInfo } from '../src/http-cache.js';
import dotenv from 'dotenv';

// Load .env from project root
const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(__dirname, '..', '.env') });

const DOWNLOAD_PATH = '../downloads/specs';
const BASE_URL = 'https://portal.etsi.org';

async function downloadLatestSpecs() {
  console.log('\uD83D\uDCE5 ETSI Specification Downloader');
  console.log('================================\n');

  const args = process.argv.slice(2);
  const limitArg = args.find(a => a.startsWith('--limit='));
  const limit = limitArg ? parseInt(limitArg.split('=')[1]) : null;
  const publishedOnly = args.includes('--published-only');

  const workItems = JSON.parse(await fs.readFile('../downloads/esi_overview.json', 'utf-8'));
  const activeItems    = workItems.activeWorkItems;
  const publishedItems = workItems.publishedDocuments;
  console.log(`\uD83D\uDCCB Found ${activeItems.length} active + ${publishedItems.length} published items\n`);

  const client = new ETSIClient();
  console.log('\uD83D\uDD10 Logging in...');
  const loggedIn = await client.login(process.env.ETSI_USERNAME, process.env.ETSI_PASSWORD);
  if (!loggedIn) { console.error('\u274C Login failed'); process.exit(1); }
  console.log('\u2705 Login successful!\n');

  await fs.mkdir(DOWNLOAD_PATH, { recursive: true });

  // Build file index: sanitized-key → absolute file path
  const fileIndex = await buildDownloadIndex(DOWNLOAD_PATH);
  console.log(`\uD83D\uDDC2\uFE0F  Already downloaded: ${fileIndex.size} files\n`);

  const results = { success: [], skipped: [], revalidated: [], failed: [], noDownload: [] };

  const allItems = publishedOnly ? publishedItems : [...publishedItems, ...activeItems];
  const seen = new Set();
  const uniqueItems = allItems.filter(item => {
    const key = item.etsiNumber;
    if (!key || seen.has(key)) return false;
    seen.add(key); return true;
  });

  const itemsToProcess = limit ? uniqueItems.slice(0, limit) : uniqueItems;
  console.log(`\uD83D\uDCE6 Processing ${itemsToProcess.length} specifications${limit ? ` (limited to ${limit})` : ''}...\n`);

  for (let i = 0; i < itemsToProcess.length; i++) {
    const item = itemsToProcess[i];
    const progress = `[${i + 1}/${uniqueItems.length}]`;
    const safeKey = (item.etsiNumber || '').replace(/[^a-zA-Z0-9-_]/g, '_');
    console.log(`${progress} ${item.etsiNumber}`);

    // ── Idempotency: file exists ───────────────────────────────────────────────
    if (fileIndex.has(safeKey)) {
      const existingPath = fileIndex.get(safeKey);
      const cache = await loadHeaders(existingPath);
      if (cache) {
        console.log(`    \u23ED\uFE0F  Cached: ${formatCacheInfo(cache)}`);
      } else {
        console.log(`    \u23ED\uFE0F  Already downloaded (no HTTP cache sidecar)`);
      }
      results.skipped.push({ etsiNumber: item.etsiNumber, file: existingPath, cache });
      continue;
    }

    try {
      const downloadInfo = await fetchDownloadLink(client, item);

      if (downloadInfo && downloadInfo.url) {
        const result = await downloadFile(client, downloadInfo, item);
        if (result) {
          results.success.push({ etsiNumber: item.etsiNumber, ...result });
          console.log(`    \u2705 Downloaded: ${result.filename}`);
        } else {
          const attemptedUrl = downloadInfo.url;
          results.failed.push({ etsiNumber: item.etsiNumber, reason: 'Download failed', url: attemptedUrl });
          console.log(`    \u274C Download failed`);
          console.log(`    \uD83D\uDD17 URL: ${attemptedUrl}`);
        }
      } else {
        const attemptedUrl = item.detailUrl
          ? (item.detailUrl.startsWith('http') ? item.detailUrl : `${BASE_URL}${item.detailUrl}`)
          : '(no detail URL)';
        results.noDownload.push({ etsiNumber: item.etsiNumber, reason: 'No download link found', attemptedUrl });
        console.log(`    \u26A0\uFE0F  No download available`);
        console.log(`    \uD83D\uDD17 Tried: ${attemptedUrl}`);
      }

      await sleep(500);
    } catch (error) {
      const attemptedUrl = item.detailUrl
        ? (item.detailUrl.startsWith('http') ? item.detailUrl : `${BASE_URL}${item.detailUrl}`)
        : '(no detail URL)';
      results.failed.push({ etsiNumber: item.etsiNumber, reason: error.message, attemptedUrl });
      console.log(`    \u274C Error: ${error.message}`);
      console.log(`    \uD83D\uDD17 Tried: ${attemptedUrl}`);
    }
  }

  await fs.writeFile(
    path.join(DOWNLOAD_PATH, '_download_results.json'),
    JSON.stringify(results, null, 2)
  );

  console.log('\n\uD83D\uDCCA Download Summary:');
  console.log(`   \u2705 Success:               ${results.success.length}`);
  console.log(`   \u23ED\uFE0F  Skipped (cached):      ${results.skipped.length}`);
  console.log(`   \u274C Failed:                ${results.failed.length}`);
  console.log(`   \u26A0\uFE0F  No download available:  ${results.noDownload.length}`);
  console.log(`\n\uD83D\uDCBE Results saved to ${DOWNLOAD_PATH}/_download_results.json`);
}

/**
 * Returns a Map of sanitized-key → absolute file path.
 * Skips hidden dot-files (sidecar .headers.* files).
 */
async function buildDownloadIndex(basePath) {
  const index = new Map();
  try {
    const dirs = await fs.readdir(basePath, { withFileTypes: true });
    for (const entry of dirs) {
      if (!entry.isDirectory()) continue;
      const subDir = path.join(basePath, entry.name);
      const files = await fs.readdir(subDir);
      for (const file of files) {
        if (file.startsWith('.')) continue; // skip sidecar files
        const base = path.basename(file, path.extname(file));
        index.set(base, path.join(subDir, file));
      }
    }
  } catch { /* directory doesn't exist yet */ }
  return index;
}

async function fetchDownloadLink(client, item) {
  if (!item.detailUrl) return null;

  const response = await client.fetch(item.detailUrl, { headers: client.getDefaultHeaders() });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);

  const html = await response.text();
  const $ = cheerio.load(html);

  let downloadUrl = null;
  let downloadType = null;

  $('a[href*="www.etsi.org/deliver"]').each((_, el) => {
    const href = $(el).attr('href');
    if (href && href.includes('.pdf')) { downloadUrl = href; downloadType = 'pdf'; }
  });
  if (!downloadUrl) {
    $('a[href*="pda.etsi.org"]').each((_, el) => {
      const href = $(el).attr('href');
      if (href) { downloadUrl = href; downloadType = 'pda'; }
    });
  }
  if (!downloadUrl) {
    $('a').each((_, el) => {
      const href = $(el).attr('href') || '';
      if (href.includes('.pdf') && href.includes('etsi')) { downloadUrl = href; downloadType = 'pdf'; }
      else if (href.includes('.zip') && !downloadUrl) { downloadUrl = href; downloadType = 'zip'; }
    });
  }
  if (!downloadUrl) {
    $('a[href*="docbox.etsi.org"]').each((_, el) => {
      const href = $(el).attr('href')?.trim();
      if (href && (href.includes('.docx') || href.includes('.doc') || href.includes('.pdf'))) {
        downloadUrl = href;
        downloadType = href.includes('.pdf') ? 'draft-pdf' : 'draft-docx';
      }
    });
  }

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
    const contentDisposition = response.headers.get('content-disposition');
    if (contentDisposition) {
      const match = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
      if (match) filename = match[1].replace(/['"]/g, '');
    }
    if (!filename) {
      const urlPath = new URL(downloadInfo.url).pathname;
      filename = path.basename(urlPath);
    }
    if (!filename || filename === '' || filename === '/') {
      const safeNumber = item.etsiNumber.replace(/[^a-zA-Z0-9-_]/g, '_');
      const ext = { pdf: '.pdf', zip: '.zip', 'draft-docx': '.docx', 'draft-pdf': '.pdf' }[downloadInfo.type] || '.bin';
      filename = `${safeNumber}${ext}`;
    }
    filename = filename.replace(/[<>:"/\\|?*]/g, '_');

    const buffer = Buffer.from(await response.arrayBuffer());
    if (buffer.length < 1000) {
      console.log(`    \u26A0\uFE0F File too small (${buffer.length} bytes), skipping`);
      return null;
    }

    const typeMatch = item.etsiNumber?.match(/^(EN|TS|TR|ES|EG)/i);
    const subDir = typeMatch ? typeMatch[1].toUpperCase() : 'Other';
    const targetDir = path.join(DOWNLOAD_PATH, subDir);
    await fs.mkdir(targetDir, { recursive: true });

    const filePath = path.join(targetDir, filename);
    await fs.writeFile(filePath, buffer);

    // Save HTTP cache headers sidecar
    await saveHeaders(filePath, response);

    return {
      filename: `${subDir}/${filename} (${formatBytes(buffer.length)})`,
      filePath,
      url: downloadInfo.url
    };
  } catch (error) {
    console.error(`    Download error: ${error.message}`);
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

function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

downloadLatestSpecs().catch(console.error);
