# ADR 0023 — Container image registry: GitHub Container Registry (GHCR) over ACR / Docker Hub

- **Status:** Accepted
- **Date:** 2026-06-27
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0010](0010-provider-agnostic-infrastructure-seams.md) (Azure is one impl behind
  each seam — the registry is infra, not business logic), [0013](0013-marketplace-distribution-and-anti-lock-in.md)
  (BYOL/marketplace distribution — a neutral registry doubles as the future distribution channel),
  [0021](0021-demo-test-data-environment-strategy.md) (deploy/harness context). Supersedes the ACR
  choice scaffolded in the Week-7 deploy work (#379); rides the `deploy.yml` SHA-pin (#382).

## Context

The Week-7 deploy scaffolding (#379) wired `.github/workflows/deploy.yml` to **Azure Container
Registry (ACR)**: backend image built in CI, pushed to ACR, pulled by Azure Container Apps (api +
worker share one image; a migrate Job runs `alembic upgrade head`). Before the apply, we reconsidered
the registry. The DataQ repo is **public**, and a standing project principle (ADR 0010/0013) is that
**Azure is one implementation behind each seam** — no Azure-specific assumption should be load-bearing
where a neutral option is equivalent. A registry is pure infra; ACR couples the artifact path to Azure
for no functional gain.

Options weighed: **ACR**, **Docker Hub**, **GHCR**.

## Decision

**Use GitHub Container Registry (`ghcr.io`) as the container registry for the backend image.**

- CI pushes `ghcr.io/theurgicduke771/dataq-backend:${{ github.sha }}` (immutable SHA tag, #382),
  authenticating with the built-in `GITHUB_TOKEN` (`permissions: packages: write`) — **no separate
  registry account or stored PAT** in CI.
- The package is published **public** (the repo is already public and the image bakes in no secrets —
  credentials are injected at runtime via Key Vault / Container Apps secrets per the working
  agreement), so **Azure Container Apps pulls anonymously — no registry credential stored on the
  apps**. This preserves the "no literals / no long-lived registry secret" posture that ACR's managed
  identity gave us, without the Azure coupling.
- The migrate Job, api, and worker all pull the **same** SHA-pinned image.

## Consequences

**Positive**
- **Vendor-neutral** — the artifact path no longer assumes Azure (ADR 0010/0013). If DataQ moves off
  Azure, the registry doesn't move with it.
- **Free** for public images; **no Docker Hub pull-rate throttling** on our own GHCR images (matters
  under ACA autoscaling + worker + migrate Job all pulling).
- **No extra credential** — CI uses `GITHUB_TOKEN`; ACA pulls a public package anonymously. One fewer
  secret to rotate than Docker Hub (PAT) and parity with ACR's MI on the "no literals" goal.
- **Doubles as the BYOL/marketplace distribution registry** (ADR 0013) — a neutral public registry is
  the natural home for a customer-pullable image post-v1.
- Same GitHub surface as the source + Actions — one fewer system to provision/manage than ACR.

**Negative / watch**
- Loses ACR's in-region **co-location** with ACA → pulls cross the internet (added cold-start/scale-out
  latency) and loses **Defender for Cloud** registry vulnerability scanning inside the Azure trust
  boundary. Mitigation: image scanning can run in CI (e.g. Trivy/Scout) independent of the registry;
  the security cadence (CLAUDE.md §6) already covers SAST/deps.
- If the image ever needs to be **private** (e.g. a closed BYOL build), GHCR pull from ACA requires a
  GitHub PAT as a Container Apps registry secret — re-introducing a stored credential. Revisit then.
- Relies on GHCR availability for deploys/scale-out (as any external registry would).

## Alternatives considered

- **Azure Container Registry (ACR).** Best purely *in-Azure*: managed-identity pull (no secret),
  region co-location, Defender scanning. Rejected as the default because it couples the artifact path
  to Azure (against ADR 0010/0013) and carries a small standing cost, for benefits that are latency/
  scanning niceties rather than correctness. Remains a valid swap if a fully-in-Azure trust boundary is
  later required — the registry is config, not code.
- **Docker Hub.** Vendor-neutral and free for public images, but **pull-rate limits** (anon/free tiers)
  are a real risk under ACA scale-out, and it needs a **separate account + PAT** stored as an ACA
  registry credential. Strictly worse than GHCR here (GHCR has neither problem and reuses GitHub auth).
  Better reserved as an optional *additional* public distribution mirror if ever wanted.
