/**
 * enrich-titles.js
 *
 * Reads workitem sidecars (_workitems/*.workitem.html), extracts fullTitle
 * and etsiShortTitle, then generates a ≤4-word AI short title.
 *
 * Flags:
 *   --force                  re-run everything (re-extract + re-generate AI)
 *   --force-new-short-titles re-run AI only; keep extracted titles from disk
 *   --no-ai                  extract titles only, skip AI
 *   --limit=N                process first N sidecars
 */

import fs   from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';
import * as cheerio from 'cheerio';
import { isAvailable, bestModel, activeProvider, suggestShortTitle, detectTitleInconsistency }
  from '../src/local-ai.js';

const __dirname    = path.dirname(fileURLToPath(import.meta.url));
const SPECS_ROOT   = path.join(__dirname, '..', 'downloads', 'specs');
const WORKITEM_DIR = path.join(SPECS_ROOT, '_workitems');
const TITLES_DIR   = path.join(SPECS_ROOT, '_titles');

// ── CLI flags ─────────────────────────────────────────────────────────────────

const args               = process.argv.slice(2);
const FORCE              = args.includes('--force');               // re-run everything
const FORCE_SHORT_TITLES = args.includes('--force-new-short-titles'); // AI only, keep extracted data
const NO_AI              = args.includes('--no-ai');
const limitArg           = args.find(a => a.startsWith('--limit='));
const LIMIT              = limitArg ? parseInt(limitArg.split('=')[1]) : null;

// ── Title extraction ──────────────────────────────────────────────────────────

/**
 * Extract fullTitle and etsiShortTitle from a workitem sidecar HTML.
 *
 * ETSI HTML structure:
 *   <td class="Head1"><b>Title</b></td>
 *   <td class="Table" COLSPAN="6">
 *     <font class="Normal">Full title here </font>
 *     <BR><FONT Color="#708090">ETSI short title</FONT>
 *   </td>
 *
 * cheerio + htmlparser2 handle ETSI's broken HTML4 gracefully.
 */
function extractWorkitemTitles(html) {
  const $ = cheerio.load(html);

  let titleCell = null;
  $('b').each((_, el) => {
    if ($(el).text().trim() === 'Title') {
      titleCell = $(el).closest('tr').find('td[class="Table"], td.Table').first();
      return false;
    }
  });

  if (!titleCell || !titleCell.length) return { fullTitle: null, etsiShortTitle: null };

  const cellHtml = titleCell.html() ?? '';
  const parts    = cellHtml.split(/<br\s*\/?>/i);

  const fullTitle = parts[0]
    ? cheerio.load(parts[0]).text().replace(/\s+/g, ' ').trim() || null
    : null;

  let etsiShortTitle = null;
  if (parts[1]) {
    const $p = cheerio.load(parts[1]);
    $p('font').each((_, el) => {
      const color = ($p(el).attr('color') ?? '').toLowerCase();
      if (color === '#708090' || color === '708090') {
        const txt = $p(el).text().replace(/\s+/g, ' ').trim();
        if (txt) { etsiShortTitle = txt; return false; }
      }
    });
    if (!etsiShortTitle) {
      const txt = $p.text().replace(/[\u00a0\s]+/g, ' ').trim();
      if (txt.length > 3) etsiShortTitle = txt;
    }
  }

  return { fullTitle, etsiShortTitle };
}

// ── PDF title extraction ──────────────────────────────────────────────────────

