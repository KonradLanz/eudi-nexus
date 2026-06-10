#!/bin/sh
# test/debug-ai-prompt-variants.sh
#
# Testet verschiedene Prompt-Formulierungen gegen das aktive Modell.
# Hilft herauszufinden welche Prompt-Variante das Modell am besten befolgt
# ("Respond with ONLY" vs andere Formulierungen).
#
# Usage:
#   sh test/debug-ai-prompt-variants.sh

set -e
cd "$(dirname "$0")/.."

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

if [ -z "$MODEL" ]; then
  echo "ERROR: No model detected — is LM Studio running?"
  exit 1
fi

TITLE="Electronic Signatures and Infrastructures (ESI); Conformity Assessment for Signature Creation and Validation"

echo "=== Prompt Variant Test ==="
echo "  Model : $MODEL"
echo "  Title : $TITLE"
echo ""

call_model() {
  SYSTEM="$1"
  USER="$2"
  LABEL="$3"
  echo "--- Variant: $LABEL ---"
  node -e "
  fetch('$BASE_URL/v1/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: '$MODEL',
      messages: [
        { role: 'system', content: $(node -e "process.stdout.write(JSON.stringify('$SYSTEM'))") },
        { role: 'user',   content: $(node -e "process.stdout.write(JSON.stringify('$USER'))") }
      ],
      temperature: 0.15,
      max_tokens: 20,
      stream: false
    })
  }).then(r => r.json()).then(d => {
    const c = d.choices?.[0]?.message?.content ?? '';
    console.log('  Response:', JSON.stringify(c));
  }).catch(e => console.log('  Error:', e.message));
  " 2>/dev/null
  echo ""
}

# Variant A: current prompt (verbose system)
call_model \
  "You are a technical standards editor specialising in ETSI and eIDAS standards. Your task: given the full title of an ETSI standard, output a short title of AT MOST FOUR WORDS that captures the document's core topic. Rules: Maximum 4 words. Use official abbreviations. Never include the ETSI number. Respond with ONLY the short title — no punctuation, no explanation, no quotes. Do not start with A, An, The." \
  "Full title: \"$TITLE\"\nShort title (max 4 words):" \
  "A: Current (verbose system prompt)"

# Variant B: minimal system
call_model \
  "Output only a short title of max 4 words. No other text." \
  "Full title: \"$TITLE\"\nShort title:" \
  "B: Minimal system prompt"

# Variant C: instruction in user turn only
call_model \
  "" \
  "Give a short title (max 4 words) for this ETSI standard. Reply with ONLY the short title, nothing else.\n\"$TITLE\"" \
  "C: Instruction in user turn only"

# Variant D: few-shot
call_model \
  "You output short titles (max 4 words) for ETSI standards. Only the title, nothing else." \
  "Full: \"Electronic Signatures and Infrastructures (ESI); Certificate Profiles\"\nShort: Certificate Profiles ESI\n\nFull: \"Trust Service Provider (TSP) — Conformity Assessment\"\nShort: TSP Conformity Assessment\n\nFull: \"$TITLE\"\nShort:" \
  "D: Few-shot examples"

echo "=== Done — compare responses to find best prompt variant ==="
