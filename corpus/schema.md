# EUDI Nexus — Corpus Datenmodell

> **Knuth-Prinzip:** Die meiste Intelligenz steckt im Datenmodell.  
> Dieses Schema ist die verbindliche Grundlage bevor Scripts geschrieben werden.

---

## 1. Grundidee: Zwei Pole, eine Brücke

Das Dokumentuniversum hat zwei Pole:

| Pol | Quelle | Rolle |
|-----|--------|-------|
| **Standards** | ETSI / IETF / ISO — PDFs der Normen | Das normative Fundament |
| **Contributions** | ETSI Portal / Docbox — Arbeitsdokumente | Die Arbeitsebene, Entwürfe, Diskussionen |

Jede Verlinkung, jeder Diff, jede KI-Frage bewegt sich zwischen diesen Polen.  
Der `shortname` + `abbr` ist die Brücke — er überlebt Versionswechsel und macht Nummern menschenlesbar.

---

## 2. Identifier-System — vier Ebenen

```
id        "EN_319_401_v2.2.1"          ← maschinenlesbar, eindeutig, versioniert
shortname "TrustServicePolicy-Baseline" ← KI-generiert, menschenlesbar, stabil über Versionen
abbr      "TSP-Baseline"                ← kanonische Klammer: EN 319 401 (TSP-Baseline)
layer     "trust-services"              ← Schicht im Stack (siehe §4)
```

**Verwendungsregel im Text:**
- Erste Nennung: `EN 319 401 (TSP-Baseline)`
- Folgenennungen: nur `TSP-Baseline`
- Mouseover (später): zeigt shortname + layer + offene Contributions + Eselsbrücke

---

## 3. Serien-Anatomie der EN 319 xxx Familie

```
{prefix} {series} {group}{sequence}
   EN       319      4       01
```

| Gruppe `xxx` | Bedeutung | Eselsbrücke | Kanonische Abkürzung |
|---|---|---|---|
| `319` | ETSI ESI — die gesamte Normenfamilie | "drei-neunzehn = Signatur-Welt" | ESI |
| `1xx` | Signaturformate | "Wie sieht eine Unterschrift aus?" | SigFormat |
| `2xx` | Signaturerstellung und -validierung | "Wie macht und prüft man sie?" | SigProc |
| `3xx` | Zeitstempel (TSA) | "Wann war es?" | TSA |
| `4xx` | Vertrauensdiensteanbieter (TSP) | "Wer darf es?" | TSP |
| `5xx` | Preservation / Langzeitarchivierung | "Wie bleibt es gültig?" | LTA |
| `6xx` | Trusted Lists / Trust Status | "Wer kennt wen?" | TrustList |
| `46x/47x/49x` | Wallet, Credentials, Attestations | "Das neue Wallet-Universum" | WalletLayer |

### Innerhalb jeder Serie: die letzte Stelle

| Suffix | Rolle | Eselsbrücke |
|---|---|---|
| `x01` | Policy / Requirements (allgemein) | "die Spielregeln" |
| `x11` | Profile für Zertifikate | "wie der Ausweis aussieht" |
| `x21` | Profile für Signaturen/Dienste | "wie die Unterschrift aussieht" |
| `x31` | Konformitätsprüfung / Conformance Testing | "der TÜV" |
| `x41` | Vertrauenslisten-Format | "das Verzeichnis" |
| `x51` | Zusatzprofile / Extensions | "die Sonderfälle" |

---

## 4. Stack-Schichten (layer)

