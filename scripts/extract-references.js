import fs from 'fs/promises';
import path from 'path';
import { createRequire } from 'module';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const require = createRequire(import.meta.url);
const pdfParse = require('pdf-parse');
import mammoth from 'mammoth';

const SPECS_PATH = path.join(__dirname, '../downloads/specs');
const OUTPUT_PATH = path.join(__dirname, '../downloads');

// CLI flags
const args = process.argv.slice(2);
const INCLUDE_DRAFTS = args.includes('--include-drafts') || args.includes('-d');

// Regex patterns for different reference types
const REF_PATTERNS = {
  // ETSI documents
  etsi: [
    /ETSI\s+(EN|TS|TR|ES|EG|SR)\s+(\d{3}\s*\d{3}(?:-\d+)?)/gi,
    /(?<![A-Z])(EN|TS|TR|ES|EG|SR)\s+(\d{3}\s*\d{3}(?:-\d+)?)/gi,
  ],
  // IETF RFCs
  ietf: [
    /RFC\s*(\d{3,5})/gi,
  ],
  // ISO/IEC standards
  iso: [
    /ISO(?:\/IEC)?\s+(\d+(?:[-–]\d+)*)/gi,
  ],
  // ITU-T recommendations
  itu: [
    /ITU-T\s+([A-Z]\.?\s*\d+(?:\.\d+)?)/gi,
  ],
  // W3C specifications
  w3c: [
    /W3C\s+([\w-]+)/gi,
  ],
  // OpenID Foundation (OIDF) specifications
  oidf: [
    /OpenID4VP(?:\s+[\d.]+)?/gi,
    /OpenID4VCI(?:\s+[\d.]+)?/gi,
    /OpenID4VC(?:-HAIP)?(?:\s+[\d.]+)?/gi,
    /OpenID\s+Connect(?:\s+Core)?(?:\s+[\d.]+)?/gi,
    /OpenID\s+for\s+Verifiable\s+(?:Presentations?|Credentials?)(?:\s+[\d.]+)?/gi,
    /SD-JWT(?:\s+VC)?/gi,
    /\bHAIP\b/g,
  ],
  // CEN/CENELEC European standards
  cen: [
    /(?:CEN|CENELEC)\s+(\d+(?:[-–]\d+)*)/gi,
  ],
};

