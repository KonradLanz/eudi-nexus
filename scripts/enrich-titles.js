/**
 * enrich-titles.js
 *
 * Reads workitem sidecars (_workitems/*.workitem.html), extracts fullTitle
 * and etsiShortTitle, then generates a ≤4-word AI short title.
 *
 * After writing each _titles sidecar the script optionally patches the
 * matching corpus/specs JSON with shortTitleAI and title fields.
 * It never overwrites canonical shortname unless shortname is empty or
 * was derived from the SHORTNAMES map (shortnameSource = 'shortnames-map').
 *
 * Field model:
 *   shortname         canonical stable ID  (kuratiert / map / sidecar-promoted)
 *   shortnameSource   'manual'|'shortnames-map'|'titles-sidecar'|'ai'
 *   shortTitleAI      AI suggestion ≤4 words (suggestion only, NOT used as ID)
 *   etsiShortTitle    raw ETSI portal label
 *   fullTitleWorkitem raw HTML workitem title
 *   fullTitlePdf      raw PDF title (if extracted)
 *
 * Flags:
 *   --force                  re-run everything (re-extract + re-generate AI)
 *   --force-new-short-titles re-run AI only; keep extracted titles from disk
 *   --no-ai                  extract titles only, skip AI
 *   --no-corpus-write        skip patching corpus/specs JSON files
 *   --limit=N                process first N sidecars
 *   --id=<etsiNumber>        process only the item matching this ETSI number
 *                            (e.g. --id="DMI ESI-0019204", --id="EN 319 403")
 *                            combine with --force to re-enrich a single item
 */

import fs   from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';
import * as cheerio from 'cheerio';
import { isAvailable, bestModel, activeProvider, suggestShortTitle, detectTitleInconsistency }
  from '../src/local-ai.js';

const __dirname    = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.join(__dirname, '..');
const SPECS_ROOT   = path.join(PROJECT_ROOT, 'downloads', 'specs');
const WORKITEM_DIR = path.join(SPECS_ROOT, '_workitems');
const TITLES_DIR   = path.join(SPECS_ROOT, '_titles');
const CORPUS_DIR   = path.join(PROJECT_ROOT, 'corpus', 'specs');
const RESULTS_JSON = path.join(SPECS_ROOT, '_download_results.json');

// ── CLI flags ───────────────────────────────────────────────────────────────────

const args               = process.argv.slice(2);
const FORCE              = args.includes('--force');
const FORCE_SHORT_TITLES = args.includes('--force-new-short-titles');
const NO_AI              = args.includes('--no-ai');
const NO_CORPUS_WRITE    = args.includes('--no-corpus-write');
const limitArg           = args.find(a => a.startsWith('--limit='));
const LIMIT              = limitArg ? parseInt(limitArg.split('=')[1]) : null;
const idArg              = args.find(a => a.startsWith('--id='));
const ID_FILTER          = idArg ? idArg.slice('--id='.length).trim() : null;

// ── STOPPED items ────────────────────────────────────────────────────────────────

/**
 * Returns a Set of etsiNumber strings that are listed under "stopped" in
 * _download_results.json.  These work items are abandoned — no AI title needed.
 */
async function loadStoppedEtsiNumbers() {
  try {
    const raw     = await fs.readFile(RESULTS_JSON, 'utf-8');
    const results = JSON.parse(raw);
    const stopped = results.stopped ?? [];
    return new Set(stopped.map(s => (s.etsiNumber ?? '').trim()).filter(Boolean));
  } catch {
    return new Set(); // file absent → treat as empty
  }
}

// ── Corpus patching ───────────────────────────────────────────────────────────────

/**
 * Find the corpus/specs JSON whose "norm" field matches etsiNumber.
 * Normalises both sides (strip spaces/hyphens, uppercase) for fuzzy match.
 */
async function findCorpusJson(etsiNumber) {
  try {
    await fs.access(CORPUS_DIR);
  } catch {
    return null; // corpus dir does not exist yet
  }

  const needle = etsiNumber.replace(/[\s-]/g, '').toUpperCase();
  let files;
  try { files = await fs.readdir(CORPUS_DIR); } catch { return null; }

  for (const f of files) {
    if (!f.endsWith('.json')) continue;
    const fullPath = path.join(CORPUS_DIR, f);
    try {
      const rec = JSON.parse(await fs.readFile(fullPath, 'utf-8'));
      const hay  = (rec.norm ?? '').replace(/[\s-]/g, '').toUpperCase();
      if (hay === needle) return { path: fullPath, record: rec };
    } catch { /* skip unreadable */ }
  }
  return null;
}

