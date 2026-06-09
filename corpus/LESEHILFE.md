# Lesehilfe: Die EN 319 Normenfamilie verstehen

> **Ziel:** Wer die Logik hinter den Nummern kennt, kann sich in jedem neuen Dokument sofort orientieren —  
> ohne das Inhaltsverzeichnis gelesen zu haben.

---

## Das Grundprinzip: Eine Nummer = eine Adresse

```
EN   319   4   01
│    │     │   └─ Laufnummer innerhalb der Gruppe
│    │     └───── Gruppe (Themenbereich)
│    └─────────── Familie (ETSI ESI = "Signatur-Welt")
└──────────────── Präfix (Europäische Norm)
```

**Merksatz:** *„Drei Stellen, drei Fragen: Was? Wer? Wie genau?"*

---

## Die Familie: `319` = Signatur-Welt

**Eselsbrücke:** *„Drei-neunzehn — da wird unterschrieben."*  
**Dummer Merksatz:** *„319 klingt wie ‚drei-eins-neun' — stell dir vor, du unterschreibst dreimal, einmal, neunmal bis es rechtsgültig ist."*

Alle Normen der `EN 319 xxx`-Familie befassen sich mit elektronischen Signaturen, Vertrauensdiensten und dem digitalen Identitätsökosystem in Europa. Sie sind das technische Rückgrat von eIDAS.

---

## Die Gruppen: Die mittlere Stelle erklärt das Thema

| Gruppe | Kurzname | Thema | Eselsbrücke | Merksatz |
|--------|----------|-------|-------------|----------|
| `1xx` | SigFormat | Signaturformate | *„Wie sieht eine Unterschrift aus?"* | *„1 wie ‚eins' — das Erste, was man sieht: die Form der Unterschrift"* |
| `2xx` | SigProc | Erstellung & Validierung | *„Wie macht und prüft man sie?"* | *„2 wie ‚zwei Schritte' — erst unterschreiben, dann prüfen"* |
| `3xx` | TSA | Zeitstempel | *„Wann war es?"* | *„3 wie ‚drei Uhr' — der Zeitstempel sagt wann"* |
| `4xx` | TSP | Vertrauensdiensteanbieter | *„Wer darf es?"* | *„4 wie ‚Vier-Augen-Prinzip' — nur wer zugelassen ist darf Vertrauen vergeben"* |
| `5xx` | LTA | Preservation / Langzeitarchiv | *„Wie bleibt es gültig?"* | *„5 wie ‚fünfzig Jahre' — Langzeitarchivierung denkt in Jahrzehnten"* |
| `6xx` | TrustList | Trusted Lists / Trust Status | *„Wer kennt wen?"* | *„6 wie ‚sechs Grad Trennung' — die Vertrauensliste verbindet alle"* |
| `46x/47x/49x` | WalletLayer | Wallet, Credentials, Attestations | *„Das neue Wallet-Universum"* | *„46x/47x/49x — die nächste Generation, fast bei 500"* |

---

## Die letzte Stelle: Was genau steht drin?

Innerhalb jeder Gruppe verrät die letzte Stelle die **Art des Dokuments**:

| Suffix | Rolle | Eselsbrücke | Merksatz |
|--------|-------|-------------|----------|
| `x01` | Policy / Requirements (allgemein) | *„Die Spielregeln"* | *„01 wie ‚Artikel 1' — der allgemeine Rahmen, die Grundsätze"* |
| `x11` | Profil für Zertifikate | *„Wie der Ausweis aussieht"* | *„11 wie ‚Elfmeter' — ganz genau definiert, kein Spielraum"* |
| `x21` | Profil für Signaturen/Dienste | *„Wie die Unterschrift aussieht"* | *„21 wie ‚Blackjack' — das Optimum, die konkrete Ausführung"* |
| `x31` | Konformitätsprüfung | *„Der TÜV"* | *„31 wie ‚31. Dezember' — der Stichtag, an dem geprüft wird ob alles stimmt"* |
| `x41` | Vertrauenslisten-Format | *„Das Verzeichnis"* | *„41 wie ‚Paragraph 41' — formale Listenpflicht"* |
| `x51` | Zusatzprofile / Extensions | *„Die Sonderfälle"* | *„51 wie ‚Area 51' — die speziellen, etwas verborgenen Fälle"* |

