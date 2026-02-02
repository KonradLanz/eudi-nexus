import fs from 'fs/promises';

async function generateMarkdown() {
  const data = JSON.parse(await fs.readFile('../downloads/esi_overview.json', 'utf-8'));
  
  let md = `# ESI Work Program Overview

> Generated: ${new Date(data.generatedAt).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })}

## Summary

| Metric | Count |
|--------|-------|
| Total Documents Scraped | ${data.statistics.totalScraped} |
| Unique ETSI Documents | ${data.statistics.uniqueDocuments} |
| Active Work Items | ${data.statistics.activeWorkItems} |
| Published Documents | ${data.statistics.publishedDocuments} |

### Active Work by Document Type

| Type | Count | Description |
|------|-------|-------------|
| EN | ${data.activeWorkByType.EN || 0} | European Standard |
| TS | ${data.activeWorkByType.TS || 0} | Technical Specification |
| TR | ${data.activeWorkByType.TR || 0} | Technical Report |
| ES | ${data.activeWorkByType.ES || 0} | ETSI Standard |
| Other | ${data.activeWorkByType.Other || 0} | Other documents |

---

## Active Work Items (In Development)

| ETSI Number | Title | Current Status | Current Date | Next Milestone | Due Date |
|-------------|-------|----------------|--------------|----------------|----------|
`;

  // Sort active items by next status date
  const activeItems = [...data.activeWorkItems].sort((a, b) => {
    const dateA = a.nextStatus?.date || a.currentStatus?.date || '9999';
    const dateB = b.nextStatus?.date || b.currentStatus?.date || '9999';
    return dateA.localeCompare(dateB);
  });

  for (const item of activeItems) {
    const etsiNum = item.etsiNumber || 'N/A';
    const etsiLink = item.detailUrl ? `[${escapeMarkdown(etsiNum)}](${item.detailUrl})` : escapeMarkdown(etsiNum);
    const title = truncate(item.title?.replace(/Electronic Signatures and Infrastructures \(ESI\);?/gi, '').trim() || item.subtitle || 'N/A', 60);
    const currentStatus = item.currentStatus?.status || 'N/A';
    const currentDate = item.currentStatus?.date || 'N/A';
    const nextMilestone = item.nextStatus?.status || '-';
    const dueDate = item.nextStatus?.date || '-';
    
    md += `| ${etsiLink} | ${escapeMarkdown(title)} | ${escapeMarkdown(currentStatus)} | ${currentDate} | ${escapeMarkdown(nextMilestone)} | ${dueDate} |\n`;
  }

  md += `
---

## Active Work Items by Category

`;

  // Group by category based on ETSI number series
  const categories = categorizeItems(activeItems);
  
  for (const [category, items] of Object.entries(categories)) {
    if (items.length === 0) continue;
    
    md += `### ${category}

| ETSI Number | Title | Status | Next Milestone | Due |
|-------------|-------|--------|----------------|-----|
`;
    
    for (const item of items) {
      const etsiNum = item.etsiNumber || 'N/A';
      const etsiLink = item.detailUrl ? `[${escapeMarkdown(etsiNum)}](${item.detailUrl})` : escapeMarkdown(etsiNum);
      const title = truncate(item.title?.replace(/Electronic Signatures and Infrastructures \(ESI\);?/gi, '').trim() || item.subtitle || 'N/A', 50);
      const status = item.currentStatus?.status || 'N/A';
      const next = item.nextStatus?.status || '-';
      const due = item.nextStatus?.date || '-';
      
      md += `| ${etsiLink} | ${escapeMarkdown(title)} | ${escapeMarkdown(status)} | ${escapeMarkdown(next)} | ${due} |\n`;
    }
    
    md += '\n';
  }

  md += `---

## Published Documents

### By Document Type

`;

  // Group published by type
  const publishedByType = {};
  for (const item of data.publishedDocuments) {
    const match = item.etsiNumber?.match(/^(EN|TS|TR|ES|EG)/i);
    const type = match ? match[1].toUpperCase() : 'Other';
    if (!publishedByType[type]) publishedByType[type] = [];
    publishedByType[type].push(item);
  }

  for (const [type, items] of Object.entries(publishedByType).sort()) {
    md += `<details>
<summary><strong>${type} Documents (${items.length})</strong></summary>

| ETSI Number | Title | Publication Date |
|-------------|-------|------------------|
`;
    
    // Sort by publication date descending
    items.sort((a, b) => {
      const dateA = a.currentStatus?.date || '0000';
      const dateB = b.currentStatus?.date || '0000';
      return dateB.localeCompare(dateA);
    });
    
    for (const item of items) {
      const etsiNum = item.etsiNumber || 'N/A';
      const etsiLink = item.detailUrl ? `[${escapeMarkdown(etsiNum)}](${item.detailUrl})` : escapeMarkdown(etsiNum);
      const title = truncate(item.title?.replace(/Electronic Signatures and Infrastructures \(ESI\);?/gi, '').trim() || item.subtitle || 'N/A', 70);
      const pubDate = item.currentStatus?.date || 'N/A';
      
      md += `| ${etsiLink} | ${escapeMarkdown(title)} | ${pubDate} |\n`;
    }
    
    md += `
</details>

`;
  }

  md += `---

## Timeline View (Upcoming Milestones)

`;

  // Get items with upcoming milestones in next 6 months
  const now = new Date();
  const sixMonthsLater = new Date(now.getTime() + 180 * 24 * 60 * 60 * 1000);
  
  const upcoming = activeItems
    .filter(item => {
      const date = item.nextStatus?.date;
      if (!date) return false;
      const d = new Date(date);
      return d >= now && d <= sixMonthsLater;
    })
    .sort((a, b) => a.nextStatus.date.localeCompare(b.nextStatus.date));

  if (upcoming.length > 0) {
    // Group by month
    const byMonth = {};
    for (const item of upcoming) {
      const date = new Date(item.nextStatus.date);
      const monthKey = date.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
      if (!byMonth[monthKey]) byMonth[monthKey] = [];
      byMonth[monthKey].push(item);
    }

    for (const [month, items] of Object.entries(byMonth)) {
      md += `### ${month}

| Date | ETSI Number | Milestone | Title |
|------|-------------|-----------|-------|
`;
      
      for (const item of items) {
        const etsiLink = item.detailUrl ? `[${escapeMarkdown(item.etsiNumber)}](${item.detailUrl})` : escapeMarkdown(item.etsiNumber);
        md += `| ${item.nextStatus.date} | ${etsiLink} | ${escapeMarkdown(item.nextStatus.status)} | ${escapeMarkdown(truncate(item.title?.replace(/Electronic Signatures and Infrastructures \(ESI\);?/gi, '').trim() || '', 40))} |\n`;
      }
      
      md += '\n';
    }
  } else {
    md += '*No upcoming milestones in the next 6 months.*\n';
  }

  // Write to file
  await fs.writeFile('../downloads/esi_overview.md', md);
  console.log('✅ Generated downloads/esi_overview.md');
  
  return md;
}

