/**
 * enrich-titles.js
 *
 * Reads existing workitem sidecars (_workitems/*.workitem.html) and optionally
 * downloaded PDFs, generates a ≤4-word short title via local AI (Ollama),
 * detects WorkItem↔PDF title inconsistencies, and writes one
 * _titles/<safe>.title.json per document.
 *
 * npm run enrich-titles
 * npm run enrich-titles -- --force      (re-generate even if .title.json exists)
 * npm run enrich-titles -- --limit=10
 * npm run enrich-titles -- --no-ai      (extract + compare titles, skip AI)
 */

import fs   from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';
import * as cheerio from 'cheerio';
import { isAvailable, bestModel, suggestShortTitle, detectTitleInconsistency }
  from '../src/local-ai.js';

const __dirname     = path.dirname(fileURLToPath(import.meta.url));
const SPECS_ROOT    = path.join(__dirname, '..', 'downloads', 'specs');
const WORKITEM_DIR  = path.join(SPECS_ROOT, '_workitems');
const TITLES_DIR    = path.join(SPECS_ROOT, '_titles');

// ── CLI args ──────────────────────────────────────────────────────────────────

const args      = process.argv.slice(2);
const FORCE     = args.includes('--force');
const NO_AI     = args.includes('--no-ai');
const limitArg  = args.find(a => a.startsWith('--limit='));
const LIMIT     = limitArg ? parseInt(limitArg.split('=')[1]) : null;

// ── helpers ───────────────────────────────────────────────────────────────────

/** Extract the full title from a workitem sidecar HTML. */
function extractWorkitemTitle(html) {
  const $ = cheerio.load(html);

  // Pattern 1: dedicated "Title" row in the work-item table
  let title = null;
  $('tr').each((_, row) => {
    const cells = $(row).find('td');
    if (cells.length >= 2) {
      const label = $(cells[0]).text().trim().toLowerCase();
      if (label === 'title' || label === 'work item title') {
        const candidate = $(cells[1]).text().trim();
        if (candidate.length > 10) { title = candidate; return false; }
      }
    }
  });
  if (title) return title;

  // Pattern 2: first <b> or <strong> longer than 30 chars
  $('b, strong').each((_, el) => {
    const txt = $(el).text().trim();
    if (txt.length > 30 && !title) title = txt;
  });
  if (title) return title;

  // Pattern 3: page <title> tag minus the ETSI boilerplate
  const pageTitle = $('title').text().replace(/ETSI[^-]*-?/i, '').trim();
  if (pageTitle.length > 10) return pageTitle;

  return null;
}

/** Extract title from first page of a downloaded PDF via its .url sidecar
 *  so we don't have to parse binary — use mammoth for docx instead.
 *  For now we look for a matching .url sidecar and note the filename as
 *  a hint; actual PDF text extraction can be added once pdf-parse is wired. */
async function extractPdfTitle(etsiNumber) {
  // Scan spec subdirectories for a matching .url sidecar
  let urlSidecar = null;
  try {
    const subdirs = await fs.readdir(SPECS_ROOT, { withFileTypes: true });
    for (const d of subdirs) {
      if (!d.isDirectory() || d.name.startsWith('_')) continue;
      const dir   = path.join(SPECS_ROOT, d.name);
      const files = await fs.readdir(dir);
      for (const f of files) {
        if (!f.startsWith('.url.')) continue;
        const raw = JSON.parse(await fs.readFile(path.join(dir, f), 'utf-8'));
        // rough match by etsiNumber digits
        const digits  = etsiNumber.replace(/[^0-9]/g, '');
        const inName  = f.replace(/[^0-9]/g, '');
        if (digits.length >= 6 && inName.includes(digits.slice(0, 6))) {
          urlSidecar = { ...raw, filename: f.replace(/^\.url\./, '') };
          break;
        }
      }
      if (urlSidecar) break;
    }
  } catch { /* downloads not yet available */ }

  if (!urlSidecar) return null;

  // Try pdf-parse if available and file is a PDF
  const ext = path.extname(urlSidecar.filename).toLowerCase();
  if (ext !== '.pdf') return null;   // docx/zip handled separately later

  try {
    const pdfParse = (await import('pdf-parse')).default;
    const subdirs  = await fs.readdir(SPECS_ROOT, { withFileTypes: true });
    for (const d of subdirs) {
      if (!d.isDirectory() || d.name.startsWith('_')) continue;
      const candidate = path.join(SPECS_ROOT, d.name, urlSidecar.filename);
      if (!await fs.stat(candidate).then(() => true).catch(() => false)) continue;
      const buf  = await fs.readFile(candidate);
      const data = await pdfParse(buf, { max: 1 });   // first page only
      // First non-trivial line is usually the document title
      const lines = data.text.split('\n').map(l => l.trim()).filter(l => l.length > 20);
      // Skip lines that are clearly headers ("ETSI EN …", page numbers, etc.)
      const skip  = /^(ETSI|Draft|\d+|Version|Release|©)/i;
      const found = lines.find(l => !skip.test(l));
      return found ?? null;
    }
  } catch { /* pdf-parse failed or not installed */ }

  return null;
}

/** Derive a safe filename stem from an ETSI number. */
const safeStem = n => n.replace(/[^a-zA-Z0-9-_]/g, '_');

// ── main ──────────────────────────────────────────────────────────────────────

