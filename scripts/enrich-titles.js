/**
 * enrich-titles.js
 *
 * Reads existing workitem sidecars (_workitems/*.workitem.html), extracts
 * both the full title and the ETSI-provided short title from the HTML table,
 * then generates a ≤4-word AI short title (always via local AI — the ETSI
 * short titles are often long too), detects WorkItem↔PDF title inconsistencies,
 * and writes one _titles/<safe>.title.json per document.
 *
 * HTML structure in every workitem sidecar:
 *
 *   <tr>
 *     <td class="Head1"><b>Title</b></td>
 *     <td class="Table" COLSPAN="6"><font class="Normal">
 *       Full long title here </FONT>
 *       <BR><FONT Color="#708090">ETSI short title (often still long)</FONT>
 *     </td>
 *   </tr>
 *
 * npm run enrich-titles
 * npm run enrich-titles -- --force      (re-generate even if .title.json exists)
 * npm run enrich-titles -- --limit=10
 * npm run enrich-titles -- --no-ai      (extract titles only, skip AI)
 */

import fs   from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';
import * as cheerio from 'cheerio';
import { isAvailable, bestModel, activeProvider, suggestShortTitle, detectTitleInconsistency }
  from '../src/local-ai.js';

const __dirname     = path.dirname(fileURLToPath(import.meta.url));
const SPECS_ROOT    = path.join(__dirname, '..', 'downloads', 'specs');
const WORKITEM_DIR  = path.join(SPECS_ROOT, '_workitems');
const TITLES_DIR    = path.join(SPECS_ROOT, '_titles');

// ── CLI args ──────────────────────────────────────────────────────────────────

const args     = process.argv.slice(2);
const FORCE    = args.includes('--force');
const NO_AI    = args.includes('--no-ai');
const limitArg = args.find(a => a.startsWith('--limit='));
const LIMIT    = limitArg ? parseInt(limitArg.split('=')[1]) : null;

// ── title extraction ─────────────────────────────────────────────────────────

/**
 * Extract both the full title and the ETSI-provided short title from a
 * workitem sidecar HTML.
 *
 * Returns { fullTitle, etsiShortTitle } — either may be null.
 *
 * The HTML structure is:
 *   <td class="Head1"><b>Title</b></td>
 *   <td class="Table" COLSPAN="6"><font class="Normal">
 *     Full title here </FONT> <BR><FONT Color="#708090">Short title</FONT>
 *   </td>
 *
 * cheerio + htmlparser2 handle the broken HTML4 gracefully
 * (missing closing tags, unquoted attributes, &nbsp; etc.)
 */
function extractWorkitemTitles(html) {
  const $ = cheerio.load(html);

  // Find the <td> that contains exactly the text "Title" in a <b> tag
  let titleCell = null;
  $('b').each((_, el) => {
    if ($(el).text().trim() === 'Title') {
      // Sibling td in the same tr (colspan=6 data cell)
      titleCell = $(el).closest('tr').find('td[class="Table"], td.Table').first();
      return false; // stop
    }
  });

  if (!titleCell || !titleCell.length) return { fullTitle: null, etsiShortTitle: null };

  // The cell HTML looks like:
  //   <font class="Normal">Full title </FONT> <BR><FONT Color="#708090">Short</FONT>&nbsp;
  // We split on the <BR> to separate the two parts.
  const cellHtml = titleCell.html() ?? '';

  // Split at <BR> / <br> (case-insensitive, optional slash)
  const parts = cellHtml.split(/<br\s*\/?>/i);

  // Part 0: fullTitle — load as mini-HTML and take .text()
  const fullTitle = parts[0]
    ? cheerio.load(parts[0]).text().replace(/\s+/g, ' ').trim() || null
    : null;

  // Part 1: etsiShortTitle — inside the gray <FONT Color="#708090"> tag
  let etsiShortTitle = null;
  if (parts[1]) {
    const $p = cheerio.load(parts[1]);
    // Match by color attribute (case-insensitive)
    $p('font').each((_, el) => {
      const color = ($p(el).attr('color') ?? '').toLowerCase();
      if (color === '#708090' || color === '708090') {
        const txt = $p(el).text().replace(/\s+/g, ' ').trim();
        if (txt) { etsiShortTitle = txt; return false; }
      }
    });
    // Fallback: just take all text from part 1
    if (!etsiShortTitle) {
      const txt = $p.text().replace(/[\u00a0\s]+/g, ' ').trim();
      if (txt.length > 3) etsiShortTitle = txt;
    }
  }

  return { fullTitle, etsiShortTitle };
}

// ── PDF title extraction ────────────────────────────────────────────────────

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
        if (digits.length >= 6 && inName.includes(digits.slice(0, 6))) {
          urlSidecar = { ...raw, filename: f.replace(/^\.url\./, '') };
          break;
        }
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
      const skip = /^(ETSI|Draft|\d+|Version|Release|©)/i;
      const found = data.text.split('\n')
        .map(l => l.trim())
        .filter(l => l.length > 20 && !skip.test(l))[0];
      return found ?? null;
    }
  } catch { /* pdf-parse unavailable */ }
  return null;
}

// ── main ──────────────────────────────────────────────────────────────────────

