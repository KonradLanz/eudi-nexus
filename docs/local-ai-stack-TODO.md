# Local AI Stack — TODO & Roadmap

> **Ziel:** Lokales Compute (LM Studio / Ollama) als Entwicklungshelfer einsetzen,
> damit repetitive, strukturierte Aufgaben (Auditieren, Klassifizieren, Zusammenfassen)
> automatisch vorab erledigt werden — ohne Cloud-API-Kosten und ohne manuelle
> Vorarbeit.

---

## Konzept

```
  scripts/
    local-ai-helper.js   ← CLI-Einstiegspunkt für dev-time tasks
  src/
    local-ai.js          ← Provider-Abstraktion (LM Studio | Ollama) ✅
    ai-tasks/
      project-audit.js   ← POC: Ordnerstruktur + Dateiinhalte prüfen ✅
      classify-spec.js   ← TODO: ETSI-Dokument nach Thema klassifizieren
      summarise-spec.js  ← TODO: Abstract aus PDF/HTML extrahieren & kürzen
      suggest-tags.js    ← TODO: Keywords aus Titeln ableiten
```

Jeder Task lädt den besten verfügbaren Provider via `local-ai.js` und gibt
strukturierten JSON-Output zurück (damit spätere Scripts darauf aufbauen können).

---

## Status

| Task | Datei | Status |
|---|---|---|
| Provider-Erkennung (LMStudio / Ollama) | `src/local-ai.js` | ✅ fertig |
| Short-title generation | `src/local-ai.js` | ✅ fertig |
| **Project Audit POC** | `src/ai-tasks/project-audit.js` | ✅ POC fertig |
| Spec-Klassifikation | `src/ai-tasks/classify-spec.js` | 🔲 TODO |
| Abstract-Summaries | `src/ai-tasks/summarise-spec.js` | 🔲 TODO |
| Keyword-Tagging | `src/ai-tasks/suggest-tags.js` | 🔲 TODO |
| `enrich-titles.js` integration | `scripts/enrich-titles.js` | ✅ nutzt local-ai.js |
| Batch-Queue mit Retry | `src/ai-tasks/queue.js` | 🔲 TODO |
| Token-Budget-Guard (kein overflow) | `src/local-ai.js` | 🔲 TODO |
| Modell-Präferenz-Konfiguration in `.env` | `src/local-ai.js` | 🔲 TODO |

---

## TODO — konkrete nächste Schritte

### 1. Modell-Präferenz via `.env`
```
LOCAL_AI_PREFERRED_MODEL=llama3.2
LOCAL_AI_MAX_TOKENS=512
```
Damit kann der Nutzer steuern, welches Modell für welchen Task verwendet wird
(schnelles Modell für Klassifikation, großes für Summarisation).

### 2. Token-Budget-Guard
Vor jedem Prompt grob schätzen wie viele Token er braucht. Wenn er das
konfigurierte Budget übersteigt → truncaten + warnen, nicht crashen.

### 3. `classify-spec.js`
Eingabe: `etsiNumber`, `fullTitle`  
Ausgabe: `{ category: 'eidas' | 'wallet' | 'crypto' | 'trust' | 'other', confidence: 0..1 }`  
Dient als Filter für die UI-Ansicht und die Download-Priorisierung.

### 4. `summarise-spec.js`
Eingabe: Erste ~1500 Zeichen aus einem Workitem-HTML  
Ausgabe: 2-3 Satz Abstract auf Englisch  
Speichern als `_titles/{stem}.summary.json`

### 5. Batch-Queue
Mehrere AI-Tasks nacheinander mit konfigurierbarem Rate-Limit (delay zwischen
Requests), damit LM Studio / Ollama nicht überlastet wird.

### 6. `local-ai-helper.js` ausbauen
Weitere Sub-Commands:
- `audit`      — Ordnerstruktur + Datei-POC (✅ bereits implementiert)
- `classify`   — alle Sidecars klassifizieren
- `summarise`  — alle Sidecars zusammenfassen
- `tags`       — Keyword-Tags generieren
- `report`     — kombinierter HTML-Report aller AI-Outputs

---

## Referenzen

- [LM Studio Docs](https://lmstudio.ai/docs/api)
- [Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md)
- [`src/local-ai.js`](../src/local-ai.js) — Provider-Abstraktion
- [`scripts/local-ai-helper.js`](../scripts/local-ai-helper.js) — CLI