async function extractReferences() {
  console.log('ETSI Reference Extractor');
  console.log('========================');
  if (INCLUDE_DRAFTS) {
    console.log('(including draft documents)');
  }
  console.log('');

  // Find all documents (PDFs and optionally Word drafts)
  const pdfFiles = await findPdfFiles(SPECS_PATH);
  const docxFiles = INCLUDE_DRAFTS ? await findDocxFiles(SPECS_PATH) : [];
  
  console.log(`Found ${pdfFiles.length} PDF files to analyze`);
  if (INCLUDE_DRAFTS) {
    console.log(`Found ${docxFiles.length} Word draft documents to analyze`);
  }
  console.log('');

  const graph = {
    nodes: new Map(), // docId -> { id, title, type, path, referencesCount, referencedByCount }
    edges: [],        // { from, to, type: 'normative'|'informative' }
  };

  const results = {
    documents: [],
    errors: [],
  };

  for (let i = 0; i < pdfFiles.length; i++) {
    const pdfPath = pdfFiles[i];
    const filename = path.basename(pdfPath);
    const progress = `[${i + 1}/${pdfFiles.length}]`;
    
    process.stdout.write(`${progress} ${filename}...`);
    
    try {
      const refs = await extractReferencesFromPdf(pdfPath);
      
      // Get document ID from filename
      const docId = normalizeDocId(filenameToDocId(filename));
      
      if (docId) {
        // Count total refs across all types
        const countRefs = (obj) => Object.values(obj).reduce((sum, arr) => sum + arr.length, 0);
        const normativeCount = countRefs(refs.normative);
        const informativeCount = countRefs(refs.informative);
        const totalCount = countRefs(refs.all);
        
        // Add source node
        if (!graph.nodes.has(docId)) {
          graph.nodes.set(docId, {
            id: docId,
            type: docId.split(' ')[0],
            source: 'etsi',
            path: pdfPath,
            referencesCount: 0,
            referencedByCount: 0,
          });
        }
        graph.nodes.get(docId).referencesCount = totalCount;
        
        // Add ETSI edges
        for (const ref of refs.normative.etsi) {
          const targetId = normalizeDocId(ref);
          if (targetId && targetId !== docId) {
            graph.edges.push({ from: docId, to: targetId, type: 'normative', source: 'etsi' });
            ensureNode(graph, targetId, 'etsi');
            graph.nodes.get(targetId).referencedByCount++;
          }
        }
        
        for (const ref of refs.informative.etsi) {
          const targetId = normalizeDocId(ref);
          if (targetId && targetId !== docId) {
            graph.edges.push({ from: docId, to: targetId, type: 'informative', source: 'etsi' });
            ensureNode(graph, targetId, 'etsi');
            graph.nodes.get(targetId).referencedByCount++;
          }
        }
        
        // Add external refs (IETF, ISO, ITU, W3C, OIDF)
        const externalTypes = ['ietf', 'iso', 'itu', 'w3c', 'oidf'];
        for (const extType of externalTypes) {
          for (const ref of refs.normative[extType] || []) {
            graph.edges.push({ from: docId, to: ref, type: 'normative', source: extType });
            ensureExternalNode(graph, ref, extType);
            graph.nodes.get(ref).referencedByCount++;
          }
          for (const ref of refs.informative[extType] || []) {
            graph.edges.push({ from: docId, to: ref, type: 'informative', source: extType });
            ensureExternalNode(graph, ref, extType);
            graph.nodes.get(ref).referencedByCount++;
          }
        }
        
        results.documents.push({
          file: filename,
          docId,
          normativeRefs: normativeCount,
          informativeRefs: informativeCount,
          totalRefs: totalCount,
          references: refs,
        });
        
        const extCounts = externalTypes.map(t => 
          (refs.normative[t]?.length || 0) + (refs.informative[t]?.length || 0)
        ).filter(n => n > 0);
        const extInfo = extCounts.length > 0 ? ` + ${extCounts.reduce((a,b)=>a+b,0)} external` : '';
        
        console.log(`   OK: ${refs.normative.etsi.length} norm, ${refs.informative.etsi.length} info${extInfo}`);
      } else {
        console.log(`   WARN: Could not determine doc ID`);
      }
      
    } catch (error) {
      console.log(`   ERROR: ${error.message}`);
      results.errors.push({ file: filename, error: error.message });
    }
  }

  // Process Word documents (drafts)
  if (INCLUDE_DRAFTS && docxFiles.length > 0) {
    console.log('\nProcessing draft documents...');
    
    for (let i = 0; i < docxFiles.length; i++) {
      const docxPath = docxFiles[i];
      const filename = path.basename(docxPath);
      const progress = `[${i + 1}/${docxFiles.length}]`;
      
      process.stdout.write(`${progress} ${filename} (draft)...`);
      
      try {
        const refs = await extractReferencesFromDocx(docxPath);
        
        // Get document ID from filename
        const docId = normalizeDocId(filenameToDocId(filename)) || docxFilenameToDocId(filename);
        
        if (docId) {
          const countRefs = (obj) => Object.values(obj).reduce((sum, arr) => sum + arr.length, 0);
          const normativeCount = countRefs(refs.normative);
          const informativeCount = countRefs(refs.informative);
          const totalCount = countRefs(refs.all);
          
          // Add source node (marked as draft)
          if (!graph.nodes.has(docId)) {
            graph.nodes.set(docId, {
              id: docId,
              type: docId.split(' ')[0],
              source: 'etsi',
              path: docxPath,
              referencesCount: 0,
              referencedByCount: 0,
              isDraft: true,
            });
          } else {
            // Update existing node to mark as draft if it wasn't downloaded as PDF
            const node = graph.nodes.get(docId);
            if (!node.path || node.path.endsWith('.docx')) {
              node.isDraft = true;
              node.path = docxPath;
            }
          }
          graph.nodes.get(docId).referencesCount = totalCount;
          
          // Add ETSI edges
          for (const ref of refs.normative.etsi) {
            const targetId = normalizeDocId(ref);
            if (targetId && targetId !== docId) {
              graph.edges.push({ from: docId, to: targetId, type: 'normative', source: 'etsi' });
              ensureNode(graph, targetId, 'etsi');
              graph.nodes.get(targetId).referencedByCount++;
            }
          }
          
          for (const ref of refs.informative.etsi) {
            const targetId = normalizeDocId(ref);
            if (targetId && targetId !== docId) {
              graph.edges.push({ from: docId, to: targetId, type: 'informative', source: 'etsi' });
              ensureNode(graph, targetId, 'etsi');
              graph.nodes.get(targetId).referencedByCount++;
            }
          }
          
          // Add external refs
          const externalTypes = ['ietf', 'iso', 'itu', 'w3c', 'oidf'];
          for (const extType of externalTypes) {
            for (const ref of refs.normative[extType] || []) {
              graph.edges.push({ from: docId, to: ref, type: 'normative', source: extType });
              ensureExternalNode(graph, ref, extType);
              graph.nodes.get(ref).referencedByCount++;
            }
            for (const ref of refs.informative[extType] || []) {
              graph.edges.push({ from: docId, to: ref, type: 'informative', source: extType });
              ensureExternalNode(graph, ref, extType);
              graph.nodes.get(ref).referencedByCount++;
            }
          }
          
          results.documents.push({
            file: filename,
            docId,
            isDraft: true,
            normativeRefs: normativeCount,
            informativeRefs: informativeCount,
            totalRefs: totalCount,
            references: refs,
          });
          
          const extCounts = externalTypes.map(t => 
            (refs.normative[t]?.length || 0) + (refs.informative[t]?.length || 0)
          ).filter(n => n > 0);
          const extInfo = extCounts.length > 0 ? ` + ${extCounts.reduce((a,b)=>a+b,0)} external` : '';
          
          console.log(`   OK: ${refs.normative.etsi.length} norm, ${refs.informative.etsi.length} info${extInfo}`);
        } else {
          console.log(`   WARN: Could not determine doc ID`);
        }
        
      } catch (error) {
        console.log(`   ERROR: ${error.message}`);
        results.errors.push({ file: filename, error: error.message, isDraft: true });
      }
    }
  }

  // Convert Map to array for JSON
  const graphData = {
    nodes: Array.from(graph.nodes.values()),
    edges: graph.edges,
    statistics: {
      totalDocuments: graph.nodes.size,
      totalReferences: graph.edges.length,
      normativeRefs: graph.edges.filter(e => e.type === 'normative').length,
      informativeRefs: graph.edges.filter(e => e.type === 'informative').length,
      bySource: {
        etsi: graph.edges.filter(e => e.source === 'etsi').length,
        ietf: graph.edges.filter(e => e.source === 'ietf').length,
        iso: graph.edges.filter(e => e.source === 'iso').length,
        itu: graph.edges.filter(e => e.source === 'itu').length,
        w3c: graph.edges.filter(e => e.source === 'w3c').length,
        oidf: graph.edges.filter(e => e.source === 'oidf').length,
      },
      nodesBySource: {
        etsi: graph.nodes.size - [...graph.nodes.values()].filter(n => ['ietf','iso','itu','w3c','oidf'].includes(n.source)).length,
        ietf: [...graph.nodes.values()].filter(n => n.source === 'ietf').length,
        iso: [...graph.nodes.values()].filter(n => n.source === 'iso').length,
        itu: [...graph.nodes.values()].filter(n => n.source === 'itu').length,
        w3c: [...graph.nodes.values()].filter(n => n.source === 'w3c').length,
        oidf: [...graph.nodes.values()].filter(n => n.source === 'oidf').length,
      },
      draftDocuments: [...graph.nodes.values()].filter(n => n.isDraft).length,
    }
  };

  // Save results
  await fs.writeFile(
    path.join(OUTPUT_PATH, 'references.json'),
    JSON.stringify({ ...results, graph: graphData }, null, 2)
  );

  // Generate DOT format for Graphviz
  const dot = generateDotGraph(graphData);
  await fs.writeFile(path.join(OUTPUT_PATH, 'references.dot'), dot);

  // Generate Mermaid format
  const mermaid = generateMermaidGraph(graphData);
  await fs.writeFile(path.join(OUTPUT_PATH, 'references.mmd'), mermaid);

  // Generate HTML visualization
  const html = generateHtmlVisualization(graphData);
  await fs.writeFile(path.join(OUTPUT_PATH, 'index.html'), html);

  // Print summary
  console.log('\nSummary');
  console.log('=======');
  const draftCount = results.documents.filter(d => d.isDraft).length;
  const publishedCount = results.documents.length - draftCount;
  console.log(`Documents analyzed: ${results.documents.length} (${publishedCount} published, ${draftCount} drafts)`);
  console.log(`Errors: ${results.errors.length}`);
  console.log(`Total nodes in graph: ${graphData.nodes.length}`);
  console.log(`Total edges (references): ${graphData.edges.length}`);
  console.log(`  - Normative: ${graphData.statistics.normativeRefs}`);
  console.log(`  - Informative: ${graphData.statistics.informativeRefs}`);
  
  console.log('\nReferences by Source:');
  console.log(`  - ETSI: ${graphData.statistics.bySource.etsi} refs to ${graphData.statistics.nodesBySource.etsi} docs`);
  console.log(`  - IETF (RFC): ${graphData.statistics.bySource.ietf} refs to ${graphData.statistics.nodesBySource.ietf} docs`);
  console.log(`  - ISO/IEC: ${graphData.statistics.bySource.iso} refs to ${graphData.statistics.nodesBySource.iso} docs`);
  console.log(`  - ITU-T: ${graphData.statistics.bySource.itu} refs to ${graphData.statistics.nodesBySource.itu} docs`);
  console.log(`  - W3C: ${graphData.statistics.bySource.w3c} refs to ${graphData.statistics.nodesBySource.w3c} docs`);
  console.log(`  - OIDF: ${graphData.statistics.bySource.oidf} refs to ${graphData.statistics.nodesBySource.oidf} specs`);
  
  // Most referenced documents
  const topReferenced = graphData.nodes
    .filter(n => n.referencedByCount > 0)
    .sort((a, b) => b.referencedByCount - a.referencedByCount)
    .slice(0, 15);
  
  console.log('\nMost Referenced Documents:');
  for (const node of topReferenced) {
    const source = node.source !== 'etsi' ? ` (${node.source.toUpperCase()})` : '';
    console.log(`   ${node.id}${source}: ${node.referencedByCount} times`);
  }

  console.log('\nOutput files:');
  console.log('   - references.json (full data)');
  console.log('   - references.dot (Graphviz)');
  console.log('   - references.mmd (Mermaid)');
  console.log('   - index.html (interactive visualization)');
}

