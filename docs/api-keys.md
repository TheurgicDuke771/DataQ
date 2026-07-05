# API keys (personal access tokens)

DataQ can mint you a **personal access token (PAT)** — a long-lived, revocable
credential for scripts, CI, and always-on MCP clients, where an SSO browser
flow or a ~60-minute Azure AD token doesn't fit (ADR
[0026](adr/0026-auth-api-keys-and-principal-seam.md)).

A PAT authenticates **as you**: it inherits exactly your per-suite access
(`view < edit < admin < owner`) on the REST API and `/mcp` alike. There is no
separate "API-key permission model" to configure — if you can see a suite in
the web app, your key can; if you can't, it can't.

## Minting a key

`POST /api/v1/me/api-keys` (any authenticated caller — including an existing
PAT):

```bash
curl -sS -X POST "$DATAQ_URL/api/v1/me/api-keys" \
  -H "Authorization: Bearer $AZURE_AD_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "ci-smoke", "expires_in_days": 90}'
```

The response carries the plaintext token **exactly once**:

```json
{
  "id": "…",
  "name": "ci-smoke",
  "key_prefix": "dq_live_AbCd",
  "expires_at": "2026-10-02T09:00:00Z",
  "token": "dq_live_…"
}
```

!!! danger "Store it now — it cannot be retrieved again"
    Only a hash is kept at rest. If the token is lost, revoke the key and mint
    a new one. Never commit a token to version control; put it in your CI
    secret store / Key Vault.

- `name` — a label for telling keys apart (e.g. `ci-smoke`, `mcp-desktop`).
- `expires_in_days` — default **90**, maximum **365**. Non-expiring keys are
  not supported.

## Using a key

The token goes wherever an Azure AD bearer would — same header, same
endpoints:

```bash
curl -sS "$DATAQ_URL/api/v1/suites" -H "Authorization: Bearer dq_live_…"
```

For MCP clients, use it in place of the short-lived browser token in your
client config (see [AI assistants (MCP setup)](mcp-setup.md)) — no more
re-pasting on 401s until the key expires.

## Listing and revoking

```bash
curl -sS "$DATAQ_URL/api/v1/me/api-keys" -H "Authorization: Bearer …"
curl -sS -X DELETE "$DATAQ_URL/api/v1/me/api-keys/<key_id>" -H "Authorization: Bearer …"
```

Listing returns metadata only (name, prefix, expiry, last-used) — never the
token. Revocation (`DELETE`, 204) is immediate and idempotent: the key stops
authenticating on its next request. Mint one key **per integration** so
revoking one doesn't break the others.

## Security properties

| Property | Behaviour |
|---|---|
| At rest | SHA-256 hash only; the plaintext is never stored or logged (prefix only) |
| Show-once | Plaintext appears solely in the creation response |
| Expiry | Mandatory (≤ 365 days); expired keys stop authenticating |
| Revocation | Immediate, per-key, idempotent |
| Failure mode | Unknown, revoked, and expired keys all return the **same** 401 — no probing oracle |
| Owner lifecycle | Deleting/deactivating a user kills their keys with them |
| Last used | `last_used_at` tracked (coarse-grained) for spotting stale keys |