/**
 * Patch a corpus JSON with title fields derived from the _titles sidecar.
 *
 * Rules:
 *  - shortTitleAI is always overwritten with the latest AI suggestion.
 *  - shortname is only overwritten if currently empty or from 'shortnames-map'.
 *  - shortnameSource is set accordingly.
 *  - title fields (fullTitleWorkitem, etsiShortTitle, fullTitlePdf) are written
 *    if not already present.
 */
async function patchCorpusJson(etsiNumber, titleRecord) {
  const found = await findCorpusJson(etsiNumber);
  if (!found) return false;

  const { path: corpusPath, record } = found;

  // Always update the AI suggestion field
  if (titleRecord.shortTitle) {
    record.shortTitleAI = titleRecord.shortTitle;
  }

  // Patch shortname only if not already canonical
  const currentSource = record.shortnameSource ?? (record.shortname ? 'shortnames-map' : '');
  const mayPromote = !record.shortname || currentSource === 'shortnames-map';

  if (mayPromote && titleRecord.shortTitle) {
    // Use etsiShortTitle as shortname candidate (more stable than AI)
    // AI short title goes to shortTitleAI only — never auto-promoted to shortname
    // (promotion is a manual/downstream step)
  }

  // Write source title fields if missing
  if (!record.fullTitleWorkitem && titleRecord.fullTitleWorkitem) {
    record.fullTitleWorkitem = titleRecord.fullTitleWorkitem;
  }
  if (!record.etsiShortTitle && titleRecord.etsiShortTitle) {
    record.etsiShortTitle = titleRecord.etsiShortTitle;
  }
  if (!record.fullTitlePdf && titleRecord.fullTitlePdf) {
    record.fullTitlePdf = titleRecord.fullTitlePdf;
  }

  // Mark corpus provenance
  if (!record.shortnameSource) {
    record.shortnameSource = record.shortname ? 'shortnames-map' : '';
  }
  record.titleEnrichedAt = new Date().toISOString();

  await fs.writeFile(corpusPath, JSON.stringify(record, null, 2) + '\n', 'utf-8');
  return true;
}

// ── Title extraction ────────────────────────────────────────────────────────────────

// Dummy-title guard: values that are not real titles
const DUMMY_TITLE_RE = /^(title\s*\d+\s*)+$/i;