async function findPdfFiles(dir) {
  const files = [];
  const entries = await fs.readdir(dir, { withFileTypes: true });
  
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...await findPdfFiles(fullPath));
    } else if (entry.name.endsWith('.pdf')) {
      files.push(fullPath);
    }
  }
  
  return files.sort();
}

async function findDocxFiles(dir) {
  const files = [];
  const entries = await fs.readdir(dir, { withFileTypes: true });
  
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...await findDocxFiles(fullPath));
    } else if (entry.name.endsWith('.docx') || entry.name.endsWith('.doc')) {
      files.push(fullPath);
    }
  }
  
  return files.sort();
}

async function extractReferencesFromDocx(docxPath) {
  const result = await mammoth.extractRawText({ path: docxPath });
  const text = result.value;

  // Find the References section (same logic as PDF)
  const refsSection = extractReferencesSection(text);
  
  const normative = { etsi: new Set(), ietf: new Set(), iso: new Set(), itu: new Set(), w3c: new Set(), oidf: new Set() };
  const informative = { etsi: new Set(), ietf: new Set(), iso: new Set(), itu: new Set(), w3c: new Set(), oidf: new Set() };
  const all = { etsi: new Set(), ietf: new Set(), iso: new Set(), itu: new Set(), w3c: new Set(), oidf: new Set() };

  // Extract from normative section
  if (refsSection.normative) {
    const refs = extractAllRefs(refsSection.normative);
    for (const [type, set] of Object.entries(refs)) {
      for (const ref of set) {
        normative[type]?.add(ref);
        all[type]?.add(ref);
      }
    }
  }

  // Extract from informative section
  if (refsSection.informative) {
    const refs = extractAllRefs(refsSection.informative);
    for (const [type, set] of Object.entries(refs)) {
      for (const ref of set) {
        if (!normative[type]?.has(ref)) {
          informative[type]?.add(ref);
        }
        all[type]?.add(ref);
      }
    }
  }

  // If no structured sections found, search whole document
  const hasStructuredRefs = normative.etsi.size > 0 || informative.etsi.size > 0;
  if (!hasStructuredRefs) {
    const refs = extractAllRefs(text);
    for (const [type, set] of Object.entries(refs)) {
      for (const ref of set) {
        all[type]?.add(ref);
      }
    }
  }

  // Convert to arrays
  const toArrays = (obj) => {
    const result = {};
    for (const [type, set] of Object.entries(obj)) {
      result[type] = Array.from(set).sort();
    }
    return result;
  };

  return {
    normative: toArrays(normative),
    informative: toArrays(informative),
    all: toArrays(all),
  };
}

