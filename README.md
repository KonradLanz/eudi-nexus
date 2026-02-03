# EUDI Nexus

Interactive reference graph of ETSI ESI standards for the **European Digital Identity Wallet** ecosystem.

🔗 **Live Demo**: [cre8.github.io/eudi-nexus](https://cre8.github.io/eudi-nexus)

## Features

- **EUDI-focused**: Filters to ~100 specs relevant to the EUDI Wallet (default mode)
- **Interactive graph**: Visualize normative and informative references between specs
- **Multi-source**: Includes ETSI, IETF RFCs, ISO/IEC, ITU-T, W3C, and OIDF (OpenID4VP, OpenID4VCI, etc.)
- **Iterative crawling**: Automatically downloads and analyzes referenced external specs (OIDF, IETF RFCs)
- **Draft support**: Includes work-in-progress documents from ETSI docbox
- **Search & navigate**: Select box to find and focus on specific documents
- **Clickable links**: Navigate directly to spec sources

## Quick Start

```bash
# Install dependencies
npm install

# Run everything (scrape → download → build)
npm run scrape      # Scrape ETSI work program
npm run download    # Download PDFs (requires credentials)
npm run build       # Generate reference graph

# View locally
npm run serve       # Opens http://localhost:9999
```

## Configuration

Create a `.env` file with your ETSI portal credentials (needed for downloading specs):

```bash
ETSI_USERNAME=your_username
ETSI_PASSWORD=your_password
```

## Commands

| Command | Description |
| ------- | ----------- |
| `npm run scrape` | Scrape work items from ETSI portal → `esi_overview.json` |
| `npm run download` | Download all published PDFs (~150 specs) |
| `npm run download:oidf` | Download OIDF specs (OpenID4VP, OpenID4VCI, etc.) |
| `npm run build` | Extract references and build graph (EUDI focus + drafts) |
| `npm run build:full` | Iteratively crawl & download all referenced external specs |
| `npm run crawl` | Same as `build:full` - iterative reference crawler |
| `npm run serve` | Serve the visualization locally on port 9999 |

### Reference Extraction Options

| Command | Description |
| ------- | ----------- |
| `npm run references` | EUDI-focused specs only |
| `npm run references:all` | All ETSI ESI specs |
| `npm run references:drafts` | EUDI specs + draft documents |
| `npm run references:all:drafts` | Everything |

### Iterative Crawling

The `npm run crawl` command iteratively:

1. Extracts references from all downloaded specs (ETSI PDFs, OIDF HTML, IETF RFCs)
2. Identifies referenced but not-yet-downloaded external specs
3. Downloads missing OIDF specs and IETF RFCs
4. Repeats until no new specs are found (max 5 iterations)

This builds a comprehensive graph including transitive dependencies.

```bash
# Run with verbose output
npm run crawl:verbose

# Limit iterations
cd scripts && node crawl-references.js --max-iterations 3
```

## Output Files

All output goes to the `downloads/` directory:

| File | Description |
| ---- | ----------- |
| `index.html` | Interactive visualization (deployed to GitHub Pages) |
| `references.json` | Full reference data as JSON |
| `references.dot` | Graphviz DOT format |
| `references.mmd` | Mermaid diagram format |
| `esi_overview.json` | Scraped work items summary |
| `specs/` | Downloaded PDF/DOCX specifications |
| `specs/OIDF/` | Downloaded OIDF specifications (HTML) |
| `specs/IETF/` | Downloaded IETF RFCs (plain text) |

## Supported Sources

| Source | Format | Auto-Download | Notes |
| ------ | ------ | ------------- | ----- |
| ETSI | PDF/DOCX | Yes (with credentials) | ESI standards (EN, TS, TR) |
| OIDF | HTML | Yes | OpenID4VP, OpenID4VCI, SD-JWT, etc. |
| IETF | Text | Yes | RFCs from rfc-editor.org |
| ISO/IEC | - | No | Referenced only |
| ITU-T | - | No | Referenced only |
| W3C | - | No | Referenced only |

## EUDI-Relevant Specifications

The default mode focuses on specs relevant to the EUDI Wallet ecosystem:

- **Trust Services**: EN 319 xxx series
- **Electronic Signatures**: CAdES, XAdES, PAdES, JAdES
- **Wallet & Credentials**: TS 119 46x, 47x, 49x series
- **External Standards**: OpenID4VP, OpenID4VCI, HAIP, SD-JWT, key RFCs

Use `--all` flag to include all ETSI ESI specifications.

## CI/CD

The GitHub Actions workflow automatically:

1. Scrapes the ETSI work program
2. Downloads specifications
3. Extracts references and builds the graph
4. Deploys to GitHub Pages

Runs on:

- Push to `main` branch
- Weekly schedule (Mondays)
- Manual trigger

Required secrets: `ETSI_USERNAME`, `ETSI_PASSWORD`

## License

Apache-2.0 License
