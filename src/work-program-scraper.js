import * as cheerio from 'cheerio';
import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';
import { ETSIClient } from './etsi-client.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const BASE_URL = 'https://portal.etsi.org';

// ── Page-level sidecar helpers ────────────────────────────────────────────────
//
// Every list-page fetch stores two files in <downloadPath>/_pages/:
//   page_<N>.html            – raw HTML (re-used on 304 or cache hit)
//   .headers.page_<N>.json  – ETag / Last-Modified / downloaded-at
//
// On subsequent runs the scraper sends a conditional GET (If-None-Match /
// If-Modified-Since).  A 304 means "page unchanged" → we parse the cached HTML
// instead of downloading again.  This makes re-runs after nothing changed nearly
// instant and reduces load on the ETSI portal.

function pageHtmlPath(pagesDir, pageIndex) {
  return path.join(pagesDir, `page_${pageIndex}.html`);
}

function pageHeaderPath(pagesDir, pageIndex) {
  return path.join(pagesDir, `.headers.page_${pageIndex}.json`);
}

async function loadPageHeaders(pagesDir, pageIndex) {
  try {
    return JSON.parse(await fs.readFile(pageHeaderPath(pagesDir, pageIndex), 'utf-8'));
  } catch { return null; }
}

async function savePageSidecar(pagesDir, pageIndex, response, html) {
  await fs.writeFile(pageHtmlPath(pagesDir, pageIndex), html);
  const cache = { 'x-downloaded-at': new Date().toISOString() };
  for (const h of ['etag', 'last-modified', 'content-length', 'content-type', 'cache-control']) {
    const v = response.headers.get(h);
    if (v) cache[h] = v;
  }
  await fs.writeFile(pageHeaderPath(pagesDir, pageIndex), JSON.stringify(cache, null, 2));
}

async function loadCachedPage(pagesDir, pageIndex) {
  try {
    return await fs.readFile(pageHtmlPath(pagesDir, pageIndex), 'utf-8');
  } catch { return null; }
}

function conditionalHeaders(cache) {
  if (!cache) return {};
  const h = {};
  if (cache['etag'])          h['If-None-Match']     = cache['etag'];
  if (cache['last-modified']) h['If-Modified-Since'] = cache['last-modified'];
  return h;
}

// ── Main scraper ─────────────────────────────────────────────────────────────