// Parse draft filenames like ESI-0019472-2v121v114.docx
function docxFilenameToDocId(filename) {
  // Format: ESI-XXXYYYYY-Zv...
  const match = filename.match(/ESI-(\d{3})(\d{4,5})-?(\d)?/i);
  if (match) {
    const num1 = match[1];
    const num2 = match[2].slice(0, 3);
    const part = match[3] ? `-${parseInt(match[3], 10)}` : '';
    return `TS ${num1} ${num2}${part}`;
  }
  
  // Try other patterns
  const match2 = filename.match(/(TS|TR|EN|ES)[\s_-]*(\d{3})[\s_-]*(\d{3})(?:[\s_-]*(\d+))?/i);
  if (match2) {
    const type = match2[1].toUpperCase();
    const num1 = match2[2];
    const num2 = match2[3];
    const part = match2[4] ? `-${parseInt(match2[4], 10)}` : '';
    return `${type} ${num1} ${num2}${part}`;
  }
  
  return null;
}

async function extractReferencesFromPdf(pdfPath) {
  const dataBuffer = await fs.readFile(pdfPath);
  const data = await pdfParse(dataBuffer);
  const text = data.text;

  // Find the References section
  const refsSection = extractReferencesSection(text);
  
  const normative = { etsi: new Set(), ietf: new Set(), iso: new Set(), itu: new Set(), w3c: new Set(), oidf: new Set() };
  const informative = { etsi: new Set(), ietf: new Set(), iso: new Set(), itu: new Set(), w3c: new Set(), oidf: new Set() };
  const all = { etsi: new Set(), ietf: new Set(), iso: new Set(), itu: new Set(), w3c: new Set(), oidf: new Set() };

  // Extract from normative section
  if (refsSection.normative) {
    const refs = extractAllRefs(refsSection.normative);
    for (const [type, set] of Object.entries(refs)) {
      for (const ref of set) {
        normative[type]?.add(ref);
        all[type]?.add(ref);
      }
    }
  }

  // Extract from informative section
  if (refsSection.informative) {
    const refs = extractAllRefs(refsSection.informative);
    for (const [type, set] of Object.entries(refs)) {
      for (const ref of set) {
        if (!normative[type]?.has(ref)) {
          informative[type]?.add(ref);
        }
        all[type]?.add(ref);
      }
    }
  }

  // If no structured sections found, search whole document for ETSI only
  const hasStructuredRefs = normative.etsi.size > 0 || informative.etsi.size > 0;
  if (!hasStructuredRefs) {
    const refs = extractAllRefs(text);
    for (const [type, set] of Object.entries(refs)) {
      for (const ref of set) {
        all[type]?.add(ref);
      }
    }
  }

  // Convert to arrays
  const toArrays = (obj) => {
    const result = {};
    for (const [type, set] of Object.entries(obj)) {
      result[type] = Array.from(set).sort();
    }
    return result;
  };

  return {
    normative: toArrays(normative),
    informative: toArrays(informative),
    all: toArrays(all),
  };
}

function extractReferencesSection(text) {
  const result = { normative: null, informative: null };
  
  // Common section patterns
  const normativePatterns = [
    /(?:^|\n)\s*2\.?1?\s*Normative\s+references?\s*\n([\s\S]*?)(?=\n\s*(?:2\.?2|3|Informative|Definition|Terms|Abbreviation))/i,
    /(?:^|\n)\s*Normative\s+references?\s*\n([\s\S]*?)(?=\n\s*(?:Informative|Definition|Terms|Abbreviation|\d+\s+[A-Z]))/i,
  ];
  
  const informativePatterns = [
    /(?:^|\n)\s*2\.?2?\s*Informative\s+references?\s*\n([\s\S]*?)(?=\n\s*(?:3|Definition|Terms|Abbreviation|\d+\s+[A-Z]))/i,
    /(?:^|\n)\s*Informative\s+references?\s*\n([\s\S]*?)(?=\n\s*(?:Definition|Terms|Abbreviation|\d+\s+[A-Z]))/i,
  ];
  
  for (const pattern of normativePatterns) {
    const match = text.match(pattern);
    if (match) {
      result.normative = match[1];
      break;
    }
  }
  
  for (const pattern of informativePatterns) {
    const match = text.match(pattern);
    if (match) {
      result.informative = match[1];
      break;
    }
  }
  
  return result;
}