```
foundation        X.509 (PKIX) · RFC 5280 (PKIX-Cert) · RFC 6960 (OCSP) · ASN.1
sig-formats       CAdES (CMS-Sig) · XAdES (XML-Sig) · PAdES (PDF-Sig) · JAdES (JSON-Sig)
sig-processing    EN 319 1xx/2xx — Erstellung und Validierung
timestamping      EN 319 3xx — TSA (Zeitstempel-Dienste)
trust-services    EN 319 4xx — TSP-Baseline, Zertifikatsprofile, QES/QSeal
preservation      EN 319 5xx — LTA (Langzeitarchivierung)
trust-lists       EN 319 6xx — TrustList-Format, EU-TrustList, TL-Profile
legal             eIDAS 2.0 (EU 2024/1183) · Implementing Acts (IA)
wallet-layer      TS 119 46x/47x/49x — WalletTrust, QEAA, RemoteID
credentials       SD-JWT (Selective Disclosure) · OID4VCI (Issuance) · OID4VP (Presentation)
proximity         mDoc / ISO 18013-5 (Proximity-Ausweis)
zero-knowledge    ZKP-Ansätze (in Entwicklung)
```

---

## 5. Spec-Eintrag (JSON-Schema)

```jsonc
{
  // --- Identifikation ---
  "id": "EN_319_401_v2.2.1",           // maschinenlesbar, mit Version
  "family": "EN_319_401",              // versionsunabhängig, für Diff-Ketten
  "version": "2.2.1",
  "status": "published",               // published | draft | withdrawn | superseded

  // --- Menschenlesbar ---
  "shortname": "TrustServicePolicy-Baseline",
  "abbr": "TSP-Baseline",
  "title_official": "Electronic Signatures and Infrastructures (ESI); General Policy Requirements for Trust Service Providers",
  "layer": "trust-services",
  "series_group": "4xx",
  "series_role": "policy",             // policy | cert-profile | sig-profile | conformance | tl-format

  // --- Eselsbrücke (für verzögertes Mouseover, Stufe 2) ---
  "mnemonic": "Wer darf es? — die Spielregeln für alle Vertrauensdiensteanbieter",

  // --- Anker ---
  "source_url": "https://www.etsi.org/deliver/etsi_en/319400_319499/319401/02.02.01_60/en_319401v020201p.pdf",
  "pdf_local": "downloads/specs/EN_319_401_v2.2.1.pdf",

  // --- Versionsgeschichte ---
  "predecessors": ["EN_319_401_v2.1.1", "EN_319_401_v1.1.1"],
  "superseded_by": null,

  // --- Querverweise ---
  "normative_refs": ["RFC_5280", "EN_319_411-1_v1.3.1", "EN_319_421_v1.1.1"],
  "informative_refs": ["eIDAS_2024_1183"],

  // --- Contributions-Pol ---
  "contributions": ["ESI(26)000369", "ESI(25)001234"],  // Contributions die diese Norm betreffen

  // --- Namespace für Glossar-Terme die hier definiert werden ---
  "defines_terms": ["etsi.esi/TSP", "etsi.esi/QTSP", "etsi.esi/QES"],

  // --- Seiten-Index (wird durch pdf-ingest.py befüllt) ---
  "pages": []   // → siehe §6
}
```

---

## 6. Seiten-Eintrag (nach pdf-ingest)

```jsonc
{
  "page": 23,
  "pdf_anchor": "EN_319_401_v2.2.1.pdf#page=23",  // kanonischer Anker — nie verlieren
  "section": "6.3.2",
  "section_title": "TSP repository requirements",
  "text_clean": "The TSP shall maintain a publicly accessible repository...",

  // --- Extrahierte Requirements ---
  "requirements": [
    {
      "id": "REQ-TSPBaseline-6.3.2-01",
      "level": "SHALL",                // SHALL | SHOULD | MAY | SHALL NOT
      "text": "The TSP shall maintain a publicly accessible repository of all issued certificates.",
      "xrefs": ["etsi.esi/TSP", "RFC_5280#4.2.1"],
      "legal_ref": null                // z.B. "eIDAS2/Art.24(2)(e)"
    }
  ],

  // --- Glossar-Treffer auf dieser Seite ---
  "glossary_hits": ["etsi.esi/TSP", "etsi.esi/QTSP"]
}
```

