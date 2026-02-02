import * as cheerio from 'cheerio';
import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';
import { ETSIClient } from './etsi-client.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const BASE_URL = 'https://portal.etsi.org';

export async function scrapeWorkProgram(client, downloadPath) {
  console.log('📋 Fetching ESI Work Program...\n');
  
  const allItems = [];
  let offset = 0;
  const pageSize = 100; // Request more per page to reduce requests
  let totalItems = 0;
  
  // Base URL for the form action
  const baseUrl = `${BASE_URL}/webapp/WorkProgram/Frame_WorkItemList.asp`;
  const baseParams = 'qSORT=HIGHVERSION&qETSI_ALL=&SearchPage=TRUE&qTB_ID=607%3BESI&qINCLUDE_SUB_TB=True&qINCLUDE_MOVED_ON=&qSTOP_FLG=&qKEYWORD_BOOLEAN=&qCLUSTER_BOOLEAN=&qFREQUENCIES_BOOLEAN=&qSTOPPING_OUTDATED=&butSimple=Search&includeNonActiveTB=FALSE&includeSubProjectCode=&qREPORT_TYPE=SUMMARY';
  
  // Fetch all pages using POST (which the form uses)
  while (true) {
    console.log(`  Fetching items ${offset + 1} to ${offset + pageSize}...`);
    
    // Use POST with form data for pagination
    const formData = new URLSearchParams();
    formData.append('qOFFSET', offset.toString());
    formData.append('qNB_TO_DISPLAY', pageSize.toString());
    formData.append('SubmitNext', ' Next Page ');
    
    const response = await client.fetch(`${baseUrl}?${baseParams}`, {
      method: 'POST',
      headers: {
        ...client.getDefaultHeaders(),
        'Content-Type': 'application/x-www-form-urlencoded'
      },
      body: formData.toString()
    });
    
    if (!response.ok) {
      throw new Error(`Failed to fetch work program: ${response.status}`);
    }
    
    const html = await response.text();
    
    // Save first page for debugging
    if (offset === 0) {
      await fs.mkdir(path.join(downloadPath, 'debug'), { recursive: true });
      await fs.writeFile(path.join(downloadPath, 'debug', 'work_program_list.html'), html);
      
      // Extract total count
      const totalMatch = html.match(/Found\s*<b>\s*(\d+)\s*<\/b>\s*Items/i);
      if (totalMatch) {
        totalItems = parseInt(totalMatch[1]);
        console.log(`  Total work items found: ${totalItems}\n`);
      }
    }
    
    // Parse items from this page
    const pageItems = parseWorkItemList(html);
    
    if (pageItems.length === 0) {
      break;
    }
    
    allItems.push(...pageItems);
    console.log(`  → Got ${pageItems.length} items (total so far: ${allItems.length})`);
    
    offset += pageItems.length;
    
    if (allItems.length >= totalItems) {
      break;
    }
    
    // Rate limiting
    await sleep(300);
  }
  
  console.log(`\n📝 Collected ${allItems.length} work items\n`);
  
  // Save results
  const outputPath = path.join(downloadPath, 'work_items.json');
  await fs.writeFile(outputPath, JSON.stringify(allItems, null, 2));
  console.log(`💾 Saved to ${outputPath}`);
  
  // Create summary report
  await createSummaryReport(allItems, downloadPath);
  
  return allItems;
}