function extractAllRefs(text) {
  const refs = {
    etsi: new Set(),
    ietf: new Set(),
    iso: new Set(),
    itu: new Set(),
    w3c: new Set(),
    oidf: new Set(),
    cen: new Set(),
  };
  
  // Extract ETSI refs
  for (const pattern of REF_PATTERNS.etsi) {
    pattern.lastIndex = 0;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      const type = match[1].toUpperCase();
      const number = match[2].replace(/\s+/g, ' ').trim();
      refs.etsi.add(`${type} ${number}`);
    }
  }
  
  // Extract IETF RFCs
  for (const pattern of REF_PATTERNS.ietf) {
    pattern.lastIndex = 0;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      refs.ietf.add(`RFC ${match[1]}`);
    }
  }
  
  // Extract ISO/IEC
  for (const pattern of REF_PATTERNS.iso) {
    pattern.lastIndex = 0;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      const num = match[1].replace(/[–]/g, '-');
      refs.iso.add(`ISO ${num}`);
    }
  }
  
  // Extract ITU-T
  for (const pattern of REF_PATTERNS.itu) {
    pattern.lastIndex = 0;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      const rec = match[1].replace(/\s+/g, '').replace(/\./g, '');
      refs.itu.add(`ITU-T ${rec}`);
    }
  }
  
  // Extract W3C
  for (const pattern of REF_PATTERNS.w3c) {
    pattern.lastIndex = 0;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      if (match[1].toLowerCase() !== 'technical' && match[1].toLowerCase() !== 'recommendation') {
        refs.w3c.add(`W3C ${match[1]}`);
      }
    }
  }
  
  // Extract OIDF (OpenID Foundation)
  for (const pattern of REF_PATTERNS.oidf) {
    pattern.lastIndex = 0;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      let spec = match[0].trim();
      // Normalize common variations
      if (/OpenID4VP/i.test(spec)) spec = 'OpenID4VP';
      else if (/OpenID4VCI/i.test(spec)) spec = 'OpenID4VCI';
      else if (/OpenID4VC-HAIP|OpenID4VC HAIP/i.test(spec)) spec = 'OpenID4VC-HAIP';
      else if (/OpenID4VC/i.test(spec)) spec = 'OpenID4VC';
      else if (/OpenID\s+(for\s+)?Verifiable\s+Presentation/i.test(spec)) spec = 'OpenID4VP';
      else if (/OpenID\s+(for\s+)?Verifiable\s+Credential/i.test(spec)) spec = 'OpenID4VCI';
      else if (/OpenID\s+Connect/i.test(spec)) spec = 'OpenID Connect';
      else if (/SD-JWT\s*VC/i.test(spec)) spec = 'SD-JWT VC';
      else if (/SD-JWT/i.test(spec)) spec = 'SD-JWT';
      else if (/^HAIP$/i.test(spec)) spec = 'HAIP';
      refs.oidf.add(spec);
    }
  }
  
  return refs;
}

function extractEtsiRefs(text) {
  const refs = new Set();
  
  for (const pattern of REF_PATTERNS.etsi) {
    pattern.lastIndex = 0; // Reset regex
    let match;
    while ((match = pattern.exec(text)) !== null) {
      const type = match[1].toUpperCase();
      const number = match[2].replace(/\s+/g, ' ').trim();
      refs.add(`${type} ${number}`);
    }
  }
  
  return refs;
}

function filenameToDocId(filename) {
  // Parse filenames like: en_319403v020202p.pdf, ts_11910201v010201p.pdf
  const match = filename.match(/^(en|ts|tr|es|eg|sr)_(\d+)(?:v\d+)?/i);
  if (match) {
    const type = match[1].toUpperCase();
    let num = match[2];
    
    // Convert number format: 319403 -> 319 403, 11910201 -> 119 102-01
    if (num.length === 6) {
      // Simple: 319403 -> 319 403
      return `${type} ${num.slice(0, 3)} ${num.slice(3)}`;
    } else if (num.length === 8) {
      // With part: 11910201 -> 119 102-1
      const part = parseInt(num.slice(6), 10);
      return `${type} ${num.slice(0, 3)} ${num.slice(3, 6)}-${part}`;
    } else if (num.length === 9) {
      // With part: 119102010 -> 119 102-10 (double digit part)
      const part = parseInt(num.slice(6), 10);
      return `${type} ${num.slice(0, 3)} ${num.slice(3, 6)}-${part}`;
    }
    
    return `${type} ${num}`;
  }
  return null;
}

function normalizeDocId(ref) {
  if (!ref) return null;
  
  // Normalize: "EN 319 412-1" format
  const match = ref.match(/(EN|TS|TR|ES|EG|SR)\s*(\d{3})\s*(\d{3})(?:-(\d+))?/i);
  if (match) {
    const type = match[1].toUpperCase();
    const num1 = match[2];
    const num2 = match[3];
    const part = match[4] ? `-${parseInt(match[4], 10)}` : '';
    return `${type} ${num1} ${num2}${part}`;
  }
  return null;
}

function ensureNode(graph, docId, source = 'etsi') {
  if (!graph.nodes.has(docId)) {
    graph.nodes.set(docId, {
      id: docId,
      type: docId.split(' ')[0],
      source: source,
      path: null,
      referencesCount: 0,
      referencedByCount: 0,
    });
  }
}