function categorizeItems(items) {
  const categories = {
    '🪪 EUDI Wallet & Electronic Attestation of Attributes': [],
    '📜 Certificate Profiles & Policies': [],
    '✍️ Signature Formats (AdES)': [],
    '🔐 Cryptographic & Validation': [],
    '📋 Trust Lists & Conformity Assessment': [],
    '🔧 Other': []
  };

  for (const item of items) {
    const num = item.etsiNumber || '';
    const title = (item.title + ' ' + item.subtitle).toLowerCase();
    
    if (num.includes('472') || num.includes('475') || num.includes('476') || num.includes('479') || 
        title.includes('wallet') || title.includes('eaa') || title.includes('eudi') || title.includes('attestation')) {
      categories['🪪 EUDI Wallet & Electronic Attestation of Attributes'].push(item);
    } else if (num.includes('411') || num.includes('412') || title.includes('certificate') || title.includes('policy')) {
      categories['📜 Certificate Profiles & Policies'].push(item);
    } else if (num.includes('122') || num.includes('132') || num.includes('142') || num.includes('152') || 
               num.includes('162') || num.includes('172') || num.includes('182') ||
               title.includes('ades') || title.includes('signature') || title.includes('pades') || 
               title.includes('xades') || title.includes('cades') || title.includes('jades') || title.includes('asic')) {
      categories['✍️ Signature Formats (AdES)'].push(item);
    } else if (num.includes('102') || num.includes('312') || num.includes('322') || num.includes('432') || num.includes('442') ||
               title.includes('cryptograph') || title.includes('validation')) {
      categories['🔐 Cryptographic & Validation'].push(item);
    } else if (num.includes('401') || num.includes('403') || num.includes('6') || 
               title.includes('trust') || title.includes('conformity') || title.includes('conformance')) {
      categories['📋 Trust Lists & Conformity Assessment'].push(item);
    } else {
      categories['🔧 Other'].push(item);
    }
  }

  return categories;
}

function truncate(str, maxLen) {
  if (!str) return '';
  str = str.replace(/\s+/g, ' ').trim();
  if (str.length <= maxLen) return str;
  return str.substring(0, maxLen - 3) + '...';
}

function escapeMarkdown(str) {
  if (!str) return '';
  return str.replace(/\|/g, '\\|').replace(/\n/g, ' ');
}

generateMarkdown().catch(console.error);