---

## 7. Glossar-Eintrag

```jsonc
{
  "namespace": "etsi.esi",
  "key": "TSP",
  "full_term": "Trust Service Provider",
  "abbr": "TSP",
  "definition": "An entity that provides one or more trust services.",
  "defined_in": "EN_319_401_v2.2.1.pdf#page=8",  // kanonischer PDF-Anker
  "defined_in_section": "3.1",
  "also_in": ["EN_319_411-1_v1.3.1", "eIDAS_2024_1183/Art.3(19)"],
  "layer": "trust-services",
  "mnemonic": "Der zugelassene Aussteller — wie eine Bank die Ausweise drucken darf"
}
```

### Namespaces

| Namespace | Bedeutet |
|---|---|
| `etsi.esi` | Im ETSI-ESI-Normwerk definiert |
| `eudi` | EUDI-Wallet-spezifisch (ARF, IA) |
| `legal` | Rechtlich definiert (eIDAS, GDPR) |
| `ietf` | In IETF RFCs definiert |
| `iso` | ISO/IEC-Standards |

Kollisionen zwischen Namespaces (gleicher Begriff, verschiedene Definitionen) werden **explizit** als `collision_note` vermerkt — nicht still überschrieben.

---

## 8. Contribution-Eintrag

```jsonc
{
  "id": "ESI(26)000369",
  "title": "Proposed changes to TSP repository requirements",
  "source": "Deutsche Telekom",
  "meeting": "ESI#68",
  "date": "2026-03-15",
  "status": "agreed",                   // agreed | noted | withdrawn | revised
  "type": "tdoc",                       // tdoc | wid | cr | ls
  "affects": ["EN_319_401_v2.2.1"],     // welche Standards betroffen
  "affects_sections": ["6.3.2"],
  "affects_requirements": ["REQ-TSPBaseline-6.3.2-01"],
  "pdf_anchor": "ESI(26)000369.pdf#page=1",
  "shortname": null                     // optional KI-generierter Kurzname
}
```

---

## 9. Diff-Eintrag (zwischen Versionen)

```jsonc
{
  "from": "EN_319_401_v2.1.1",
  "to": "EN_319_401_v2.2.1",
  "changes": [
    {
      "type": "requirement_modified",   // added | removed | modified | renumbered
      "req_id": "REQ-TSPBaseline-6.3.2-01",
      "section": "6.3.2",
      "page_from": 21,                  // Seite in v2.1.1
      "page_to": 23,                    // Seite in v2.2.1
      "delta": "SHALL → clarified with 'within 24 hours'",
      "triggered_by": ["ESI(25)001234"] // Contribution die die Änderung ausgelöst hat
    }
  ]
}
```

---

## 10. Dateistruktur

```
corpus/
  schema.md                  ← dieses Dokument (kanonisch)
  specs/
    EN_319_401_v2.2.1.json   ← ein JSON pro Spec-Version
    EN_319_411-1_v1.3.1.json
    RFC_5280.json
    ...
  glossary/
    etsi.esi.json             ← ein JSON pro Namespace
    eudi.json
    legal.json
    ietf.json
  diffs/
    EN_319_401_v2.1.1__v2.2.1.json
  contributions/
    ESI(26)000369.json        ← ein JSON pro Contribution
```

---

## 11. Kontextfenster-Prinzip

Jeder Eintrag ist so bemessen dass er **direkt ins KI-Kontextfenster** passt:

- `text_clean` einer Seite: max ~800 Token
- Ein vollständiger Spec-Eintrag ohne `pages[]`: ~300 Token (Metadaten + Refs)
- Ein Glossar-Eintrag: ~100 Token
- Ein Diff-Eintrag: ~150 Token

Für eine KI-Frage zu einem Thema werden gezielt **nur die relevanten Seiten** + Glossar-Treffer + Contribution-Verweise geladen — kein komplettes PDF, kein Static.