function ensureExternalNode(graph, ref, source) {
  if (!graph.nodes.has(ref)) {
    graph.nodes.set(ref, {
      id: ref,
      type: ref.split(' ')[0],
      source: source,
      path: null,
      referencesCount: 0,
      referencedByCount: 0,
    });
  }
}

function generateDotGraph(graphData) {
  const lines = [
    'digraph ETSIReferences {',
    '  rankdir=LR;',
    '  node [shape=box, style=filled];',
    '',
    '  // Node colors by type',
  ];
  
  const typeColors = {
    EN: '#4CAF50',  // Green - European Norms
    TS: '#2196F3',  // Blue - Technical Specifications
    TR: '#FF9800',  // Orange - Technical Reports
    ES: '#9C27B0',  // Purple
    EG: '#795548',  // Brown
    SR: '#607D8B',  // Gray
  };
  
  // Add nodes
  for (const node of graphData.nodes) {
    const color = typeColors[node.type] || '#CCCCCC';
    const label = node.id.replace(/ /g, '\\n');
    const penwidth = node.path ? 2 : 1; // Downloaded docs have thicker border
    lines.push(`  "${node.id}" [label="${label}", fillcolor="${color}", penwidth=${penwidth}];`);
  }
  
  lines.push('');
  lines.push('  // Edges');
  
  // Add edges
  for (const edge of graphData.edges) {
    const style = edge.type === 'normative' ? 'solid' : 'dashed';
    const color = edge.type === 'normative' ? '#333333' : '#999999';
    lines.push(`  "${edge.from}" -> "${edge.to}" [style=${style}, color="${color}"];`);
  }
  
  lines.push('}');
  return lines.join('\n');
}

function generateMermaidGraph(graphData) {
  const lines = [
    'flowchart LR',
    '',
    '  %% Style definitions',
    '  classDef en fill:#4CAF50,stroke:#333,stroke-width:2px',
    '  classDef ts fill:#2196F3,stroke:#333,stroke-width:2px',
    '  classDef tr fill:#FF9800,stroke:#333,stroke-width:2px',
    '  classDef other fill:#9E9E9E,stroke:#333,stroke-width:2px',
    '',
  ];
  
  // Mermaid has limits, so we'll only show the most connected nodes
  const topNodes = graphData.nodes
    .filter(n => n.referencedByCount > 2 || n.referencesCount > 2)
    .map(n => n.id);
  
  const nodeSet = new Set(topNodes);
  
  // Add edges for these nodes
  const relevantEdges = graphData.edges.filter(
    e => nodeSet.has(e.from) && nodeSet.has(e.to)
  );
  
  lines.push('  %% Nodes and edges (showing highly connected docs only)');
  
  for (const edge of relevantEdges.slice(0, 100)) { // Limit for readability
    const fromId = edge.from.replace(/ /g, '_').replace(/-/g, '_');
    const toId = edge.to.replace(/ /g, '_').replace(/-/g, '_');
    const fromLabel = edge.from;
    const toLabel = edge.to;
    const arrow = edge.type === 'normative' ? '-->' : '-.->';
    lines.push(`  ${fromId}["${fromLabel}"] ${arrow} ${toId}["${toLabel}"]`);
  }
  
  // Apply classes
  lines.push('');
  for (const node of graphData.nodes.filter(n => nodeSet.has(n.id))) {
    const nodeId = node.id.replace(/ /g, '_').replace(/-/g, '_');
    const className = node.type.toLowerCase();
    if (['en', 'ts', 'tr'].includes(className)) {
      lines.push(`  class ${nodeId} ${className}`);
    }
  }
  
  return lines.join('\n');
}

