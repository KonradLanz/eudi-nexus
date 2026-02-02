import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';
import * as cheerio from 'cheerio';
import { ETSIClient } from '../src/etsi-client.js';
import dotenv from 'dotenv';

// Load .env from project root
const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(__dirname, '..', '.env') });

const DOWNLOAD_PATH = '../downloads/specs';
const BASE_URL = 'https://portal.etsi.org';

async function downloadLatestSpecs() {
  console.log('📥 ETSI Specification Downloader');
  console.log('================================\n');

  // Check for command line args
  const args = process.argv.slice(2);
  const limitArg = args.find(a => a.startsWith('--limit='));
  const limit = limitArg ? parseInt(limitArg.split('=')[1]) : null;
  const publishedOnly = args.includes('--published-only');

  // Load work items
  const workItems = JSON.parse(await fs.readFile('../downloads/esi_overview.json', 'utf-8'));
  
  // Get unique active items (we're most interested in latest versions)
  const activeItems = workItems.activeWorkItems;
  const publishedItems = workItems.publishedDocuments;
  
  console.log(`📋 Found ${activeItems.length} active + ${publishedItems.length} published items\n`);

  // Create client and login
  const client = new ETSIClient();
  
  console.log('🔐 Logging in...');
  const loggedIn = await client.login(
    process.env.ETSI_USERNAME,
    process.env.ETSI_PASSWORD
  );

  if (!loggedIn) {
    console.error('❌ Login failed');
    process.exit(1);
  }
  console.log('✅ Login successful!\n');

  // Create download directory
  await fs.mkdir(DOWNLOAD_PATH, { recursive: true });

  // Track downloads
  const results = {
    success: [],
    failed: [],
    noDownload: []
  };

  // Process items - published items first (they have downloads), then active
  const allItems = publishedOnly ? publishedItems : [...publishedItems, ...activeItems];
  
  // Deduplicate by ETSI number (keep first occurrence which is the latest)
  const seen = new Set();
  const uniqueItems = allItems.filter(item => {
    const key = item.etsiNumber;
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  const itemsToProcess = limit ? uniqueItems.slice(0, limit) : uniqueItems;
  console.log(`📦 Processing ${itemsToProcess.length} specifications${limit ? ` (limited to ${limit})` : ''}...\n`);

  for (let i = 0; i < itemsToProcess.length; i++) {
    const item = itemsToProcess[i];
    const progress = `[${i + 1}/${uniqueItems.length}]`;
    
    console.log(`${progress} ${item.etsiNumber}`);
    
    try {
      const downloadInfo = await fetchDownloadLink(client, item);
      
      if (downloadInfo && downloadInfo.url) {
        const filename = await downloadFile(client, downloadInfo, item);
        if (filename) {
          results.success.push({ etsiNumber: item.etsiNumber, filename, url: downloadInfo.url });
          console.log(`    ✅ Downloaded: ${filename}`);
        } else {
          results.failed.push({ etsiNumber: item.etsiNumber, reason: 'Download failed' });
          console.log(`    ❌ Download failed`);
        }
      } else {
        results.noDownload.push({ etsiNumber: item.etsiNumber, reason: 'No download link found' });
        console.log(`    ⚠️ No download available`);
      }
      
      // Rate limiting
      await sleep(500);
      
    } catch (error) {
      results.failed.push({ etsiNumber: item.etsiNumber, reason: error.message });
      console.log(`    ❌ Error: ${error.message}`);
    }
  }

  // Save results
  await fs.writeFile(
    path.join(DOWNLOAD_PATH, '_download_results.json'),
    JSON.stringify(results, null, 2)
  );

  console.log('\n📊 Download Summary:');
  console.log(`   ✅ Success: ${results.success.length}`);
  console.log(`   ❌ Failed: ${results.failed.length}`);
  console.log(`   ⚠️ No download available: ${results.noDownload.length}`);
  console.log(`\n💾 Results saved to ${DOWNLOAD_PATH}/_download_results.json`);
}

async function fetchDownloadLink(client, item) {
  if (!item.detailUrl) {
    return null;
  }

  const response = await client.fetch(item.detailUrl, {
    headers: client.getDefaultHeaders()
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }

  const html = await response.text();
  const $ = cheerio.load(html);

  // Look for ETSI delivery system PDF links (primary source)
  // Format: https://www.etsi.org/deliver/etsi_XX/XXXXXX_XXXXXX/XXXXXXXX/XX.XX.XX_XX/XX_XXXXXXXX.pdf
  let downloadUrl = null;
  let downloadType = null;

  // Look for the main PDF download link from ETSI delivery system
  $('a[href*="www.etsi.org/deliver"]').each((_, el) => {
    const href = $(el).attr('href');
    if (href && href.includes('.pdf')) {
      downloadUrl = href;
      downloadType = 'pdf';
    }
  });

  // Also check for pda.etsi.org links (alternative download source)
  if (!downloadUrl) {
    $('a[href*="pda.etsi.org"]').each((_, el) => {
      const href = $(el).attr('href');
      if (href) {
        downloadUrl = href;
        downloadType = 'pda';
      }
    });
  }

  // Check for any other PDF/ZIP links
  if (!downloadUrl) {
    $('a').each((_, el) => {
      const href = $(el).attr('href') || '';
      if (href.includes('.pdf') && href.includes('etsi')) {
        downloadUrl = href;
        downloadType = 'pdf';
      } else if (href.includes('.zip') && !downloadUrl) {
        downloadUrl = href;
        downloadType = 'zip';
      }
    });
  }

  // Check for docbox.etsi.org draft links (Word docs or PDFs)
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
    // For ETSI delivery URLs, we can download directly without auth
    const fetchFn = downloadInfo.url.includes('www.etsi.org/deliver') ? fetch : client.fetch.bind(client);
    
    const response = await fetchFn(downloadInfo.url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Accept': 'application/pdf,application/zip,application/octet-stream,*/*'
      }
    });

    if (!response.ok) {
      return null;
    }

    // Get filename from Content-Disposition header or URL
    let filename = null;
    const contentDisposition = response.headers.get('content-disposition');
    
    if (contentDisposition) {
      const match = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
      if (match) {
        filename = match[1].replace(/['"]/g, '');
      }
    }
    
    if (!filename) {
      // Extract from URL
      const urlPath = new URL(downloadInfo.url).pathname;
      filename = path.basename(urlPath);
    }
    
    if (!filename || filename === '' || filename === '/') {
      // Create filename from ETSI number
      const safeNumber = item.etsiNumber.replace(/[^a-zA-Z0-9-_]/g, '_');
      const ext = downloadInfo.type === 'pdf' ? '.pdf' : 
                  downloadInfo.type === 'zip' ? '.zip' :
                  downloadInfo.type === 'draft-docx' ? '.docx' :
                  downloadInfo.type === 'draft-pdf' ? '.pdf' : '.bin';
      filename = `${safeNumber}${ext}`;
    }

    // Sanitize filename
    filename = filename.replace(/[<>:"/\\|?*]/g, '_');

    // Download content
    const buffer = Buffer.from(await response.arrayBuffer());
    
    // Skip if too small (likely an error page)
    if (buffer.length < 1000) {
      console.log(`    ⚠️ File too small (${buffer.length} bytes), likely not a valid document`);
      return null;
    }
    
    // Create subdirectory based on document type
    const typeMatch = item.etsiNumber?.match(/^(EN|TS|TR|ES|EG)/i);
    const subDir = typeMatch ? typeMatch[1].toUpperCase() : 'Other';
    const targetDir = path.join(DOWNLOAD_PATH, subDir);
    await fs.mkdir(targetDir, { recursive: true });
    
    const filePath = path.join(targetDir, filename);
    await fs.writeFile(filePath, buffer);

    return `${subDir}/${filename} (${formatBytes(buffer.length)})`;
    
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

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

downloadLatestSpecs().catch(console.error);
