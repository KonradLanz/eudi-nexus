#!/usr/bin/env node
/**
 * Iterative Reference Crawler
 * 
 * This script iteratively discovers and downloads external specifications
 * referenced by ETSI documents and their dependencies until no new specs are found.
 * 
 * Flow:
 * 1. Extract references from all currently downloaded specs (ETSI, OIDF, IETF)
 * 2. Identify external specs that are referenced but not yet downloaded
 * 3. Download the missing specs (OIDF HTML, IETF RFCs as text)
 * 4. Repeat until no new specs are discovered
 * 5. Generate final reference graph
 */

import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';
import { spawn } from 'child_process';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DOWNLOADS_PATH = path.join(__dirname, '../downloads');
const SPECS_PATH = path.join(DOWNLOADS_PATH, 'specs');
const OIDF_PATH = path.join(SPECS_PATH, 'OIDF');
const IETF_PATH = path.join(SPECS_PATH, 'IETF');

// OIDF spec URL mapping - maps spec IDs to their URLs
const OIDF_SPEC_URLS = {
  'OpenID4VP': 'https://openid.net/specs/openid-4-verifiable-presentations-1_0.html',
  'OpenID4VCI': 'https://openid.net/specs/openid-4-verifiable-credential-issuance-1_0.html',
  'OpenID4VC-HAIP': 'https://openid.net/specs/openid4vc-high-assurance-interoperability-profile-1_0.html',
  'HAIP': 'https://openid.net/specs/openid4vc-high-assurance-interoperability-profile-1_0.html',
  'OpenID Connect': 'https://openid.net/specs/openid-connect-core-1_0.html',
  'OpenID Connect Core': 'https://openid.net/specs/openid-connect-core-1_0.html',
  'OpenID Connect Discovery': 'https://openid.net/specs/openid-connect-discovery-1_0.html',
  'OpenID Connect Dynamic Client Registration': 'https://openid.net/specs/openid-connect-registration-1_0.html',
  'OpenID Federation': 'https://openid.net/specs/openid-federation-1_0.html',
  'SD-JWT': 'https://www.ietf.org/archive/id/draft-ietf-oauth-selective-disclosure-jwt-13.html',
  'SD-JWT VC': 'https://www.ietf.org/archive/id/draft-ietf-oauth-sd-jwt-vc-05.html',
  'OAuth 2.0 DPoP': 'https://datatracker.ietf.org/doc/html/rfc9449',
  'OAuth 2.0 PAR': 'https://datatracker.ietf.org/doc/html/rfc9126',
  'OAuth 2.0 RAR': 'https://datatracker.ietf.org/doc/html/rfc9396',
  'OpenID4VC': 'https://openid.net/specs/',
};

// CLI args
const args = process.argv.slice(2);

if (args.includes('--help') || args.includes('-h')) {
  console.log(`
Usage: node crawl-references.js [options]

Options:
  --depth <n>      Maximum crawl depth (default: 1)
                     1 = Only specs directly referenced by ETSI docs
                     2 = Also specs referenced by OIDF/IETF specs
                     Higher values may download thousands of RFCs
  --verbose, -v    Show detailed progress
  --help, -h       Show this help

Examples:
  node crawl-references.js              # Depth 1 (recommended)
  node crawl-references.js --depth 2    # Include secondary references
`);
  process.exit(0);
}

const MAX_DEPTH = args.includes('--depth') 
  ? parseInt(args[args.indexOf('--depth') + 1]) 
  : 1;
const VERBOSE = args.includes('--verbose') || args.includes('-v');