async function extractPdfTitle(etsiNumber) {
  let urlSidecar = null;
  try {
    const subdirs = await fs.readdir(SPECS_ROOT, { withFileTypes: true });
    for (const d of subdirs) {
      if (!d.isDirectory() || d.name.startsWith('_')) continue;
      const dir   = path.join(SPECS_ROOT, d.name);
      const files = await fs.readdir(dir);
      for (const f of files) {
        if (!f.startsWith('.url.')) continue;
        const raw    = JSON.parse(await fs.readFile(path.join(dir, f), 'utf-8'));
        const digits = etsiNumber.replace(/[^0-9]/g, '');
        const inName = f.replace(/[^0-9]/g, '');
        if (digits.length >= 6 && inName.includes(digits.slice(0, 6)))
          { urlSidecar = { ...raw, filename: f.replace(/^\.url\./, '') }; break; }
      }
      if (urlSidecar) break;
    }
  } catch { /* downloads not yet available */ }

  if (!urlSidecar) return null;
  if (path.extname(urlSidecar.filename).toLowerCase() !== '.pdf') return null;

  try {
    const pdfParse = (await import('pdf-parse')).default;
    const subdirs  = await fs.readdir(SPECS_ROOT, { withFileTypes: true });
    for (const d of subdirs) {
      if (!d.isDirectory() || d.name.startsWith('_')) continue;
      const candidate = path.join(SPECS_ROOT, d.name, urlSidecar.filename);
      if (!await fs.stat(candidate).then(() => true).catch(() => false)) continue;
      const buf  = await fs.readFile(candidate);
      const data = await pdfParse(buf, { max: 1 });
      const skip = /^(ETSI|Draft|\d+|Version|Release|\u00a9)/i;
      const found = data.text.split('\n').map(l => l.trim()).filter(l => l.length > 20 && !skip.test(l))[0];
      return found ?? null;
    }
  } catch { /* pdf-parse unavailable */ }
  return null;
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function enrichTitles() {
  console.log('\uD83C\uDFF7\uFE0F  ETSI Title Enrichment');
  console.log('=========================\n');

  await fs.mkdir(TITLES_DIR, { recursive: true });

  const aiOk    = !NO_AI && await isAvailable();
  const model   = aiOk ? await bestModel()      : null;
  const provider = aiOk ? await activeProvider() : null;

  if (NO_AI)      console.log('\u26A0\uFE0F   --no-ai \u2014 skipping AI generation');
  else if (aiOk)  console.log(`\u2705  ${provider === 'lmstudio' ? 'LM Studio' : 'Ollama'} \u2014 model: ${model}`);
  else            console.log('\u26A0\uFE0F   No local AI \u2014 extraction only');

  if (FORCE)              console.log('\u26A1 --force: re-running everything');
  else if (FORCE_SHORT_TITLES) console.log('\uD83E\uDD16 --force-new-short-titles: AI only, extracted data preserved');
  console.log();

  let sidecarFiles;
  try {
    // Only *.workitem.html — dotfiles (.headers.*) stay on disk but are not processed
    sidecarFiles = (await fs.readdir(WORKITEM_DIR))
      .filter(f => f.endsWith('.workitem.html'));
  } catch {
    console.error(`\u274C  No _workitems directory found at:\n    ${WORKITEM_DIR}`);
    console.error('    Run npm run download first.');
    process.exit(1);
  }

  if (LIMIT) sidecarFiles = sidecarFiles.slice(0, LIMIT);
  console.log(`\uD83D\uDCCB  Found ${sidecarFiles.length} workitem sidecars${LIMIT ? ` (limited to ${LIMIT})` : ''}\n`);

  const stats = { new: 0, skipped: 0, noTitle: 0, withPdf: 0, inconsistent: 0, aiShortTitle: 0, aiRefreshed: 0 };

  for (let i = 0; i < sidecarFiles.length; i++) {
    const file     = sidecarFiles[i];
    const stem     = file.replace(/\.workitem\.html$/, '');
    const outPath  = path.join(TITLES_DIR, `${stem}.title.json`);
    const progress = `[${i + 1}/${sidecarFiles.length}]`;

    const html       = await fs.readFile(path.join(WORKITEM_DIR, file), 'utf-8');
    const numM       = html.match(/<!--\s*etsiNumber:\s*([^-][^-]*?)\s*-->/);
    const wkiM       = html.match(/<!--\s*wkiId:\s*(\d+)\s*-->/);
    const etsiNumber = numM ? numM[1].trim() : stem.replace(/_/g, ' ');
    const wkiId      = wkiM ? wkiM[1] : null;

    console.log(`${progress} ${etsiNumber}`);

    // ── --force-new-short-titles: load existing record, only re-run AI ────
    if (FORCE_SHORT_TITLES && !FORCE) {
      const existingRecord = await fs.readFile(outPath, 'utf-8')
        .then(JSON.parse).catch(() => null);

      if (!existingRecord) {
        console.log('    \u26A0\uFE0F  No existing record \u2014 running full extraction first');
        // fall through to full extraction below
      } else {
        const sourceTitle = existingRecord.fullTitleWorkitem ?? existingRecord.fullTitlePdf ?? existingRecord.etsiShortTitle;
        if (!aiOk || !sourceTitle) {
          console.log('    \u23ED\uFE0F  No AI / no source title \u2014 skipping');
          stats.skipped++;
          continue;
        }
        const newShort = await suggestShortTitle(sourceTitle, etsiNumber);
        if (newShort) {
          existingRecord.shortTitle       = newShort;
          existingRecord.shortTitleSource = 'ai';
          existingRecord.model            = model;
          existingRecord.provider         = provider;
          existingRecord.generatedAt      = new Date().toISOString();
          await fs.writeFile(outPath, JSON.stringify(existingRecord, null, 2));
          console.log(`    \uD83C\uDFF7\uFE0F  AI (refreshed): "${newShort}"`);
          stats.aiRefreshed++;
        } else {
          console.log('    \u26A0\uFE0F  AI returned nothing');
        }
        continue;
      }
    }

    // ── default: skip if complete record exists ───────────────────────────
    if (!FORCE && !FORCE_SHORT_TITLES) {
      const existing = await fs.readFile(outPath, 'utf-8').then(JSON.parse).catch(() => null);
      if (existing?.shortTitle) {
        console.log(`    \u23ED\uFE0F  Complete \u2014 skipping ("${existing.shortTitle}")`);
        stats.skipped++;
        continue;
      }
    }

    // ── Full extraction ───────────────────────────────────────────────────
    const { fullTitle, etsiShortTitle } = extractWorkitemTitles(html);

    if (!fullTitle) {
      console.log('    \u26A0\uFE0F  Could not extract title');
      stats.noTitle++;
    } else {
      console.log(`    \uD83D\uDCC4 Full:  ${fullTitle.slice(0, 90)}${fullTitle.length > 90 ? '\u2026' : ''}`);
    }
    if (etsiShortTitle) {
      console.log(`    \uD83C\uDFE0 ETSI:  ${etsiShortTitle.slice(0, 90)}${etsiShortTitle.length > 90 ? '\u2026' : ''}`);
    }

    const fullTitlePdf  = await extractPdfTitle(etsiNumber);
    if (fullTitlePdf) {
      console.log(`    \uD83D\uDCD1 PDF:   ${fullTitlePdf.slice(0, 90)}${fullTitlePdf.length > 90 ? '\u2026' : ''}`);
      stats.withPdf++;
    }

    const inconsistency = detectTitleInconsistency(fullTitle, fullTitlePdf);
    if (inconsistency) {
      console.log(`    \u26A0\uFE0F  \u2248 Title mismatch WorkItem vs PDF`);
      stats.inconsistent++;
    }

    // AI short title — always generate, ETSI short titles are often still too long
    let shortTitle       = null;
    let shortTitleSource = 'unavailable';
    const sourceTitle    = fullTitle ?? fullTitlePdf ?? etsiShortTitle;

    if (aiOk && sourceTitle) {
      shortTitle = await suggestShortTitle(sourceTitle, etsiNumber);
      if (shortTitle) {
        shortTitleSource = 'ai';
        console.log(`    \uD83C\uDFF7\uFE0F  AI:    "${shortTitle}"`);
        stats.aiShortTitle++;
      } else {
        console.log('    \u26A0\uFE0F  AI returned no valid short title');
      }
    } else if (!sourceTitle) {
      shortTitleSource = 'none';
    }

    await fs.writeFile(outPath, JSON.stringify({
      etsiNumber,
      wkiId,
      shortTitle,
      shortTitleSource,
      etsiShortTitle:    etsiShortTitle ?? null,
      fullTitleWorkitem: fullTitle      ?? null,
      fullTitlePdf:      fullTitlePdf   ?? null,
      inconsistency:     inconsistency  ?? null,
      model:             model          ?? null,
      provider:          provider       ?? null,
      generatedAt:       new Date().toISOString(),
    }, null, 2));
    stats.new++;
  }

  // ── Summary ───────────────────────────────────────────────────────────────
  console.log('\n\uD83D\uDCCA Summary:');
  console.log(`   \uD83C\uDFF7\uFE0F  New records:           ${stats.new}`);
  console.log(`   \uD83E\uDD16 AI short titles:       ${stats.aiShortTitle}`);
  if (stats.aiRefreshed) console.log(`   \uD83D\uDD04 AI short titles refreshed: ${stats.aiRefreshed}`);
  console.log(`   \u23ED\uFE0F  Skipped (cached):      ${stats.skipped}`);
  console.log(`   \uD83D\uDCD1 With PDF title:         ${stats.withPdf}`);
  console.log(`   \u26A0\uFE0F  Inconsistencies:       ${stats.inconsistent}`);
  console.log(`   \u2753  No title extracted:     ${stats.noTitle}`);
  console.log(`\n\uD83D\uDCBE  Title records \u2192 downloads/specs/_titles/`);

  if (!aiOk && !NO_AI) {
    console.log('\n\uD83D\uDCA1  Start Ollama or LM Studio, then re-run:');
    console.log('       npm run enrich-titles');
    console.log('       npm run enrich-titles -- --force-new-short-titles  (AI only)');
  }
}

enrichTitles().catch(console.error);