---

## Konkrete Beispiele zum Nachschlagen

### Fundament — *„Der Ausweis der Maschinen"*

| Norm / RFC | Kurzname (Abbr.) | Was es ist | Eselsbrücke |
|------------|-----------------|------------|-------------|
| X.509 | PKIX-Cert | Format für digitale Zertifikate | *„Der Personalausweis der Maschinen"* |
| RFC 5280 (PKIX) | PKIX | Internet-Profil für X.509 | *„Das Passgesetz für PKIX-Ausweise"* |
| RFC 6960 (OCSP) | OCSP | Online-Prüfung ob Zertifikat noch gültig | *„Das Einwohnermeldeamt — ist dieser Ausweis noch gültig?"* |
| RFC 5652 (CMS) | CMS | Container-Format für signierte Daten | *„Der Briefumschlag um die Unterschrift"* |

### Signaturformate — *„Wie unterschreibt man digital?"*

| Norm | Kurzname (Abbr.) | Was es ist | Eselsbrücke | Merksatz |
|------|-----------------|------------|-------------|----------|
| EN 319 122 | CAdES | CMS-basierte Signatur | *„C wie Container/CMS"* | *„CAdES für Binärdaten — der universelle Briefumschlag"* |
| EN 319 132 | XAdES | XML-basierte Signatur | *„X wie XML"* | *„XAdES für XML — die Unterschrift bleibt im Dokument eingebettet"* |
| EN 319 142 | PAdES | PDF-basierte Signatur | *„P wie PDF"* | *„PAdES für PDFs — sichtbar im Dokument, unsichtbar in der Struktur"* |
| EN 319 182 | JAdES | JSON-basierte Signatur | *„J wie JSON"* | *„JAdES für APIs — die modernste der vier"* |

### Vertrauensdiensteanbieter — *„Wer darf es?"*

| Norm | Kurzname (Abbr.) | Was es ist | Eselsbrücke |
|------|-----------------|------------|-------------|
| EN 319 401 | TSP-Baseline | Allgemeine Policy für alle TSPs | *„Die Spielregeln für alle Vertrauensdiensteanbieter"* |
| EN 319 411-1 | QSign-CertProfile | Zertifikatsprofil für qualifizierte Signaturen | *„Der Personalausweis für QES-Zertifikate"* |
| EN 319 411-2 | QSeal-CertProfile | Zertifikatsprofil für qualifizierte Siegel | *„Das Firmenstempel-Profil"* |
| EN 319 421 | TSA-Policy | Policy für Zeitstempeldienste | *„Die Spielregeln für die Zeitstempel-Aussteller"* |
| EN 319 431 | TSA-Conformance | Konformitätsprüfung für TSA | *„Der TÜV für Zeitstempeldienste"* |

### Vertrauenslisten — *„Wer kennt wen?"*

| Norm | Kurzname (Abbr.) | Was es ist | Eselsbrücke |
|------|-----------------|------------|-------------|
| EN 319 601 | TrustList-Format | Format für Vertrauenslisten | *„Das Datenbankschema des europäischen Vertrauens"* |
| EN 319 611 | EU-TrustList | EU-weites Vertrauenslisten-Schema | *„Das gemeinsame Telefonbuch Europas für TSPs"* |
| EN 319 612 | TrustList-Profile | Profile für nationale Vertrauenslisten | *„Die nationalen Kapitel im europäischen Telefonbuch"* |

### Rechtlicher Rahmen — *„Das Gesetz dahinter"*

| Dokument | Kurzname (Abbr.) | Was es ist | Eselsbrücke |
|----------|-----------------|------------|-------------|
| Verordnung (EU) 2014/910 | eIDAS-1 | Ursprüngliche eIDAS-Verordnung | *„Der Startschuss für europaweite digitale Identität"* |
| Verordnung (EU) 2024/1183 | eIDAS-2 | Novellierung mit EUDI Wallet | *„eIDAS erwachsen — jetzt mit Geldbörse"* |
| Implementing Acts | eIDAS-IA | Technische Durchführungsrechtsakte | *„Die Betriebsanleitung zu eIDAS"* |

### Wallet-Schicht — *„Das neue Ökosystem"*