async function enrichTitles() {
  console.log('🏷️  ETSI Title Enrichment');
  console.log('=========================\n');

  await fs.mkdir(TITLES_DIR, { recursive: true });

  // Check AI availability
  const aiOk = !NO_AI && await isAvailable();
  const model = aiOk ? await bestModel() : null;

  if (NO_AI)       console.log('⚠️   --no-ai flag set — skipping AI generation');
  else if (aiOk)   console.log(`✅  Ollama available — model: ${model}`);
  else             console.log('⚠️   Ollama not available — titles will be extracted only (no short title)');
  console.log();

  // Collect all workitem sidecars
  let sidecarFiles;
  try {
    sidecarFiles = (await fs.readdir(WORKITEM_DIR)).filter(f => f.endsWith('.workitem.html'));
  } catch {
    console.error(`❌  No _workitems directory found at:\n    ${WORKITEM_DIR}`);
    console.error('    Run npm run download first.');
    process.exit(1);
  }

  if (LIMIT) sidecarFiles = sidecarFiles.slice(0, LIMIT);
  console.log(`📋  Found ${sidecarFiles.length} workitem sidecars${LIMIT ? ` (limited to ${LIMIT})` : ''}\n`);

  const stats = { new: 0, skipped: 0, noTitle: 0, withPdf: 0, inconsistent: 0 };

  for (let i = 0; i < sidecarFiles.length; i++) {
    const file      = sidecarFiles[i];
    const stem      = file.replace(/\.workitem\.html$/, '');
    // stem is already the safe name; recover etsiNumber from it (underscore → space/dash is lossy,
    // so we also read the HTML comment header we wrote in download-specs.js)
    const outPath   = path.join(TITLES_DIR, `${stem}.title.json`);
    const progress  = `[${i + 1}/${sidecarFiles.length}]`;

    // Read etsiNumber from HTML comment
    const html = await fs.readFile(path.join(WORKITEM_DIR, file), 'utf-8');
    const numM  = html.match(/<!--\s*etsiNumber:\s*([^\-\-]+?)\s*-->/);
    const wkiM  = html.match(/<!--\s*wkiId:\s*(\d+)\s*-->/);
    const etsiNumber = numM ? numM[1].trim() : stem.replace(/_/g, ' ');
    const wkiId      = wkiM ? wkiM[1] : null;

    console.log(`${progress} ${etsiNumber}`);

    // Skip if already enriched (unless --force)
    if (!FORCE && await fs.stat(outPath).then(() => true).catch(() => false)) {
      console.log('    ⏭️   Already enriched — skipping');
      stats.skipped++;
      continue;
    }

    // Extract full title from workitem HTML
    const fullTitleWorkitem = extractWorkitemTitle(html);
    if (!fullTitleWorkitem) {
      console.log('    ⚠️   Could not extract title from workitem sidecar');
      stats.noTitle++;
    } else {
      console.log(`    📄  WorkItem title: ${fullTitleWorkitem.slice(0, 80)}${fullTitleWorkitem.length > 80 ? '…' : ''}`);
    }

    // Try to get PDF title
    const fullTitlePdf = await extractPdfTitle(etsiNumber);
    if (fullTitlePdf) {
      console.log(`    📑  PDF title: ${fullTitlePdf.slice(0, 80)}${fullTitlePdf.length > 80 ? '…' : ''}`);
      stats.withPdf++;
    }

    // Detect inconsistency
    const inconsistency = detectTitleInconsistency(fullTitleWorkitem, fullTitlePdf);
    if (inconsistency) {
      console.log(`    ⚠️   Inconsistency: ${inconsistency.slice(0, 100)}…`);
      stats.inconsistent++;
    }

    // Generate short title via AI
    let shortTitle = null;
    let shortTitleSource = 'unavailable';
    const sourceTitle = fullTitleWorkitem ?? fullTitlePdf;

    if (aiOk && sourceTitle) {
      shortTitle = await suggestShortTitle(sourceTitle, etsiNumber);
      if (shortTitle) {
        shortTitleSource = fullTitlePdf ? 'both' : 'workitem';
        console.log(`    🏷️   Short title: "${shortTitle}"`);
      } else {
        console.log('    ⚠️   AI returned no valid short title');
        shortTitleSource = 'unavailable';
      }
    } else if (!aiOk && sourceTitle) {
      shortTitleSource = 'unavailable';
    } else if (!sourceTitle) {
      shortTitleSource = 'none';
    }

    const record = {
      etsiNumber,
      wkiId,
      shortTitle,
      shortTitleSource,
      fullTitleWorkitem: fullTitleWorkitem ?? null,
      fullTitlePdf:      fullTitlePdf      ?? null,
      inconsistency:     inconsistency     ?? null,
      model:             aiOk ? model : null,
      generatedAt:       new Date().toISOString(),
    };

    await fs.writeFile(outPath, JSON.stringify(record, null, 2));
    stats.new++;
  }

  // ── summary ────────────────────────────────────────────────────────────────
  console.log('\n📊 Summary:');
  console.log(`   🏷️   New title records:     ${stats.new}`);
  console.log(`   ⏭️   Skipped (cached):      ${stats.skipped}`);
  console.log(`   📑  With PDF title:         ${stats.withPdf}`);
  console.log(`   ⚠️   Inconsistencies:       ${stats.inconsistent}`);
  console.log(`   ❓  No title extracted:     ${stats.noTitle}`);
  console.log(`\n💾  Title records saved to downloads/specs/_titles/`);

  if (!aiOk && !NO_AI) {
    console.log('\n💡  Tip: start Ollama and re-run to generate short titles:');
    console.log('       ollama serve');
    console.log('       ollama pull llama3.2');
    console.log('       npm run enrich-titles');
  }
  if (FORCE && stats.skipped === 0 && !LIMIT) {
    console.log('\n💡  Run without --force to skip already-enriched items.');
  }
}

enrichTitles().catch(console.error);
