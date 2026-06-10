#!/bin/sh
# test/debug-ai-raw-response.sh
#
# Sendet einen einzelnen Titel direkt an LM Studio und zeigt die Rohantwort
# des Modells VOR dem cleanup/filter in local-ai.js.
#
# Zweck: Diagnose wenn "AI returned no valid short title" erscheint.
# Zeigt ob das Modell antwortet und was es zurueckgibt.
#
# Usage:
#   sh test/debug-ai-raw-response.sh
#   sh test/debug-ai-raw-response.sh "My custom full title here"

set -e
cd "$(dirname "$0")/.."

TITLE="${1:-Conformity Assessment for Signature Creation and Validation (Applications and Procedures)}"

BASE_URL=$(node -e "
import('dotenv').then(d => d.config()).catch(() => {});
process.stdout.write(process.env.LMSTUDIO_BASE_URL || 'http://localhost:1234');
" 2>/dev/null || echo 'http://localhost:1234')

MODEL=$(node -e "
import('./src/local-ai.js').then(async m => {
  await m.isAvailable();
  process.stdout.write(await m.bestModel() || '');
}).catch(() => process.stdout.write(''));
" 2>/dev/null)

echo "=== AI Raw Response Diagnostic ==="
echo ""
echo "  Base URL : $BASE_URL"
echo "  Model    : $MODEL"
echo "  Title    : $TITLE"
echo ""

if [ -z "$MODEL" ]; then
  echo "ERROR: No model detected — is LM Studio running?"
  exit 1
fi

echo "--- Raw API response ---"
node -e "
const base  = '$BASE_URL';
const model = '$MODEL';
const title = $(node -e "process.stdout.write(JSON.stringify('$TITLE'))");

const body = {
  model,
  messages: [
    {
      role: 'system',
      content: 'You are a technical standards editor. Given the full title of an ETSI standard, output a short title of AT MOST FOUR WORDS that captures the core topic. Respond with ONLY the short title — no punctuation, no explanation, no quotes.'
    },
    {
      role: 'user',
      content: 'Full title: \"' + title + '\"\nShort title (max 4 words):'
    }
  ],
  temperature: 0.15,
  max_tokens: 20,
  stream: false
};

fetch(base + '/v1/chat/completions', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body)
})
.then(r => r.json())
.then(d => {
  const content = d.choices?.[0]?.message?.content ?? '';
  console.log('Raw content  :', JSON.stringify(content));
  console.log('Length       :', content.length);
  console.log('Word count   :', content.trim().split(/\\s+/).filter(Boolean).length);
  const firstLine = content.split('\\n')[0];
  const cleaned = firstLine.replace(/^[#*\\-•>\\s\"']+/, '').replace(/[\"'\\s]+$/, '').trim();
  const words = cleaned.split(/\\s+/).filter(Boolean);
  console.log('After cleanup:', JSON.stringify(cleaned));
  console.log('Words        :', words.length, '->', words.join(' '));
  console.log('Would pass   :', words.length > 0 && words.length <= 8 ? 'YES' : 'NO (filtered out)');
})
.catch(e => console.error('Request failed:', e.message));
" 2>/dev/null

echo ""
echo "--- suggestShortTitle() result (via local-ai.js) ---"
node -e "
import('./src/local-ai.js').then(async m => {
  const result = await m.suggestShortTitle(
    '$TITLE',
    'debug test'
  );
  console.log('Result:', JSON.stringify(result));
  console.log(result ? '✓ PASS — would be stored as shortTitle' : '✗ FAIL — returns null (filtered out)');
}).catch(e => console.error('Error:', e.message));
" 2>/dev/null