async function enrichTitles() {
  console.log('🏷️  ETSI Title Enrichment');
  console.log('=========================\n');

  await fs.mkdir(TITLES_DIR, { recursive: true });

  // Check AI availability
  const aiOk    = !NO_AI && await isAvailable();
  const model   = aiOk ? await bestModel()      : null;
  const provider = aiOk ? await activeProvider() : null;

  if (NO_AI)      console.log('⚠️   --no-ai flag — skipping AI generation');
  else if (aiOk)  console.log(`✅  ${provider === 'lmstudio' ? 'LM Studio' : 'Ollama'} available — model: ${model}`);
  else            console.log('⚠️   No local AI — titles will be extracted only');
  console.log();

  // Collect only *.workitem.html (skip dot-files like .headers.*)
  let sidecarFiles;
  try {
    sidecarFiles = (await fs.readdir(WORKITEM_DIR))
      .filter(f => !f.startsWith('.') && f.endsWith('.workitem.html'));
  } catch {
    console.error(`❌  No _workitems directory found at:\n    ${WORKITEM_DIR}`);
    console.error('    Run npm run download first.');
    process.exit(1);
  }

  if (LIMIT) sidecarFiles = sidecarFiles.slice(0, LIMIT);
  console.log(`📋  Found ${sidecarFiles.length} workitem sidecars${LIMIT ? ` (limited to ${LIMIT})` : ''}\n`);

  const stats = { new: 0, skipped: 0, noTitle: 0, withPdf: 0, inconsistent: 0, aiShortTitle: 0 };

  for (let i = 0; i < sidecarFiles.length; i++) {
    const file     = sidecarFiles[i];
    const stem     = file.replace(/\.workitem\.html$/, '');
    const outPath  = path.join(TITLES_DIR, `${stem}.title.json`);
    const progress = `[${i + 1}/${sidecarFiles.length}]`;

    const html = await fs.readFile(path.join(WORKITEM_DIR, file), 'utf-8');

    // Read etsiNumber + wkiId from HTML comment header written by download-specs.js
    const numM       = html.match(/<!--\s*etsiNumber:\s*([^-][^-]*?)\s*-->/);
    const wkiM       = html.match(/<!--\s*wkiId:\s*(\d+)\s*-->/);
    const etsiNumber = numM ? numM[1].trim() : stem.replace(/_/g, ' ');
    const wkiId      = wkiM ? wkiM[1] : null;

    console.log(`${progress} ${etsiNumber}`);

    // Skip if already enriched (unless --force)
    if (!FORCE && await fs.stat(outPath).then(() => true).catch(() => false)) {
      console.log('    ⏭️  Already enriched — skipping');
      stats.skipped++;
      continue;
    }

    // ─ Extract titles from workitem HTML ───────────────────────────────
    const { fullTitle, etsiShortTitle } = extractWorkitemTitles(html);

    if (!fullTitle) {
      console.log('    ⚠️  Could not extract title');
      stats.noTitle++;
    } else {
      console.log(`    📄 Full:  ${fullTitle.slice(0, 90)}${fullTitle.length > 90 ? '…' : ''}`);
    }
    if (etsiShortTitle) {
      console.log(`    🏠 ETSI:  ${etsiShortTitle.slice(0, 90)}${etsiShortTitle.length > 90 ? '…' : ''}`);
    }

    // ─ PDF title ─────────────────────────────────────────────────────────
    const fullTitlePdf = await extractPdfTitle(etsiNumber);
    if (fullTitlePdf) {
      console.log(`    📑 PDF:   ${fullTitlePdf.slice(0, 90)}${fullTitlePdf.length > 90 ? '…' : ''}`);
      stats.withPdf++;
    }

    // ─ Inconsistency check ──────────────────────────────────────────────
    const inconsistency = detectTitleInconsistency(fullTitle, fullTitlePdf);
    if (inconsistency) {
      console.log(`    ⚠️  ≈ Title mismatch WorkItem vs PDF`);
      stats.inconsistent++;
    }

    // ─ AI short title ───────────────────────────────────────────────────
    // Always generate via AI — ETSI short titles are often still too long
    // for diagram labels. AI condenses to a true ≤4-word label.
    let shortTitle       = null;
    let shortTitleSource = 'unavailable';
    const sourceTitle    = fullTitle ?? fullTitlePdf ?? etsiShortTitle;

    if (aiOk && sourceTitle) {
      shortTitle = await suggestShortTitle(sourceTitle, etsiNumber);
      if (shortTitle) {
        shortTitleSource = 'ai';
        console.log(`    🏷️  AI:    "${shortTitle}"`);
        stats.aiShortTitle++;
      } else {
        console.log('    ⚠️  AI returned no valid short title');
      }
    } else if (!sourceTitle) {
      shortTitleSource = 'none';
    }

    const record = {
      etsiNumber,
      wkiId,
      shortTitle,
      shortTitleSource,
      etsiShortTitle:    etsiShortTitle    ?? null,
      fullTitleWorkitem: fullTitle         ?? null,
      fullTitlePdf:      fullTitlePdf      ?? null,
      inconsistency:     inconsistency     ?? null,
      model:             model             ?? null,
      provider:          provider          ?? null,
      generatedAt:       new Date().toISOString(),
    };

    await fs.writeFile(outPath, JSON.stringify(record, null, 2));
    stats.new++;
  }

  // ─ summary ─────────────────────────────────────────────────────────────────
  console.log('\n📊 Summary:');
  console.log(`   🏷️  New records:           ${stats.new}`);
  console.log(`   ⏭️  Skipped (cached):      ${stats.skipped}`);
  console.log(`   🤖 AI short titles:       ${stats.aiShortTitle}`);
  console.log(`   📑 With PDF title:         ${stats.withPdf}`);
  console.log(`   ⚠️  Inconsistencies:       ${stats.inconsistent}`);
  console.log(`   ❓  No title extracted:     ${stats.noTitle}`);
  console.log(`\n💾  Title records → downloads/specs/_titles/`);

  if (!aiOk && !NO_AI) {
    console.log('\n💡  Start Ollama or LM Studio to generate AI short titles:');
    console.log('       ollama serve  —  or  —  open LM Studio');
    console.log('       npm run enrich-titles');
  }
}

enrichTitles().catch(console.error);
