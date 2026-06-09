# Reading Guide: Understanding the EN 319 Standard Family

> **Goal:** Anyone who understands the logic behind the numbers can orient themselves  
> in any new document immediately — before reading the table of contents.

---

## The Core Principle: One Number = One Address

```
EN   319   4   01
|    |     |   └─ Sequential number within the group
|    |     └───── Group (topic area)
|    └─────────── Family (ETSI ESI = "Signature World")
└──────────────── Prefix (European Standard)
```

**Mnemonic:** *"Three digits, three questions: What? Who? How exactly?"*

---

## The Family: `319` = Signature World

**Mnemonic:** *"Three-nineteen — where things get signed."*  
**Silly hook:** *"319 — you don't sign three times, just once, and need nine times less paper for it."*

All `EN 319 xxx` standards deal with electronic signatures, trust services, and the digital identity ecosystem in Europe. They are the technical backbone of eIDAS.

---

## The Groups: The Middle Digit Explains the Topic

| Group | Short Name | Topic | Mnemonic | Silly Hook |
|-------|------------|-------|----------|------------|
| `1xx` | SigFormat | Signature formats | *"What does a signature look like?"* | *"1 like 'first impression' — the shape you see first"* |
| `2xx` | SigProc | Creation & validation | *"How do you make and verify one?"* | *"2 like 'two steps' — sign first, then verify"* |
| `3xx` | TSA | Timestamps | *"When did it happen?"* | *"3 like 'three o'clock' — the timestamp records when"* |
| `4xx` | TSP | Trust Service Providers | *"Who is allowed to do it?"* | *"4 like 'four-eyes principle' — only the authorised may grant trust"* |
| `5xx` | LTA | Preservation / Long-term archiving | *"How does it stay valid?"* | *"5 like 'fifty years' — long-term archiving thinks in decades"* |
| `6xx` | TrustList | Trusted Lists / Trust Status | *"Who knows whom?"* | *"6 like 'six degrees of separation' — the trust list connects everyone"* |
| `46x/47x/49x` | WalletLayer | Wallet, Credentials, Attestations | *"The new wallet universe"* | *"46x/47x/49x — the next generation, almost at 500"* |

---

## The Last Digit: What Exactly Is Inside?

Within each group, the last two digits reveal the **document type**:

| Suffix | Role | Mnemonic | Silly Hook |
|--------|------|----------|------------|
| `x01` | Policy / Requirements (general) | *"The rules of the game"* | *"01 like 'Article 1' — the general framework, the principles"* |
| `x11` | Certificate profile | *"What the ID card looks like"* | *"11 like a penalty kick — precisely defined, no room for interpretation"* |
| `x21` | Signature/service profile | *"What the signature looks like"* | *"21 like 'blackjack' — the optimal, concrete implementation"* |
| `x31` | Conformance testing | *"The technical audit"* | *"31 like 'December 31st' — the deadline when everything gets checked"* |
| `x41` | Trust list format | *"The directory"* | *"41 like a statutory register — formal listing obligation"* |
| `x51` | Additional profiles / Extensions | *"The special cases"* | *"51 like 'Area 51' — the specialised, slightly hidden cases"* |

---

## Concrete Examples for Reference

### Foundation — *"The ID Cards of Machines"*

| Standard / RFC | Short Name (Abbr.) | What It Is | Mnemonic |
|----------------|-------------------|------------|----------|
| X.509 | PKIX-Cert | Format for digital certificates | *"The passport of machines"* |
| RFC 5280 (PKIX) | PKIX | Internet profile for X.509 | *"The passport law for digital IDs"* |
| RFC 6960 (OCSP) | OCSP | Online check whether a certificate is still valid | *"The registry office — is this ID still current?"* |
| RFC 5652 (CMS) | CMS | Container format for signed data | *"The envelope around the signature"* |

### Signature Formats — *"How Do You Sign Digitally?"*

| Standard | Short Name (Abbr.) | What It Is | Mnemonic | Silly Hook |
|----------|-------------------|------------|----------|------------|
| EN 319 122 | CAdES | CMS-based signature | *"C for Container/CMS"* | *"CAdES for binary data — the universal envelope"* |
| EN 319 132 | XAdES | XML-based signature | *"X for XML"* | *"XAdES for XML — the signature stays embedded in the document"* |
| EN 319 142 | PAdES | PDF-based signature | *"P for PDF"* | *"PAdES for PDFs — visible in the document, invisible in the structure"* |
| EN 319 182 | JAdES | JSON-based signature | *"J for JSON"* | *"JAdES for APIs — the most modern of the four"* |

### Trust Service Providers — *"Who Is Allowed?"*

| Standard | Short Name (Abbr.) | What It Is | Mnemonic |
|----------|-------------------|------------|----------|
| EN 319 401 | TSP-Baseline | General policy for all TSPs | *"The rules of the game for all trust service providers"* |
| EN 319 411-1 | QSign-CertProfile | Certificate profile for qualified signatures | *"The ID card profile for QES certificates"* |
| EN 319 411-2 | QSeal-CertProfile | Certificate profile for qualified seals | *"The company stamp profile"* |
| EN 319 421 | TSA-Policy | Policy for timestamp services | *"The rules for the timestamp issuers"* |
| EN 319 431 | TSA-Conformance | Conformance testing for TSA | *"The technical audit for timestamp services"* |

### Trust Lists — *"Who Knows Whom?"*