function extractWorkitemTitles(html) {
  const $ = cheerio.load(html);

  // Strategy 1: look for <b>Title</b> label in a table row, grab sibling td
  let titleCell = null;
  $('b, strong').each((_, el) => {
    const txt = $(el).text().trim();
    if (/^title$/i.test(txt)) {
      // Try td.Table first, then any td sibling
      const row  = $(el).closest('tr');
      const cell = row.find('td[class="Table"], td.Table').first();
      if (cell.length) { titleCell = cell; return false; }
      // fallback: next td in the same row
      const tds = row.find('td');
      tds.each((i, td) => {
        if ($(td).find('b, strong').filter((_, b) => /^title$/i.test($(b).text().trim())).length) {
          const next = tds.eq(i + 1);
          if (next.length) { titleCell = next; return false; }
        }
      });
      return false;
    }
  });

  // Strategy 2: scan all th/td pairs for a "Title" label
  if (!titleCell) {
    $('tr').each((_, row) => {
      const cells = $(row).find('th, td');
      cells.each((i, cell) => {
        if (/^title$/i.test($(cell).text().trim())) {
          const next = cells.eq(i + 1);
          if (next.length) { titleCell = next; return false; }
        }
      });
      if (titleCell) return false;
    });
  }

  if (!titleCell || !titleCell.length) return { fullTitle: null, etsiShortTitle: null };

  const cellHtml = titleCell.html() ?? '';
  const parts    = cellHtml.split(/<br\s*\/?>/i);

  const rawFullTitle = parts[0]
    ? cheerio.load(parts[0]).text().replace(/\s+/g, ' ').trim() || null
    : null;

  // Reject dummy titles like "Title 1 Title 2"
  const fullTitle = rawFullTitle && !DUMMY_TITLE_RE.test(rawFullTitle) ? rawFullTitle : null;

  let etsiShortTitle = null;
  if (parts[1]) {
    const $p = cheerio.load(parts[1]);
    // Prefer grey-coloured font (ETSI portal convention)
    $p('font').each((_, el) => {
      const color = ($p(el).attr('color') ?? '').toLowerCase().replace(/^#/, '');
      if (color === '708090' || color === 'slategray' || color === 'slategrey') {
        const txt = $p(el).text().replace(/\s+/g, ' ').trim();
        if (txt && !DUMMY_TITLE_RE.test(txt)) { etsiShortTitle = txt; return false; }
      }
    });
    // Fallback: plain text of second part (only if meaningfully long)
    if (!etsiShortTitle) {
      const txt = $p.text().replace(/[\u00a0\s]+/g, ' ').trim();
      if (txt.length > 8 && !DUMMY_TITLE_RE.test(txt)) etsiShortTitle = txt;
    }
  }

  return { fullTitle, etsiShortTitle };
}

// ── PDF title extraction ────────────────────────────────────────────────────────────

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

// ── ID normaliser (for --id matching) ─────────────────────────────────────────────

/**
 * Normalise an ETSI number for loose matching:
 * strip spaces, hyphens, underscores; lowercase.
 * "DMI ESI-0019204" → "dmisi0019204"
 * "EN_319_403"      → "en319403"
 */
function normaliseId(s) {
  return s.replace(/[\s\-_]/g, '').toLowerCase();
}

// ── Main ──────────────────────────────────────────────────────────────────────────

async function enrichTitles() {
  console.log('\uD83C\uDFF7\uFE0F  ETSI Title Enrichment');
  console.log('=========================\n');

  await fs.mkdir(TITLES_DIR, { recursive: true });

  const aiOk     = !NO_AI && await isAvailable();
  const model    = aiOk ? await bestModel()       : null;
  const provider = aiOk ? await activeProvider()  : null;

  if (NO_AI)               console.log('\u26A0\uFE0F   --no-ai \u2014 skipping AI generation');
  else if (aiOk)           console.log(`\u2705  ${provider === 'lmstudio' ? 'LM Studio' : 'Ollama'} \u2014 model: ${model}`);
  else                     console.log('\u26A0\uFE0F   No local AI \u2014 extraction only');

  if (FORCE)               console.log('\u26A1 --force: re-running everything');
  else if (FORCE_SHORT_TITLES) console.log('\uD83E\uDD16 --force-new-short-titles: AI only, extracted data preserved');
  if (NO_CORPUS_WRITE)     console.log('\uD83D\uDEAB --no-corpus-write: skipping corpus JSON patches');
  if (ID_FILTER)           console.log(`\uD83D\uDD0D --id filter: "${ID_FILTER}" (processing this item only)`);
  console.log();

  // Load STOPPED etsiNumbers — these are abandoned work items, no AI title needed
  const stoppedNumbers = await loadStoppedEtsiNumbers();
  if (stoppedNumbers.size > 0) {
    console.log(`\u23F9\uFE0F  Loaded ${stoppedNumbers.size} STOPPED work items \u2014 will skip`);
    console.log();
  }

  let sidecarFiles;
  try {
    sidecarFiles = (await fs.readdir(WORKITEM_DIR))
      .filter(f => !f.startsWith('.') && f.endsWith('.workitem.html'));
  } catch {
    console.error(`\u274C  No _workitems directory found at:\n    ${WORKITEM_DIR}`);
    console.error('    Run npm run download first.');
    process.exit(1);
  }

  if (!sidecarFiles.length) {
    console.error('\u274C  No *.workitem.html sidecars found in _workitems/');
    console.error('    Run npm run download first to generate them.');
    process.exit(1);
  }

  // Apply --id filter: keep only the sidecar whose etsiNumber matches
  if (ID_FILTER) {
    const needle = normaliseId(ID_FILTER);
    // Read each sidecar header comment to extract etsiNumber, then filter
    const filtered = [];
    for (const f of sidecarFiles) {
      const html  = await fs.readFile(path.join(WORKITEM_DIR, f), 'utf-8');
      const numM  = html.match(/<!--\s*etsiNumber:\s*([^-][^-]*?)\s*-->/);
      const etsiN = numM ? numM[1].trim() : f.replace(/\.workitem\.html$/, '').replace(/_/g, ' ');
      if (normaliseId(etsiN) === needle) { filtered.push(f); break; }
    }
    if (!filtered.length) {
      console.error(`\u274C  No workitem sidecar found for --id="${ID_FILTER}"`);
      console.error('    Check the exact ETSI number with: ls downloads/specs/_workitems/');
      process.exit(1);
    }
    sidecarFiles = filtered;
  }

  if (LIMIT && !ID_FILTER) sidecarFiles = sidecarFiles.slice(0, LIMIT);
  console.log(`\uD83D\uDCCB  Found ${sidecarFiles.length} workitem sidecar${sidecarFiles.length !== 1 ? 's' : ''}${LIMIT && !ID_FILTER ? ` (limited to ${LIMIT})` : ID_FILTER ? ' (--id filter)' : ''}\n`);

  const stats = {
    new: 0, skipped: 0, skippedStopped: 0, noTitle: 0, withPdf: 0,
    inconsistent: 0, aiShortTitle: 0, aiRefreshed: 0,
    corpusPatched: 0,
  };

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

    // ── Skip STOPPED work items ───────────────────────────────────────────────────────────
    if (stoppedNumbers.has(etsiNumber)) {
      console.log(`${progress} ${etsiNumber}  \u23F9\uFE0F  STOPPED \u2014 skipping`);
      stats.skippedStopped++;
      continue;
    }

    console.log(`${progress} ${etsiNumber}`);

    // ── --force-new-short-titles: AI only, keep existing extraction ───────────────
    if (FORCE_SHORT_TITLES && !FORCE) {
      const existingRecord = await fs.readFile(outPath, 'utf-8')
        .then(JSON.parse).catch(() => null);

      if (!existingRecord) {
        console.log('    \u26A0\uFE0F  No existing record \u2014 running full extraction first');
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
          await fs.writeFile(outPath, JSON.stringify(existingRecord, null, 2) + '\n', 'utf-8');
          console.log(`    \uD83C\uDFF7\uFE0F  AI (refreshed): "${newShort}"`);
          stats.aiRefreshed++;
          if (!NO_CORPUS_WRITE) {
            const patched = await patchCorpusJson(etsiNumber, existingRecord);
            if (patched) { console.log('    \uD83D\uDCDD  Corpus patched'); stats.corpusPatched++; }
          }
        } else {
          console.log('    \u26A0\uFE0F  AI returned nothing');
        }
        continue;
      }
    }

    // ── Skip if already complete (no --force) ───────────────────────────────────
    if (!FORCE && !FORCE_SHORT_TITLES) {
      const existing = await fs.readFile(outPath, 'utf-8').then(JSON.parse).catch(() => null);
      if (existing?.shortTitle) {
        console.log(`    \u23ED\uFE0F  Complete \u2014 skipping ("${existing.shortTitle}")`);
        stats.skipped++;
        continue;
      }
    }

    // ── Full extraction ───────────────────────────────────────────────────────────────
    const { fullTitle, etsiShortTitle } = extractWorkitemTitles(html);

    if (!fullTitle) { console.log('    \u26A0\uFE0F  Could not extract title'); stats.noTitle++; }
    else console.log(`    \uD83D\uDCC4 Full:  ${fullTitle.slice(0, 90)}${fullTitle.length > 90 ? '\u2026' : ''}`);
    if (etsiShortTitle) console.log(`    \uD83C\uDFE0 ETSI:  ${etsiShortTitle.slice(0, 90)}${etsiShortTitle.length > 90 ? '\u2026' : ''}`);

    const fullTitlePdf  = await extractPdfTitle(etsiNumber);
    if (fullTitlePdf) { console.log(`    \uD83D\uDCD1 PDF:   ${fullTitlePdf.slice(0, 90)}${fullTitlePdf.length > 90 ? '\u2026' : ''}`); stats.withPdf++; }

    const inconsistency = detectTitleInconsistency(fullTitle, fullTitlePdf);
    if (inconsistency) { console.log(`    \u26A0\uFE0F  \u2248 Title mismatch WorkItem vs PDF`); stats.inconsistent++; }

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

    const titleRecord = {
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
    };

    await fs.writeFile(outPath, JSON.stringify(titleRecord, null, 2) + '\n', 'utf-8');
    stats.new++;

    // ── Patch matching corpus/specs JSON ───────────────────────────────────────────
    if (!NO_CORPUS_WRITE) {
      const patched = await patchCorpusJson(etsiNumber, titleRecord);
      if (patched) { console.log('    \uD83D\uDCDD  Corpus patched'); stats.corpusPatched++; }
    }
  }

  console.log('\n\uD83D\uDCCA Summary:');
  console.log(`   \uD83C\uDFF7\uFE0F  New records:              ${stats.new}`);
  console.log(`   \uD83E\uDD16 AI short titles:          ${stats.aiShortTitle}`);
  if (stats.aiRefreshed) console.log(`   \uD83D\uDD04 AI short titles refreshed: ${stats.aiRefreshed}`);
  console.log(`   \u23ED\uFE0F  Skipped (cached):         ${stats.skipped}`);
  console.log(`   \u23F9\uFE0F  Skipped (STOPPED):        ${stats.skippedStopped}`);
  console.log(`   \uD83D\uDCD1 With PDF title:            ${stats.withPdf}`);
  console.log(`   \u26A0\uFE0F  Inconsistencies:          ${stats.inconsistent}`);
  console.log(`   \u2753  No title extracted:        ${stats.noTitle}`);
  if (!NO_CORPUS_WRITE) console.log(`   \uD83D\uDCDD  Corpus JSONs patched:      ${stats.corpusPatched}`);
  console.log(`\n\uD83D\uDCBE  Title records \u2192 downloads/specs/_titles/`);

  if (!aiOk && !NO_AI) {
    console.log('\n\uD83D\uDCA1  Start Ollama or LM Studio, then re-run:');
    console.log('       npm run enrich-titles');
    console.log('       npm run enrich-titles -- --force-new-short-titles  (AI only)');
  }
}

enrichTitles().catch(console.error);