| Norm | Kurzname (Abbr.) | Was es ist | Eselsbrücke |
|------|-----------------|------------|-------------|
| TS 119 461 | RemoteID | Remote-Identitätsprüfung | *„Wie man jemanden digital ausweist ohne ihn zu sehen"* |
| TS 119 471 | WalletTrust | Wallet-Vertrauenspolicies | *„Die Spielregeln für das Wallet selbst"* |
| TS 119 491 | QEAA-Profile | Qualified Electronic Attestation of Attributes | *„Das Profil für digitale Nachweise wie Führerschein im Wallet"* |

### Credentials & Protokolle — *„Proximity und Zero-Knowledge"*

| Standard | Kurzname (Abbr.) | Was es ist | Eselsbrücke |
|----------|-----------------|------------|-------------|
| SD-JWT | SD-JWT | Selective Disclosure JWT | *„Ich beweise was ich weiß ohne alles zu zeigen"* |
| OpenID4VCI | OID4VCI | Protokoll für Credential-Ausstellung | *„VCI = Verifiable Credential Issuance — jemand stellt aus"* |
| OpenID4VP | OID4VP | Protokoll für Credential-Vorlage | *„VP = Verifiable Presentation — ich zeige vor"* |
| ISO 18013-5 | mDoc | Proximity-Ausweis (Bluetooth/NFC) | *„Der digitale Ausweis der auch ohne Internet funktioniert"* |
| ARF | EUDI-ARF | Architecture Reference Framework | *„Der Bauplan des EUDI Wallets"* |

---

## Der komplette Stack auf einen Blick

```
┌──────────────────────────────────────────────────────────────────┐
│  USE CASES       mDoc (Proximity) · SD-JWT · ZKP (in Entw.)     │
│  (iso/oidf)      OID4VP (Presentation) · OID4VCI (Issuance)     │
├──────────────────────────────────────────────────────────────────┤
│  WALLET          TS 119 461 (RemoteID) · 471 (WalletTrust)      │
│  (46x/47x/49x)   TS 119 491 (QEAA-Profile) · ARF               │
├──────────────────────────────────────────────────────────────────┤
│  LEGAL           eIDAS-2 (EU 2024/1183) · eIDAS-IA              │
│                  Wechselseitige Anerkennung über Staatsgrenzen   │
├──────────────────────────────────────────────────────────────────┤
│  TRUST LISTS     EN 319 601 (TrustList-Format)                  │
│  (6xx)           EN 319 611 (EU-TrustList) · 612 (TL-Profile)   │
├──────────────────────────────────────────────────────────────────┤
│  TSP             EN 319 401 (TSP-Baseline) · 411 (CertProfile)  │
│  (4xx)           EN 319 421 (TSA-Policy) · 431 (TSA-TÜV)       │
├──────────────────────────────────────────────────────────────────┤
│  SIG-FORMATE     CAdES · XAdES · PAdES · JAdES                  │
│  (1xx/2xx)       EN 319 122 · 132 · 142 · 182                   │
├──────────────────────────────────────────────────────────────────┤
│  FUNDAMENT       X.509 (PKIX-Cert) · RFC 5280 (PKIX)           │
│  (ietf/iso)      RFC 6960 (OCSP) · RFC 5652 (CMS)              │
└──────────────────────────────────────────────────────────────────┘
         ▲ Jede Schicht baut auf der darunter liegenden auf
```

---

## Schnell-Orientierung: Drei Fragen, eine Antwort

Wenn du ein neues `EN 319 xxx`-Dokument siehst, stelle dir diese drei Fragen:

1. **Welche Gruppe?** → Mittlere Stelle: `1`=Format, `2`=Prozess, `3`=Zeit, `4`=Wer, `5`=Langzeit, `6`=Verzeichnis
2. **Welche Rolle?** → Letzte zwei Stellen: `01`=Spielregeln, `11`=Ausweis-Profil, `21`=Signatur-Profil, `31`=TÜV
3. **Welche Version?** → `v2.2.1` — und gibt es eine neuere? Schau in die `predecessors`-Kette

**Beispiel:** `EN 319 431 v1.1.1`
→ Gruppe `4` = TSP (Wer darf es?) → Suffix `31` = Konformitätsprüfung (TÜV) → **„Der TÜV für Vertrauensdiensteanbieter"** ✓