| Standard | Short Name (Abbr.) | What It Is | Mnemonic |
|----------|-------------------|------------|----------|
| EN 319 601 | TrustList-Format | Format for trusted lists | *"The database schema of European trust"* |
| EN 319 611 | EU-TrustList | EU-wide trusted list schema | *"The shared phone book of Europe for TSPs"* |
| EN 319 612 | TrustList-Profile | Profiles for national trusted lists | *"The national chapters in the European phone book"* |

### Legal Framework — *"The Law Behind It"*

| Document | Short Name (Abbr.) | What It Is | Mnemonic |
|----------|-------------------|------------|----------|
| Regulation (EU) 2014/910 | eIDAS-1 | Original eIDAS Regulation | *"The starting gun for pan-European digital identity"* |
| Regulation (EU) 2024/1183 | eIDAS-2 | Amendment with EUDI Wallet | *"eIDAS grown up — now with a wallet"* |
| Implementing Acts | eIDAS-IA | Technical implementing acts | *"The operating manual for eIDAS"* |

### Wallet Layer — *"The New Ecosystem"*

| Standard | Short Name (Abbr.) | What It Is | Mnemonic |
|----------|-------------------|------------|----------|
| TS 119 461 | RemoteID | Remote identity verification | *"How to verify someone digitally without meeting them"* |
| TS 119 471 | WalletTrust | Wallet trust policies | *"The rules of the game for the wallet itself"* |
| TS 119 491 | QEAA-Profile | Qualified Electronic Attestation of Attributes | *"The profile for digital credentials like a driving licence in the wallet"* |

### Credentials & Protocols — *"Proximity and Zero-Knowledge"*

| Standard | Short Name (Abbr.) | What It Is | Mnemonic |
|----------|-------------------|------------|----------|
| SD-JWT | SD-JWT | Selective Disclosure JWT | *"I prove what I know without showing everything"* |
| OpenID4VCI | OID4VCI | Protocol for credential issuance | *"VCI = Verifiable Credential Issuance — someone issues"* |
| OpenID4VP | OID4VP | Protocol for credential presentation | *"VP = Verifiable Presentation — I present"* |
| ISO 18013-5 | mDoc | Proximity ID (Bluetooth/NFC) | *"The digital ID that works without internet"* |
| ARF | EUDI-ARF | Architecture Reference Framework | *"The blueprint of the EUDI Wallet"* |

---

## The Complete Stack at a Glance

```
┌──────────────────────────────────────────────────────────────────┐
|  USE CASES       mDoc (Proximity) · SD-JWT · ZKP (emerging)     |
|  (iso/oidf)      OID4VP (Presentation) · OID4VCI (Issuance)     |
├──────────────────────────────────────────────────────────────────┤
|  WALLET          TS 119 461 (RemoteID) · 471 (WalletTrust)      |
|  (46x/47x/49x)   TS 119 491 (QEAA-Profile) · ARF               |
├──────────────────────────────────────────────────────────────────┤
|  LEGAL           eIDAS-2 (EU 2024/1183) · eIDAS-IA              |
|                  Mutual recognition across borders               |
├──────────────────────────────────────────────────────────────────┤
|  TRUST LISTS     EN 319 601 (TrustList-Format)                  |
|  (6xx)           EN 319 611 (EU-TrustList) · 612 (TL-Profile)   |
├──────────────────────────────────────────────────────────────────┤
|  TSP             EN 319 401 (TSP-Baseline) · 411 (CertProfile)  |
|  (4xx)           EN 319 421 (TSA-Policy) · 431 (TSA-Audit)      |
├──────────────────────────────────────────────────────────────────┤
|  SIG-FORMATS     CAdES · XAdES · PAdES · JAdES                  |
|  (1xx/2xx)       EN 319 122 · 132 · 142 · 182                   |
├──────────────────────────────────────────────────────────────────┤
|  FOUNDATION      X.509 (PKIX-Cert) · RFC 5280 (PKIX)           |
|  (ietf/iso)      RFC 6960 (OCSP) · RFC 5652 (CMS)              |
└──────────────────────────────────────────────────────────────────┘
              ^ Each layer builds on the one below
```

---

## Quick Orientation: Three Questions, One Answer

When you encounter a new `EN 319 xxx` document, ask three questions:

1. **Which group?** → Middle digit: `1`=Format, `2`=Process, `3`=Time, `4`=Who, `5`=Longevity, `6`=Directory
2. **Which role?** → Last two digits: `01`=Rules, `11`=ID-Profile, `21`=Sig-Profile, `31`=Audit
3. **Which version?** → `v2.2.1` — is there a newer one? Check the `predecessors` chain

**Example:** `EN 319 431 v1.1.1`  
→ Group `4` = TSP (who is allowed?) → Suffix `31` = Conformance testing (audit) → **"The technical audit for trust service providers"** ✓

---

## The Two-Pole Universe

This document corpus spans two poles that feed each other:

| Pole | Source | Role |
|------|--------|------|
| **Standards** | ETSI / IETF / ISO — published PDFs | The normative foundation |
| **Contributions** | ETSI Portal / Docbox — working documents | The working level: drafts, proposals, discussions |

A Contribution like `ESI(26)000369` proposes a change to `EN 319 401 (TSP-Baseline)`.  
Once agreed, it becomes part of `v2.3.x`. The diff between versions traces back to that Contribution.  
This is the chain: **Contribution → Standard version → Requirement → Implementation**.