function parseWorkItemList(html) {
  const $ = cheerio.load(html);
  const items = [];
  
  // Each work item is in a <tr> with 4 <td> cells
  $('table.Table tr').each((_, row) => {
    const $row = $(row);
    const cells = $row.find('td');
    
    // Skip header rows
    if (cells.length !== 4) return;
    if ($row.find('.RowHead').length > 0) return;
    
    const $idCell = $(cells[0]);
    const $docCell = $(cells[1]);
    const $titleCell = $(cells[2]);
    const $statusCell = $(cells[3]);
    
    // Extract work item ID from the comment <!-- 75471 -->
    const idComment = $idCell.html()?.match(/<!--\s*(\d+)\s*-->/);
    const workItemId = idComment ? idComment[1] : null;
    
    if (!workItemId) return;
    
    // Extract ETSI number (e.g., "EN 319 615")
    const etsiLink = $docCell.find('a[href*="Report_WorkItem"]').first();
    const etsiNumber = etsiLink.find('b').text().trim() || etsiLink.text().trim();
    
    // Extract reference (e.g., "REN/ESI-0019615v141")
    const refMatch = $docCell.text().match(/Ref\.\s*([A-Z]{2,4}\/ESI-\d+[a-zA-Z0-9]*)/);
    const reference = refMatch ? refMatch[1] : '';
    
    // Extract title - it's in bold, may have <br> tags
    let title = '';
    $titleCell.find('b').each((_, el) => {
      const text = $(el).text().trim().replace(/\s+/g, ' ');
      if (text) {
        title = text;
      }
    });
    // Clean up title (remove "Electronic Signatures and Trust Infrastructures (ESI);" prefix if present)
    title = title.replace(/^Electronic Signatures and Trust Infrastructures \(ESI\);?\s*/i, '');
    title = title.replace(/<br\s*\/?>/gi, ' ').trim();
    
    // Extract subtitle/scope (gray text)
    const subtitleEl = $titleCell.find('font[color="#708090"]');
    const subtitle = subtitleEl.text().trim();
    
    // Extract stage (e.g., "Drafting Stage", "Publication")
    const stageMatch = $statusCell.html()?.match(/<b>\s*([^<]+(?:Stage|Publication|Approval)[^<]*)\s*<\/b>/i);
    const stage = stageMatch ? stageMatch[1].trim() : '';
    
    // Extract current status and date
    const currentStatusMatch = $statusCell.text().match(/Current Status:\s*([^(]+)\((\d{4}-\d{2}-\d{2})\)/);
    const currentStatus = currentStatusMatch ? {
      status: currentStatusMatch[1].trim(),
      date: currentStatusMatch[2]
    } : null;
    
    // Extract next status and date
    const nextStatusMatch = $statusCell.text().match(/Next Status:\s*([^(]+)\((\d{4}-\d{2}-\d{2})\)/);
    const nextStatus = nextStatusMatch ? {
      status: nextStatusMatch[1].trim(),
      date: nextStatusMatch[2]
    } : null;
    
    // Build detail URL
    const detailHref = etsiLink.attr('href');
    const detailUrl = detailHref ? (detailHref.startsWith('http') ? detailHref : `${BASE_URL}/webapp/WorkProgram/${detailHref}`) : null;
    
    // Extract schedule URL
    const scheduleLink = $statusCell.find('a[href*="Report_Schedule"]').first();
    const scheduleHref = scheduleLink.attr('href');
    const scheduleUrl = scheduleHref ? (scheduleHref.startsWith('http') ? scheduleHref : `${BASE_URL}/webapp/WorkProgram/${scheduleHref}`) : null;
    
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
      scheduleUrl
    });
  });
  
  return items;
}

async function createSummaryReport(items, downloadPath) {
  // Group by document type (TS, TR, EN, etc.)
  const byType = {};
  const byStage = {};
  
  for (const item of items) {
    // Extract type from ETSI number
    const typeMatch = item.etsiNumber?.match(/^(TS|TR|EN|ES|EG)/i);
    const type = typeMatch ? typeMatch[1].toUpperCase() : 'Unknown';
    
    if (!byType[type]) byType[type] = [];
    byType[type].push(item);
    
    // Group by stage
    const stage = item.stage || 'Unknown';
    if (!byStage[stage]) byStage[stage] = [];
    byStage[stage].push(item);
  }
  
  const summary = {
    generatedAt: new Date().toISOString(),
    totalItems: items.length,
    byDocumentType: Object.fromEntries(
      Object.entries(byType).map(([k, v]) => [k, v.length])
    ),
    byStage: Object.fromEntries(
      Object.entries(byStage).map(([k, v]) => [k, v.length])
    ),
    items: items.map(item => ({
      etsiNumber: item.etsiNumber,
      reference: item.reference,
      title: item.title,
      subtitle: item.subtitle,
      stage: item.stage,
      currentStatus: item.currentStatus,
      nextStatus: item.nextStatus
    }))
  };
  
  await fs.writeFile(
    path.join(downloadPath, 'work_items_summary.json'),
    JSON.stringify(summary, null, 2)
  );
  
  console.log('\n📊 Summary:');
  console.log(`   Total work items: ${summary.totalItems}`);
  console.log('   By document type:', summary.byDocumentType);
  console.log('   By stage:', summary.byStage);
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// Main entry point when run directly
async function main() {
  const downloadPath = path.join(__dirname, '../downloads');
  
  // Ensure downloads directory exists
  await fs.mkdir(downloadPath, { recursive: true });
  
  console.log('EUDI Nexus - ETSI Work Program Scraper');
  console.log('======================================\n');
  
  const client = new ETSIClient();
  
  try {
    await scrapeWorkProgram(client, downloadPath);
    await analyzeWorkItems(downloadPath);
    console.log('\nDone! Output saved to downloads/');
  } catch (error) {
    console.error('Error:', error.message);
    process.exit(1);
  }
}

main();