export async function scrapeWorkProgram(client, downloadPath) {
  console.log('📋 Fetching ESI Work Program...\n');

  const pagesDir = path.join(downloadPath, '_pages');
  await fs.mkdir(pagesDir, { recursive: true });
  await fs.mkdir(path.join(downloadPath, 'debug'), { recursive: true });

  const allItems = [];
  let offset     = 0;
  let pageIndex  = 0;
  let totalItems = 0;
  let cachedPages = 0;

  // qNB_TO_DISPLAY=999: request all items in one shot.
  // The ETSI portal is a classic ASP app that silently caps the value;
  // worst case we get fewer rows and the additive loop fetches more pages.
  const PAGE_SIZE = 999;

  const baseUrl    = `${BASE_URL}/webapp/WorkProgram/Frame_WorkItemList.asp`;
  const baseParams = [
    'qSORT=HIGHVERSION',
    'qETSI_ALL=',
    'SearchPage=TRUE',
    'qTB_ID=607%3BESI',
    'qINCLUDE_SUB_TB=True',
    'qINCLUDE_MOVED_ON=',
    'qSTOP_FLG=',
    'qKEYWORD_BOOLEAN=',
    'qCLUSTER_BOOLEAN=',
    'qFREQUENCIES_BOOLEAN=',
    'qSTOPPING_OUTDATED=',
    'butSimple=Search',
    'includeNonActiveTB=FALSE',
    'includeSubProjectCode=',
    'qREPORT_TYPE=SUMMARY',
  ].join('&');

  // 24h grace period: if cached sidecar is younger than this, skip the network
  // request entirely and parse the cached HTML directly.
  const GRACE_MS = 24 * 60 * 60 * 1000;

  while (true) {
    const cached = await loadPageHeaders(pagesDir, pageIndex);
    const condHeaders = conditionalHeaders(cached);
    const cachedHtml = await loadCachedPage(pagesDir, pageIndex);
    const hasCache = Boolean(cached && cachedHtml);

    const label = `  Page ${pageIndex + 1} (offset ${offset}–${offset + PAGE_SIZE - 1})`;

    // ── Grace period check ────────────────────────────────────────────────
    if (hasCache && cached['x-downloaded-at']) {
      const age = Date.now() - new Date(cached['x-downloaded-at']).getTime();
      if (age < GRACE_MS) {
        const remainH = Math.round((GRACE_MS - age) / 36e5);
        console.log(`${label} → ⏱️  Grace period (cache ${Math.round(age/36e5)}h old, expires in ~${remainH}h)`);
        const pageItems = parseWorkItemList(cachedHtml);
        if (pageItems.length === 0) break;
        allItems.push(...pageItems);
        cachedPages++;
        offset    += pageItems.length;
        pageIndex += 1;
        if (totalItems > 0 && allItems.length >= totalItems) break;
        continue;
      }
    }

    const formData = new URLSearchParams();
    formData.append('qOFFSET',       offset.toString());
    formData.append('qNB_TO_DISPLAY', PAGE_SIZE.toString());
    // Only send SubmitNext after the first page (first page is a fresh Search)
    if (offset > 0) formData.append('SubmitNext', ' Next Page ');

    let html;
    let fromCache = false;

    const response = await client.fetch(`${baseUrl}?${baseParams}`, {
      method:  'POST',
      headers: {
        ...client.getDefaultHeaders(),
        'Content-Type': 'application/x-www-form-urlencoded',
        ...(hasCache ? condHeaders : {}),   // only send conditional headers if we have cached HTML
      },
      body: formData.toString(),
    });

    if (response.status === 304 && hasCache) {
      // Portal says nothing changed → re-use cached HTML
      html = await loadCachedPage(pagesDir, pageIndex);
      fromCache = true;
      cachedPages++;
      console.log(`${label} → ♻️  304 Not Modified (using cache)`);
    } else if (!response.ok) {
      throw new Error(`Failed to fetch work program page ${pageIndex + 1}: ${response.status}`);
    } else {
      html = await response.text();
      await savePageSidecar(pagesDir, pageIndex, response, html);
    }

    // First page: extract total and save debug copy
    if (pageIndex === 0) {
      await fs.writeFile(path.join(downloadPath, 'debug', 'work_program_list.html'), html);
      // Try "Found <b>N</b> Items" first, fall back to totalNrItems=N in detail links
      const totalMatch = html.match(/Found\s*<b>\s*(\d+)\s*<\/b>\s*Items/i)
                      ?? html.match(/totalNrItems=(\d+)/);
      if (totalMatch) {
        totalItems = parseInt(totalMatch[1]);
        console.log(`  Total work items reported by portal: ${totalItems}`);
        const expectedPages = Math.ceil(totalItems / PAGE_SIZE);
        console.log(`  Expecting ~${expectedPages} page(s) at ${PAGE_SIZE} items/page\n`);
      }
    }

    const pageItems = parseWorkItemList(html);

    if (pageItems.length === 0) {
      if (!fromCache) console.log(`${label} → 0 items, stopping.`);
      break;
    }

    allItems.push(...pageItems);
    const cacheNote = fromCache ? ' (cached)' : '';
    console.log(`${label} → ${pageItems.length} items${cacheNote} (total so far: ${allItems.length})`);

    offset     += pageItems.length;
    pageIndex  += 1;

    if (totalItems > 0 && allItems.length >= totalItems) break;

    // Shorter delay — we already halved requests by doubling page size.
    // Skip delay entirely when reading from cache.
    if (!fromCache) await sleep(150);
  }

  if (cachedPages > 0) {
    console.log(`\n♻️  ${cachedPages} page(s) served from cache (no network request needed)`);
  }
  console.log(`\n📝 Collected ${allItems.length} work items\n`);

  const outputPath = path.join(downloadPath, 'work_items.json');
  await fs.writeFile(outputPath, JSON.stringify(allItems, null, 2));
  console.log(`💾 Saved to ${outputPath}`);

  await createSummaryReport(allItems, downloadPath);

  return allItems;
}

