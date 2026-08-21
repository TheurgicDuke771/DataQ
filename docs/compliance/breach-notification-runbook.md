# Breach-notification runbook

> **Who this is for:** whoever operates a DataQ deployment. GDPR Art 33 gives a
> controller **72 hours** from *awareness* to notify the supervisory authority;
> HIPAA breach notification runs on its own clocks (60 days to individuals, and
> for large breaches to HHS/media). DataQ is customer-deployed (BYOL, ADR 0013),
> so **the deploying organization owns notification** — this runbook is the
> processor-side half: what to check *in DataQ*, in what order, and what evidence
> the platform can hand your incident team. §1–§4 are written for the reference
> deployment shape (the in-repo Azure/AWS stacks); §5 is the template to adapt for
> any other deployment.

## 0. What counts as a breach *of DataQ*

DataQ's own crown jewels, in blast-radius order:

1. **The secret store** (Key Vault / Secrets Manager / OpenBao) — warehouse
   credentials. A compromise here is a breach of **every connected datasource**,
   not just DataQ.
2. **The database** — `results.sample_failures` / `observed_value` (incidental
   personal data), workspace accounts, the audit trail.
3. **A privileged workspace account or PAT** — reads redacted samples and configs
   at whatever level the role allows; an Admin can re-point connections.
4. **The webhook/SMTP alert channels** — carry check names and redacted samples.

An availability incident (stack down, worker dead) is an *outage*, not a breach —
use the ops runbook. It becomes a breach question the moment data or credentials
may have been read or altered by an unauthorized party.

## 1. First hour — contain and stamp the clock

Record the **time of awareness** first; every regulatory clock runs from it.

1. **Revoke what's compromised, narrowest first:**
   - A PAT → delete it in *Profile → API keys* (or `DELETE /api/v1/api_keys/{id}`).
     Role changes and revocations apply on the holder's **next request** — there
     is no token to wait out.
   - A user account → demote to `viewer` (Admin → Members) or disable at the IdP;
     OTP sessions are server-side revocable.
   - A warehouse credential → rotate at the warehouse, then update **every**
     connection secret that carries it. One credential fans out to N per-connection
     secrets — rotate ALL of them and re-run Test Connection on each; a partial
     rotation leaves silently dead or silently live copies (this exact miss caused
     a three-week outage on two connections; see the ops log).
   - The secret store itself → rotate its access (managed identity / AppRole /
     token), then every secret it held.
2. **Preserve evidence before restarting anything** — container logs are lost on
   replacement; export them first.
3. **Do not delete rows** — the audit trail is append-only by construction; keep
   it that way for the investigation.

## 2. Assess — what DataQ can tell you

| Question | Where to look |
|---|---|
| Who accessed which results, when | `audit_events` (G1): config mutations **and** data reads, on REST and MCP, admin-queryable |
| What a compromised PAT could see | The token's owner + their role and suite grants (ADR 0033 two axes); MCP reads are covered by the same read events |
| Whether samples were redacted when read | `redaction` / `redacted_columns` fields on every results surface; per-suite column policy + G3 tag floor say what *would* have been masked |
| What personal data was even present | The [DPIA input sheet](dpia-input-sheet.md) inventory; `sample_failures_purged_at` tells you whether the window had already purged |
| What left the system | Alert history (per-suite notification config + delivery logs), telemetry sink, the [sub-processor disclosure](sub-processors.md) vectors |
| Infra-side access | Cloud audit logs (Azure Activity Log / CloudTrail), Postgres logs, secret-store audit (Key Vault/Secrets Manager logging, OpenBao audit device if enabled) |

Severity guide: secret-store or DB compromise → assume Class 1 **and** Class 2
data affected until proven otherwise. Single PAT → scope to that role's reach,
which the audit trail makes concrete.

## 3. Notify — the organizational half

The 72-hour GDPR clock and HIPAA's clocks are the **controller's**; DataQ (the
project) is not a party to your notifications. Your notification content will
want, from §2: categories and approximate volume of subjects/records (sample rows
in-window × affected suites), the redaction state at read time, and the
containment steps + timestamps from §1.

If your deployment uses DataQ's reference stacks unmodified, upstream a
**security advisory** to the repo (private report preferred) when the breach
traces to a DataQ defect — fixed-or-filed applies to vulnerabilities too.

## 4. Recover and close out

1. Rotate anything not already rotated in §1 (assume-breach for adjacent
   credentials); restart dependent revisions only where env-injected values
   changed — connection secrets are read at runtime and need no restart.
2. Re-run the post-deploy smoke + a live suite run per datasource class.
3. Write the incident record: timeline, evidence, root cause, notification
   decisions (including a reasoned decision *not* to notify, which Art 33 also
   expects to be documented).
4. File the follow-ups — every gap found becomes an issue, never a prose note.

## 5. Template for non-reference deployments

Adapt §1–§4 with your own values; the DataQ-side mechanics are identical.

- **Awareness timestamp & reporter:** ______
- **Compromised element** (PAT / account / connection secret / secret store / DB /
  channel): ______
- **Containment actions + times** (revocations, rotations — remember the
  one-credential-to-N-secrets fan-out): ______
- **Scope from `audit_events`** (actors, reads, mutations in window): ______
- **Personal data present** (DPIA sheet classes; purge stamps): ______
- **Redaction state at read time:** ______
- **Egress vectors active** (from the sub-processor disclosure): ______
- **Controller notification decision + basis:** ______
- **Follow-up issues filed:** ______

Last reviewed: 2026-08-21 (G6, [#1452](https://github.com/TheurgicDuke771/DataQ/issues/1452)).
