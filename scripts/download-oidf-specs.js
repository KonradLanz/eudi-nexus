import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DOWNLOAD_PATH = path.join(__dirname, '../downloads/specs/OIDF');

// OIDF specifications relevant to EUDI Wallet ecosystem
// These are the core OpenID specs that ETSI documents reference
const OIDF_SPECS = [
  {
    id: 'OpenID4VP',
    name: 'OpenID for Verifiable Presentations',
    url: 'https://openid.net/specs/openid-4-verifiable-presentations-1_0.html',
    version: '1.0',
  },
  {
    id: 'OpenID4VCI',
    name: 'OpenID for Verifiable Credential Issuance',
    url: 'https://openid.net/specs/openid-4-verifiable-credential-issuance-1_0.html',
    version: '1.0',
  },
  {
    id: 'OpenID4VC-HAIP',
    name: 'OpenID4VC High Assurance Interoperability Profile',
    url: 'https://openid.net/specs/openid4vc-high-assurance-interoperability-profile-1_0.html',
    version: '1.0',
  },
  {
    id: 'OpenID Connect Core',
    name: 'OpenID Connect Core',
    url: 'https://openid.net/specs/openid-connect-core-1_0.html',
    version: '1.0',
  },
  {
    id: 'OpenID Connect Discovery',
    name: 'OpenID Connect Discovery',
    url: 'https://openid.net/specs/openid-connect-discovery-1_0.html',
    version: '1.0',
  },
  {
    id: 'OpenID Connect Dynamic Client Registration',
    name: 'OpenID Connect Dynamic Client Registration',
    url: 'https://openid.net/specs/openid-connect-registration-1_0.html',
    version: '1.0',
  },
  {
    id: 'OpenID Federation',
    name: 'OpenID Federation',
    url: 'https://openid.net/specs/openid-federation-1_0.html',
    version: '1.0',
  },
];

// Note: The following specs are from IETF, not OIDF:
// - SD-JWT (draft-ietf-oauth-selective-disclosure-jwt) - IETF draft
// - SD-JWT VC (draft-ietf-oauth-sd-jwt-vc) - IETF draft
// - OAuth 2.0 DPoP (RFC 9449) - already in IETF folder
// - OAuth 2.0 PAR (RFC 9126) - already in IETF folder
// - OAuth 2.0 RAR (RFC 9396) - already in IETF folder

async function downloadOidfSpecs() {
  console.log('📥 OIDF Specification Downloader');
  console.log('================================\n');
  console.log(`Downloading ${OIDF_SPECS.length} OpenID Foundation specifications...\n`);

  // Create download directory
  await fs.mkdir(DOWNLOAD_PATH, { recursive: true });

  const results = {
    success: [],
    failed: [],
  };

  for (const spec of OIDF_SPECS) {
    const filename = `${spec.id.replace(/\s+/g, '_')}.html`;
    const filepath = path.join(DOWNLOAD_PATH, filename);

    process.stdout.write(`📄 ${spec.id}...`);

    try {
      const response = await fetch(spec.url, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; EUDI-Nexus/1.0; +https://github.com/cre8/eudi-nexus)',
          'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        },
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const html = await response.text();
      
      // Save HTML content
      await fs.writeFile(filepath, html, 'utf-8');

      // Also save metadata
      const metadata = {
        ...spec,
        downloadedAt: new Date().toISOString(),
        filepath: filepath,
        size: html.length,
      };

      results.success.push(metadata);
      console.log(` ✅ (${(html.length / 1024).toFixed(1)} KB)`);

      // Small delay to be nice to servers
      await new Promise(resolve => setTimeout(resolve, 500));

    } catch (error) {
      console.log(` ❌ ${error.message}`);
      results.failed.push({ ...spec, error: error.message });
    }
  }

  // Save summary
  const summaryPath = path.join(DOWNLOAD_PATH, 'oidf_specs_summary.json');
  await fs.writeFile(summaryPath, JSON.stringify({
    downloadedAt: new Date().toISOString(),
    totalSpecs: OIDF_SPECS.length,
    successCount: results.success.length,
    failedCount: results.failed.length,
    specs: results.success,
    failed: results.failed,
  }, null, 2));

  console.log('\n================================');
  console.log(`✅ Downloaded: ${results.success.length}/${OIDF_SPECS.length}`);
  if (results.failed.length > 0) {
    console.log(`❌ Failed: ${results.failed.length}`);
    results.failed.forEach(f => console.log(`   - ${f.id}: ${f.error}`));
  }
  console.log(`\n📁 Saved to: ${DOWNLOAD_PATH}`);
  console.log(`📋 Summary: ${summaryPath}`);
}

downloadOidfSpecs().catch(console.error);