function generateHtmlVisualization(graphData) {
  return `<!DOCTYPE html>
<html>
<head>
  <title>ETSI ESI Reference Graph</title>
  <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; }
    h1 { margin-top: 0; }
    #graph { width: 100%; height: 700px; border: 1px solid #ddd; border-radius: 8px; }
    .stats { display: flex; gap: 20px; margin-bottom: 20px; }
    .stat { background: #f5f5f5; padding: 15px 20px; border-radius: 8px; }
    .stat-value { font-size: 24px; font-weight: bold; color: #333; }
    .stat-label { font-size: 12px; color: #666; text-transform: uppercase; }
    .legend { display: flex; gap: 15px; margin-bottom: 15px; flex-wrap: wrap; }
    .legend-item { display: flex; align-items: center; gap: 5px; font-size: 14px; }
    .legend-color { width: 16px; height: 16px; border-radius: 3px; }
    .controls { margin-bottom: 15px; }
    .controls label { margin-right: 15px; }
    #info { margin-top: 15px; padding: 15px; background: #f9f9f9; border-radius: 8px; display: none; }
  </style>
</head>
<body>
  <h1>ETSI ESI Reference Graph</h1>
  
  <div class="stats">
    <div class="stat">
      <div class="stat-value">${graphData.nodes.length}</div>
      <div class="stat-label">Documents</div>
    </div>
    <div class="stat">
      <div class="stat-value">${graphData.edges.length}</div>
      <div class="stat-label">References</div>
    </div>
    <div class="stat">
      <div class="stat-value">${graphData.statistics.normativeRefs}</div>
      <div class="stat-label">Normative</div>
    </div>
    <div class="stat">
      <div class="stat-value">${graphData.statistics.informativeRefs}</div>
      <div class="stat-label">Informative</div>
    </div>
  </div>
  
  <div class="legend">
    <div class="legend-item"><div class="legend-color" style="background:#4CAF50"></div> EN (European Norm)</div>
    <div class="legend-item"><div class="legend-color" style="background:#2196F3"></div> TS (Technical Specification)</div>
    <div class="legend-item"><div class="legend-color" style="background:#FF9800"></div> TR (Technical Report)</div>
    <div class="legend-item"><div class="legend-color" style="background:#9C27B0"></div> ETSI Other</div>
    <div class="legend-item"><div class="legend-color" style="background:#E91E63"></div> IETF RFC</div>
    <div class="legend-item"><div class="legend-color" style="background:#00BCD4"></div> ISO/IEC</div>
    <div class="legend-item"><div class="legend-color" style="background:#CDDC39"></div> ITU-T</div>
    <div class="legend-item"><div class="legend-color" style="background:#8BC34A"></div> OIDF (OpenID)</div>
    <div class="legend-item"><div class="legend-color" style="border:2px dashed #333; background:transparent"></div> Draft Document</div>
    <div class="legend-item"><span style="display:inline-block;width:30px;height:2px;background:#333;vertical-align:middle"></span> Normative</div>
    <div class="legend-item"><span style="display:inline-block;width:30px;border-top:2px dashed #999;vertical-align:middle"></span> Informative</div>
  </div>
  
  <div class="controls">
    <label><input type="checkbox" id="showNormative" checked> Normative refs</label>
    <label><input type="checkbox" id="showInformative" checked> Informative refs</label>
    <label><input type="checkbox" id="showExternal" checked> External standards (IETF, ISO, ITU, OIDF)</label>
    <label><input type="checkbox" id="showDrafts" checked> Draft documents</label>
    <label><input type="checkbox" id="showUndownloaded"> ETSI docs not downloaded</label>
  </div>
  
  <div id="graph"></div>
  <div id="info"></div>

  <script>
    const graphData = ${JSON.stringify(graphData)};
    
    const sourceColors = {
      etsi: { EN: '#4CAF50', TS: '#2196F3', TR: '#FF9800', ES: '#9C27B0', EG: '#795548', SR: '#607D8B' },
      ietf: '#E91E63',
      iso: '#00BCD4',
      itu: '#CDDC39',
      w3c: '#FF5722',
      oidf: '#8BC34A',
    };
    
    function getNodeColor(node) {
      if (node.source === 'etsi') {
        return sourceColors.etsi[node.type] || '#9E9E9E';
      }
      return sourceColors[node.source] || '#9E9E9E';
    }
    
    // Generate URL for a document based on its source
    function getDocumentUrl(node) {
      const id = node.id;
      const source = node.source;
      
      if (source === 'etsi') {
        // ETSI: search page with document number
        // e.g. "TS 119 472-2" -> search for "119 472-2"
        const match = id.match(/(EN|TS|TR|ES|EG|SR)\\s+(\\d+)\\s+(\\d+)(?:-(\\d+))?/);
        if (match) {
          const type = match[1];
          const num1 = match[2];
          const num2 = match[3];
          const part = match[4] || '';
          const query = part ? \`\${num1} \${num2}-\${part}\` : \`\${num1} \${num2}\`;
          return \`https://www.etsi.org/deliver/etsi_\${type.toLowerCase()}/\${num1}\${num2}_\${num1}\${num2}99/\`;
        }
        return \`https://www.etsi.org/standards#page=1&search=\${encodeURIComponent(id)}\`;
      }
      
      if (source === 'ietf') {
        // IETF RFC: direct link to datatracker
        const rfcMatch = id.match(/RFC\\s*(\\d+)/i);
        if (rfcMatch) {
          return \`https://datatracker.ietf.org/doc/html/rfc\${rfcMatch[1]}\`;
        }
      }
      
      if (source === 'iso') {
        // ISO: search page
        const isoMatch = id.match(/ISO\\s*([\\d-]+)/i);
        if (isoMatch) {
          return \`https://www.iso.org/search.html?q=\${encodeURIComponent(isoMatch[1])}\`;
        }
      }
      
      if (source === 'itu') {
        // ITU-T: recommendation page
        const ituMatch = id.match(/ITU-T\\s*([A-Z])(\\d+)/i);
        if (ituMatch) {
          return \`https://www.itu.int/rec/T-REC-\${ituMatch[1].toUpperCase()}.\${ituMatch[2]}\`;
        }
      }
      
      if (source === 'w3c') {
        // W3C: TR page
        return \`https://www.w3.org/TR/\`;
      }
      
      if (source === 'oidf') {
        // OIDF: OpenID Foundation specs
        if (/OpenID4VP/i.test(id)) return 'https://openid.net/specs/openid-4-verifiable-presentations-1_0.html';
        if (/OpenID4VCI/i.test(id)) return 'https://openid.net/specs/openid-4-verifiable-credential-issuance-1_0.html';
        if (/OpenID4VC-HAIP|HAIP/i.test(id)) return 'https://openid.net/specs/openid4vc-high-assurance-interoperability-profile-1_0.html';
        if (/OpenID4VC/i.test(id)) return 'https://openid.net/specs/';
        if (/OpenID Connect/i.test(id)) return 'https://openid.net/specs/openid-connect-core-1_0.html';
        if (/SD-JWT/i.test(id)) return 'https://datatracker.ietf.org/doc/html/rfc9449';
        return 'https://openid.net/developers/specs/';
      }
      
      return null;
    }
    
    function buildNetwork() {
      const showNormative = document.getElementById('showNormative').checked;
      const showInformative = document.getElementById('showInformative').checked;
      const showExternal = document.getElementById('showExternal').checked;
      const showDrafts = document.getElementById('showDrafts').checked;
      const showUndownloaded = document.getElementById('showUndownloaded').checked;
      
      // Filter edges
      const filteredEdges = graphData.edges.filter(e => {
        if (e.type === 'normative' && !showNormative) return false;
        if (e.type === 'informative' && !showInformative) return false;
        if (!showExternal && ['ietf', 'iso', 'itu', 'w3c', 'oidf'].includes(e.source)) return false;
        return true;
      });
      
      // Collect node IDs from filtered edges
      const nodeIds = new Set();
      filteredEdges.forEach(e => {
        nodeIds.add(e.from);
        nodeIds.add(e.to);
      });
      
      const filteredNodes = graphData.nodes.filter(n => {
        if (!nodeIds.has(n.id)) return false;
        // External standards (IETF, ISO, ITU)
        if (['ietf', 'iso', 'itu', 'w3c', 'oidf'].includes(n.source)) return showExternal;
        // Draft documents
        if (n.isDraft && !showDrafts) return false;
        // ETSI docs not downloaded
        if (n.source === 'etsi' && !n.path) return showUndownloaded;
        return true;
      });
      
      const nodes = new vis.DataSet(filteredNodes.map(n => ({
        id: n.id,
        label: n.isDraft ? n.id + ' (draft)' : n.id,
        color: {
          background: getNodeColor(n),
          border: n.isDraft ? '#FF5722' : (n.path ? '#333' : '#ccc'),
        },
        borderWidth: n.path ? 2 : 1,
        shapeProperties: {
          borderDashes: n.isDraft ? [5, 5] : false,
        },
        font: { size: 12 },
        title: \`\${n.id} (\${n.source.toUpperCase()})\${n.isDraft ? ' [DRAFT]' : ''}\\nRefs: \${n.referencesCount}, Referenced by: \${n.referencedByCount}\${n.path ? '' : ' (external)'}\`,
      })));
      
      const validNodeIds = new Set(filteredNodes.map(n => n.id));
      const edges = new vis.DataSet(filteredEdges.filter(e => 
        validNodeIds.has(e.from) && validNodeIds.has(e.to)
      ).map(e => ({
        from: e.from,
        to: e.to,
        arrows: 'to',
        dashes: e.type === 'informative',
        color: { color: e.type === 'normative' ? '#666' : '#bbb' },
      })));
      
      const container = document.getElementById('graph');
      const data = { nodes, edges };
      const options = {
        layout: {
          improvedLayout: true,
        },
        physics: {
          solver: 'forceAtlas2Based',
          forceAtlas2Based: {
            gravitationalConstant: -50,
            springLength: 100,
          },
          stabilization: { iterations: 150 },
        },
        interaction: {
          hover: true,
          tooltipDelay: 100,
        },
      };
      
      const network = new vis.Network(container, data, options);
      
      network.on('click', function(params) {
        if (params.nodes.length > 0) {
          const nodeId = params.nodes[0];
          const node = graphData.nodes.find(n => n.id === nodeId);
          const incoming = graphData.edges.filter(e => e.to === nodeId);
          const outgoing = graphData.edges.filter(e => e.from === nodeId);
          
          const draftBadge = node.isDraft ? '<span style="background:#FF5722;color:white;padding:2px 6px;border-radius:3px;margin-left:8px;font-size:12px;">DRAFT</span>' : '';
          
          const url = getDocumentUrl(node);
          const linkHtml = url ? \`<a href="\${url}" target="_blank" style="color:#1976D2;text-decoration:none;">Open in browser</a>\` : '';
          
          // Make reference lists clickable too
          const makeRefLinks = (edges, direction) => {
            if (edges.length === 0) return 'none';
            return edges.map(e => {
              const refId = direction === 'out' ? e.to : e.from;
              const refNode = graphData.nodes.find(n => n.id === refId);
              if (refNode) {
                const refUrl = getDocumentUrl(refNode);
                if (refUrl) {
                  return \`<a href="\${refUrl}" target="_blank" style="color:#1976D2;text-decoration:none;">\${refId}</a>\`;
                }
              }
              return refId;
            }).join(', ');
          };
          
          document.getElementById('info').style.display = 'block';
          document.getElementById('info').innerHTML = \`
            <strong>\${nodeId}</strong>\${draftBadge} \${node.path ? '' : '(external reference)'}
            \${linkHtml ? '<br>' + linkHtml : ''}
            <br><br>
            <strong>References (\${outgoing.length}):</strong> \${makeRefLinks(outgoing, 'out')}
            <br><br>
            <strong>Referenced by (\${incoming.length}):</strong> \${makeRefLinks(incoming, 'in')}
          \`;
        }
      });
    }
    
    document.getElementById('showNormative').addEventListener('change', buildNetwork);
    document.getElementById('showInformative').addEventListener('change', buildNetwork);
    document.getElementById('showExternal').addEventListener('change', buildNetwork);
    document.getElementById('showDrafts').addEventListener('change', buildNetwork);
    document.getElementById('showUndownloaded').addEventListener('change', buildNetwork);
    
    buildNetwork();
  </script>
</body>
</html>`;
}

extractReferences().catch(console.error);