async function main() {
  console.log('🔄 EUDI Nexus - Iterative Reference Crawler');
  console.log('============================================');
  console.log(`Max depth: ${MAX_DEPTH}`);
  console.log('  Depth 1 = Direct ETSI references only');
  console.log('  Depth 2+ = Also follow references from downloaded specs');
  console.log('');

  // Ensure directories exist
  await fs.mkdir(OIDF_PATH, { recursive: true });
  await fs.mkdir(IETF_PATH, { recursive: true });

  let depth = 0;
  let newSpecsFound = true;

  while (newSpecsFound && depth < MAX_DEPTH) {
    depth++;
    console.log(`\n📍 Depth ${depth}`);
    console.log('─'.repeat(40));

    // Step 1: Run reference extraction
    console.log('\n1️⃣  Extracting references from all specs...');
    await runExtractReferences();

    // Step 2: Load the generated references.json
    const refsPath = path.join(DOWNLOADS_PATH, 'references.json');
    const refsData = JSON.parse(await fs.readFile(refsPath, 'utf-8'));

    // Step 3: Find specs that are referenced but not downloaded
    const missingSpecs = findMissingSpecs(refsData.graph);
    
    if (missingSpecs.oidf.length === 0 && missingSpecs.ietf.length === 0) {
      console.log('\n✅ No new external specs to download. Graph is complete!');
      newSpecsFound = false;
      break;
    }

    console.log(`\n2️⃣  Found ${missingSpecs.oidf.length} OIDF + ${missingSpecs.ietf.length} IETF specs to download`);

    // Step 4: Download missing OIDF specs
    if (missingSpecs.oidf.length > 0) {
      console.log('\n3️⃣  Downloading OIDF specifications...');
      await downloadOidfSpecs(missingSpecs.oidf);
    }

    // Step 5: Download missing IETF RFCs
    if (missingSpecs.ietf.length > 0) {
      console.log('\n4️⃣  Downloading IETF RFCs...');
      await downloadIetfRfcs(missingSpecs.ietf);
    }

    console.log(`\n✅ Depth ${depth} complete`);
  }

  if (depth >= MAX_DEPTH) {
    console.log(`\n✅ Reached maximum depth (${MAX_DEPTH}). Use --depth <n> to go deeper.`);
  }

  // Final extraction to update the graph
  console.log('\n📊 Generating final reference graph...');
  await runExtractReferences();

  // Print final stats
  const finalRefs = JSON.parse(await fs.readFile(path.join(DOWNLOADS_PATH, 'references.json'), 'utf-8'));
  console.log('\n🎯 Final Statistics');
  console.log('─'.repeat(40));
  console.log(`Total nodes: ${finalRefs.graph.nodes.length}`);
  console.log(`Total edges: ${finalRefs.graph.edges.length}`);
  console.log(`  - ETSI: ${finalRefs.graph.statistics.nodesBySource.etsi} docs`);
  console.log(`  - IETF: ${finalRefs.graph.statistics.nodesBySource.ietf} RFCs`);
  console.log(`  - OIDF: ${finalRefs.graph.statistics.nodesBySource.oidf} specs`);
  console.log(`  - ISO: ${finalRefs.graph.statistics.nodesBySource.iso} docs`);
  console.log(`  - W3C: ${finalRefs.graph.statistics.nodesBySource.w3c} docs`);
}

function findMissingSpecs(graph) {
  const missing = { oidf: [], ietf: [] };
  
  for (const node of graph.nodes) {
    // Check OIDF specs without a path (not downloaded)
    if (node.source === 'oidf' && !node.path) {
      // Check if we have a URL for this spec
      const url = findOidfUrl(node.id);
      if (url) {
        missing.oidf.push({ id: node.id, url });
      } else if (VERBOSE) {
        console.log(`   ⚠️  No URL mapping for OIDF spec: ${node.id}`);
      }
    }
    
    // Check IETF RFCs without a path
    if (node.source === 'ietf' && !node.path) {
      const rfcMatch = node.id.match(/RFC\s*(\d+)/i);
      if (rfcMatch) {
        // Strip leading zeros - RFC editor URLs don't have them
        const rfcNum = String(parseInt(rfcMatch[1], 10));
        missing.ietf.push({ id: node.id, number: rfcNum });
      }
    }
  }
  
  return missing;
}

function findOidfUrl(specId) {
  // Direct match
  if (OIDF_SPEC_URLS[specId]) {
    return OIDF_SPEC_URLS[specId];
  }
  
  // Try variations
  const variations = [
    specId,
    specId.replace(/-/g, ' '),
    specId.replace(/\s+/g, '-'),
  ];
  
  for (const variant of variations) {
    if (OIDF_SPEC_URLS[variant]) {
      return OIDF_SPEC_URLS[variant];
    }
  }
  
  // Special patterns
  if (/OpenID4VP/i.test(specId)) return OIDF_SPEC_URLS['OpenID4VP'];
  if (/OpenID4VCI/i.test(specId)) return OIDF_SPEC_URLS['OpenID4VCI'];
  if (/OpenID4VC-HAIP|HAIP/i.test(specId)) return OIDF_SPEC_URLS['OpenID4VC-HAIP'];
  if (/OpenID\s*Connect\s*Core/i.test(specId)) return OIDF_SPEC_URLS['OpenID Connect Core'];
  if (/OpenID\s*Connect\s*Discovery/i.test(specId)) return OIDF_SPEC_URLS['OpenID Connect Discovery'];
  if (/OpenID\s*Connect/i.test(specId)) return OIDF_SPEC_URLS['OpenID Connect'];
  if (/OpenID\s*Federation/i.test(specId)) return OIDF_SPEC_URLS['OpenID Federation'];
  if (/SD-JWT\s*VC/i.test(specId)) return OIDF_SPEC_URLS['SD-JWT VC'];
  if (/SD-JWT/i.test(specId)) return OIDF_SPEC_URLS['SD-JWT'];
  
  return null;
}

