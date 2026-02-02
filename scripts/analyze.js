import fs from 'fs/promises';

async function analyzeWorkItems() {
  // Read the work items
  const data = JSON.parse(await fs.readFile('../downloads/work_items.json', 'utf-8'));
  
  console.log(`\n📊 ESI Work Program Analysis`);
  console.log(`════════════════════════════\n`);
  console.log(`Total work items scraped: ${data.length}\n`);
  
  // Deduplicate by ETSI number (keep latest version based on workItemId)
  const byEtsiNumber = new Map();
  for (const item of data) {
    const key = item.etsiNumber;
    if (!key) continue;
    
    const existing = byEtsiNumber.get(key);
    if (!existing || parseInt(item.workItemId) > parseInt(existing.workItemId)) {
      byEtsiNumber.set(key, item);
    }
  }
  
  console.log(`Unique ETSI document numbers: ${byEtsiNumber.size}\n`);
  
  // Separate active work (Drafting Stage) from published
  const activeWork = [];
  const published = [];
  
  for (const item of byEtsiNumber.values()) {
    // Clean up stage
    item.cleanStage = item.stage?.replace(/[\n\t]+/g, ' ').trim() || 'Unknown';
    
    if (item.cleanStage.includes('Drafting') || item.cleanStage.includes('approval')) {
      activeWork.push(item);
    } else {
      published.push(item);
    }
  }
  
  console.log(`📝 Active Work Items (in development): ${activeWork.length}`);
  console.log(`✅ Published Documents: ${published.length}\n`);
  
  // Sort active work by next status date
  activeWork.sort((a, b) => {
    const dateA = a.nextStatus?.date || a.currentStatus?.date || '9999';
    const dateB = b.nextStatus?.date || b.currentStatus?.date || '9999';
    return dateA.localeCompare(dateB);
  });
  
  // Group by document type
  const activeByType = groupByType(activeWork);
  const publishedByType = groupByType(published);
  
  // Create summary JSON
  const summary = {
    generatedAt: new Date().toISOString(),
    statistics: {
      totalScraped: data.length,
      uniqueDocuments: byEtsiNumber.size,
      activeWorkItems: activeWork.length,
      publishedDocuments: published.length
    },
    activeWorkByType: Object.fromEntries(
      Object.entries(activeByType).map(([k, v]) => [k, v.length])
    ),
    publishedByType: Object.fromEntries(
      Object.entries(publishedByType).map(([k, v]) => [k, v.length])
    ),
    activeWorkItems: activeWork.map(formatItem),
    publishedDocuments: published.map(formatItem)
  };
  
  // Save the summary
  await fs.writeFile('../downloads/esi_overview.json', JSON.stringify(summary, null, 2));
  console.log(`💾 Saved complete overview to downloads/esi_overview.json\n`);
  
  // Print active work items
  console.log(`\n${'═'.repeat(60)}`);
  console.log(`📋 ACTIVE WORK ITEMS (Currently in Development)`);
  console.log(`${'═'.repeat(60)}\n`);
  
  for (const [type, items] of Object.entries(activeByType)) {
    console.log(`\n${type} Documents (${items.length}):`);
    console.log(`${'─'.repeat(40)}`);
    
    for (const item of items) {
      console.log(`\n  📄 ${item.etsiNumber}`);
      console.log(`     Reference: ${item.reference}`);
      console.log(`     Title: ${item.title}`);
      if (item.subtitle) {
        console.log(`     Scope: ${item.subtitle}`);
      }
      console.log(`     Current: ${item.currentStatus?.status || 'N/A'} (${item.currentStatus?.date || 'N/A'})`);
      if (item.nextStatus) {
        console.log(`     Next: ${item.nextStatus.status} (${item.nextStatus.date})`);
      }
    }
  }
  
  // Print a simpler table format for active items
  console.log(`\n\n${'═'.repeat(80)}`);
  console.log(`📊 ACTIVE WORK ITEMS - TIMELINE VIEW`);
  console.log(`${'═'.repeat(80)}\n`);
  
  console.log('ETSI Number'.padEnd(20) + 'Current Status'.padEnd(25) + 'Next Milestone'.padEnd(30) + 'Due Date');
  console.log('─'.repeat(85));
  
  for (const item of activeWork) {
    const etsi = (item.etsiNumber || 'Unknown').substring(0, 19).padEnd(20);
    const current = (item.currentStatus?.status || 'N/A').substring(0, 24).padEnd(25);
    const next = (item.nextStatus?.status || 'N/A').substring(0, 29).padEnd(30);
    const date = item.nextStatus?.date || 'N/A';
    console.log(`${etsi}${current}${next}${date}`);
  }
  
  return summary;
}

function groupByType(items) {
  const groups = {};
  
  for (const item of items) {
    const match = item.etsiNumber?.match(/^(EN|TS|TR|ES|EG)/i);
    const type = match ? match[1].toUpperCase() : 'Other';
    
    if (!groups[type]) groups[type] = [];
    groups[type].push(item);
  }
  
  return groups;
}

function formatItem(item) {
  return {
    etsiNumber: item.etsiNumber,
    reference: item.reference,
    title: item.title,
    subtitle: item.subtitle,
    stage: item.cleanStage,
    currentStatus: item.currentStatus,
    nextStatus: item.nextStatus,
    detailUrl: item.detailUrl,
    scheduleUrl: item.scheduleUrl
  };
}

analyzeWorkItems().catch(console.error);