// ── HTML parser ───────────────────────────────────────────────────────────────

function parseWorkItemList(html) {
  const $ = cheerio.load(html);
  const items = [];

  $('table.Table tr').each((_, row) => {
    const $row  = $(row);
    const cells = $row.find('td');

    if (cells.length !== 4)              return;
    if ($row.find('.RowHead').length > 0) return;

    const $idCell     = $(cells[0]);
    const $docCell    = $(cells[1]);
    const $titleCell  = $(cells[2]);
    const $statusCell = $(cells[3]);

    const idComment = $idCell.html()?.match(/<!--\s*(\d+)\s*-->/);
    const workItemId = idComment ? idComment[1] : null;
    if (!workItemId) return;

    const etsiLink   = $docCell.find('a[href*="Report_WorkItem"]').first();
    const etsiNumber = etsiLink.find('b').text().trim() || etsiLink.text().trim();

    const refMatch  = $docCell.text().match(/Ref\.\s*([A-Z]{2,4}\/ESI-\d+[a-zA-Z0-9]*)/);
    const reference = refMatch ? refMatch[1] : '';

    let title = '';
    $titleCell.find('b').each((_, el) => {
      const text = $(el).text().trim().replace(/\s+/g, ' ');
      if (text) title = text;
    });
    title = title
      .replace(/^Electronic Signatures and Trust Infrastructures \(ESI\);?\s*/i, '')
      .replace(/<br\s*\/?>/gi, ' ')
      .trim();

    const subtitleEl = $titleCell.find('font[color="#708090"]');
    const subtitle   = subtitleEl.text().trim();

    const stageEl   = $statusCell.find('i');
    const stageText = stageEl.text().trim();
    const stage     = stageText ||
      ($statusCell.text().includes('Publication') ? 'Publication' : '');

    const currentStatusMatch = $statusCell.text().match(/Current Status:\s*([^(]+)\((\d{4}-\d{2}-\d{2})\)/);
    const currentStatus = currentStatusMatch ? {
      status: currentStatusMatch[1].trim(),
      date:   currentStatusMatch[2],
    } : null;

    const nextStatusMatch = $statusCell.text().match(/Next Status:\s*([^(]+)\((\d{4}-\d{2}-\d{2})\)/);
    const nextStatus = nextStatusMatch ? {
      status: nextStatusMatch[1].trim(),
      date:   nextStatusMatch[2],
    } : null;

    const detailHref  = etsiLink.attr('href');
    const detailUrl   = detailHref
      ? (detailHref.startsWith('http') ? detailHref : `${BASE_URL}/webapp/WorkProgram/${detailHref}`)
      : null;

    const scheduleLink = $statusCell.find('a[href*="Report_Schedule"]').first();
    const scheduleHref = scheduleLink.attr('href');
    const scheduleUrl  = scheduleHref
      ? (scheduleHref.startsWith('http') ? scheduleHref : `${BASE_URL}/webapp/WorkProgram/${scheduleHref}`)
      : null;

    items.push({
      workItemId,
      etsiNumber,
      reference,
      title,
      subtitle,
      stage,
      currentStatus,
      nextStatus,
      detailUrl,
      scheduleUrl,
    });
  });

  return items;
}

// ── Summary ───────────────────────────────────────────────────────────────────

async function createSummaryReport(items, downloadPath) {
  const byType  = {};
  const byStage = {};

  for (const item of items) {
    const typeMatch = item.etsiNumber?.match(/^(TS|TR|EN|ES|EG)/i);
    const type      = typeMatch ? typeMatch[1].toUpperCase() : 'Unknown';
    if (!byType[type])  byType[type]  = [];
    byType[type].push(item);

    const stage = item.stage || 'Unknown';
    if (!byStage[stage]) byStage[stage] = [];
    byStage[stage].push(item);
  }

  const summary = {
    generatedAt: new Date().toISOString(),
    totalItems: items.length,
    byDocumentType: Object.fromEntries(Object.entries(byType).map(([k, v])  => [k, v.length])),
    byStage:        Object.fromEntries(Object.entries(byStage).map(([k, v]) => [k, v.length])),
    items: items.map(item => ({
      etsiNumber:    item.etsiNumber,
      reference:     item.reference,
      title:         item.title,
      subtitle:      item.subtitle,
      stage:         item.stage,
      currentStatus: item.currentStatus,
      nextStatus:    item.nextStatus,
    })),
  };

  await fs.writeFile(
    path.join(downloadPath, 'work_items_summary.json'),
    JSON.stringify(summary, null, 2),
  );

  console.log('\n📊 Summary:');
  console.log(`   Total work items: ${summary.totalItems}`);
  console.log('   By document type:', summary.byDocumentType);
  console.log('   By stage:', summary.byStage);
}

// ── Work-item analyze (creates esi_overview.json) ──────────────────────────────

export async function analyzeWorkItems(downloadPath) {
  const data = JSON.parse(await fs.readFile(path.join(downloadPath, 'work_items.json'), 'utf-8'));

  console.log(`\n📊 Analyzing work items...`);

  const byEtsiNumber = new Map();
  for (const item of data) {
    const key = item.etsiNumber;
    if (!key) continue;
    const existing = byEtsiNumber.get(key);
    if (!existing || parseInt(item.workItemId) > parseInt(existing.workItemId)) {
      byEtsiNumber.set(key, item);
    }
  }

  const activeWork = [];
  const published  = [];

  for (const item of byEtsiNumber.values()) {
    item.cleanStage = item.stage?.replace(/[\n\t]+/g, ' ').trim() || 'Unknown';
    if (item.cleanStage.includes('Drafting') || item.cleanStage.includes('approval')) {
      activeWork.push(item);
    } else {
      published.push(item);
    }
  }

  activeWork.sort((a, b) => {
    const da = a.nextStatus?.date || a.currentStatus?.date || '9999';
    const db = b.nextStatus?.date || b.currentStatus?.date || '9999';
    return da.localeCompare(db);
  });

  const groupByType = (items) => {
    const groups = {};
    for (const item of items) {
      const m    = item.etsiNumber?.match(/^(EN|TS|TR|ES|EG)/i);
      const type = m ? m[1].toUpperCase() : 'Other';
      if (!groups[type]) groups[type] = [];
      groups[type].push(item);
    }
    return groups;
  };

  const formatItem = (item) => ({
    etsiNumber:    item.etsiNumber,
    reference:     item.reference,
    title:         item.title,
    subtitle:      item.subtitle,
    stage:         item.cleanStage,
    currentStatus: item.currentStatus,
    nextStatus:    item.nextStatus,
    detailUrl:     item.detailUrl,
    scheduleUrl:   item.scheduleUrl,
  });

  const activeByType    = groupByType(activeWork);
  const publishedByType = groupByType(published);

  const overview = {
    generatedAt: new Date().toISOString(),
    statistics: {
      totalScraped:       data.length,
      uniqueDocuments:    byEtsiNumber.size,
      activeWorkItems:    activeWork.length,
      publishedDocuments: published.length,
    },
    activeWorkItems:    activeWork.map(formatItem),
    publishedDocuments: published.map(formatItem),
    byType: {
      active:    Object.fromEntries(Object.entries(activeByType).map(([k, v])    => [k, v.length])),
      published: Object.fromEntries(Object.entries(publishedByType).map(([k, v]) => [k, v.length])),
    },
  };

  const overviewPath = path.join(downloadPath, 'esi_overview.json');
  await fs.writeFile(overviewPath, JSON.stringify(overview, null, 2));
  console.log(`💾 Saved complete overview to downloads/esi_overview.json\n`);

  console.log('📈 Work Item Statistics:');
  console.log(`   Total scraped:        ${overview.statistics.totalScraped}`);
  console.log(`   Unique documents:     ${overview.statistics.uniqueDocuments}`);
  console.log(`   Active work items:    ${overview.statistics.activeWorkItems}`);
  console.log(`   Published documents:  ${overview.statistics.publishedDocuments}`);
  console.log('   Active by type:',    overview.byType.active);
  console.log('   Published by type:',  overview.byType.published);

  return overview;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}
