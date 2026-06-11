# TODO: Secret Detection + Credential Backend

> **Do later** — documented here for visibility since eudi-nexus is public.

## Architecture (two layers)

### Layer 1 — Preventive scanning

- **trufflehog** (preferred over git-secrets — stronger, history-aware) as pre-commit hook
- Prevents secrets from ever entering git history
- CI/CD: trufflehog scans every PR incl. full git history
- Pattern registry for project-specific formats (API keys, tokens)

### Layer 2 — Storage backend: KeePass + GPG

- Secrets live in a `.kdbx` file encrypted with a GPG key
- Scripts fetch secrets at runtime via `keepassxc-cli` or `gpg --decrypt`
- No plaintext `.env` on disk — env vars injected directly into process
- `.kdbx` committed to repo (encrypted); GPG private key stays local / on YubiKey

```sh
# Runtime usage example
export API_KEY=$(keepassxc-cli show -a Password vault.kdbx "ETSI_API_KEY")
```

### `.gitignore` consequences
- `.env` in `.gitignore` — never written to disk
- `.kdbx` committed (encrypted, safe)
- GPG public key in repo; private key local only

## Reference implementation

Full design documented in **bootstrap-foundation**:

👉 [`bootstrap-foundation • feature/enter-once-cache • CREDENTIAL-BACKENDS.md`](https://github.com/KonradLanz/bootstrap-foundation/blob/feature/enter-once-cache/CREDENTIAL-BACKENDS.md)

Implement here once that branch is merged / stabilised.