async function downloadOidfSpecs(specs) {
  for (const spec of specs) {
    const filename = `${spec.id.replace(/\s+/g, '_').replace(/[^\w-]/g, '')}.html`;
    const filepath = path.join(OIDF_PATH, filename);
    
    // Check if already downloaded
    try {
      await fs.access(filepath);
      console.log(`   ⏭️  ${spec.id} - already exists`);
      continue;
    } catch {}
    
    process.stdout.write(`   📄 ${spec.id}...`);
    
    try {
      const response = await fetch(spec.url, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; EUDI-Nexus/1.0)',
          'Accept': 'text/html,application/xhtml+xml,*/*',
        },
      });
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      
      const html = await response.text();
      await fs.writeFile(filepath, html, 'utf-8');
      console.log(` ✅ (${(html.length / 1024).toFixed(1)} KB)`);
      
      await sleep(300); // Rate limiting
    } catch (error) {
      console.log(` ❌ ${error.message}`);
    }
  }
}

async function downloadIetfRfcs(rfcs) {
  let failCount = 0;
  for (const rfc of rfcs) {
    const filename = `rfc${rfc.number}.txt`;
    const filepath = path.join(IETF_PATH, filename);
    
    // Check if already downloaded
    try {
      await fs.access(filepath);
      console.log(`   ⏭️  RFC ${rfc.number} - already exists`);
      continue;
    } catch {}
    
    process.stdout.write(`   📄 RFC ${rfc.number}...`);
    
    // Try with retries
    let success = false;
    for (let attempt = 1; attempt <= 3 && !success; attempt++) {
      try {
        // Try RFC editor first (plain text)
        const url = `https://www.rfc-editor.org/rfc/rfc${rfc.number}.txt`;
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15000);
        
        const response = await fetch(url, {
          headers: {
            'User-Agent': 'Mozilla/5.0 (compatible; EUDI-Nexus/1.0)',
          },
          signal: controller.signal,
        });
        
        clearTimeout(timeout);
        
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        
        const text = await response.text();
        await fs.writeFile(filepath, text, 'utf-8');
        console.log(` ✅ (${(text.length / 1024).toFixed(1)} KB)`);
        success = true;
        
        await sleep(200); // Rate limiting
      } catch (error) {
        if (attempt < 3) {
          await sleep(1000 * attempt); // Exponential backoff
        } else {
          console.log(` ❌ ${error.message}`);
          failCount++;
        }
      }
    }
    
    // Stop if too many consecutive failures (network issue)
    if (failCount > 10) {
      console.log(`\n   ⚠️  Too many failures, stopping downloads (network issue?)`);
      break;
    }
  }
}

function runExtractReferences() {
  return new Promise((resolve, reject) => {
    const child = spawn('node', ['extract-references.js', '--include-drafts'], {
      cwd: __dirname,
      stdio: VERBOSE ? 'inherit' : 'pipe',
    });
    
    let output = '';
    if (!VERBOSE) {
      child.stdout?.on('data', (data) => { output += data; });
      child.stderr?.on('data', (data) => { output += data; });
    }
    
    child.on('close', (code) => {
      if (code === 0) {
        if (!VERBOSE) {
          // Extract summary from output
          const summaryMatch = output.match(/Total nodes in graph: (\d+)/);
          const edgesMatch = output.match(/Total edges \(references\): (\d+)/);
          if (summaryMatch && edgesMatch) {
            console.log(`   → ${summaryMatch[1]} nodes, ${edgesMatch[1]} edges`);
          }
        }
        resolve();
      } else {
        reject(new Error(`extract-references.js exited with code ${code}`));
      }
    });
  });
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

main().catch(console.error);
