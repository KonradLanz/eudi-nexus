# ETSI ESI Work Program Tools

Tools to scrape, analyze, and download ETSI ESI (Electronic Signatures and Infrastructures) specifications.

## Setup

1. Install dependencies:

   ```bash
   npm install
   ```

2. Configure credentials:

   ```bash
   cp .env.example .env
   # Edit .env with your ETSI portal credentials
   ```

## Usage

### Quick Start (run all steps)

```bash
npm run all        # Scrape → Analyze → Generate Markdown
npm run download   # Download all published PDFs (143 specs, ~53MB)
```

### Individual Commands

| Command | Description |
| -------- | ------------- |
| `npm run scrape` | Scrape work items from ETSI portal |
| `npm run analyze` | Process scraped data → `esi_overview.json` |
| `npm run markdown` | Generate markdown tables → `esi_overview.md` |
| `npm run download` | Download all published PDFs |
| `npm run download:test` | Test download with 5 files |

### Download Options

```bash
# Download with limit
cd scripts && node download-specs.js --limit=10

# Download only published specs (skip active work items)
cd scripts && node download-specs.js --published-only

# Combine options
cd scripts && node download-specs.js --limit=20 --published-only
```

## Project Structure

```string
etsi/
├── src/
│   ├── etsi-client.js          # ETSI API client with authentication
│   └── work-program-scraper.js # Scrapes Work Program page
├── scripts/
│   ├── analyze.js              # Creates esi_overview.json
│   ├── generate-markdown.js    # Creates esi_overview.md
│   └── download-specs.js       # Downloads PDF specifications
├── downloads/
│   ├── work_items.json         # Raw scraped data
│   ├── esi_overview.json       # Processed summary (44 active, 225 published)
│   ├── esi_overview.md         # Markdown tables with links
│   └── specs/                  # Downloaded PDFs organized by type
│       ├── EN/                 # European Norms
│       ├── TS/                 # Technical Specifications
│       ├── TR/                 # Technical Reports
│       └── Other/              # Special Reports, etc.
├── .env                        # Your credentials (gitignored)
├── .env.example                # Example configuration
└── package.json
```

## Output Data

### esi_overview.json

Contains:

- `activeWorkItems`: 44 specifications currently being worked on
- `publishedDocuments`: 225 published specifications
- `statistics`: Summary counts by document type
- Each item includes: ETSI number, title, status, version, dates, links

### esi_overview.md

Markdown tables with:

- Summary statistics
- Active work items by type (EN, TS, TR)
- Published documents by type
- Clickable links to ETSI portal detail pages

## Configuration

Edit `.env`:

```env
ETSI_USERNAME=your-username
ETSI_PASSWORD=your-password
```

## Requirements

- Node.js 18+
- ETSI portal account with ESI committee access
