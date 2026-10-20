# Third-party notices & SBOM

> **Who this is for:** anyone redistributing DataQ's images, and the reviewer who has to
> confirm what a release bundles and under which licences. DataQ itself is MIT; every
> dependency it ships is permissive or weak-copyleft, and the notices and bill of materials
> below are generated from the real dependency set on every build, never hand-maintained.

## What ships, and where

| Artifact | Where to find it | What it covers |
|---|---|---|
| `THIRD-PARTY-NOTICES.md` | `/THIRD-PARTY-NOTICES.md` in the **backend** image | every Python package installed into the image's virtualenv — name, version, licence, homepage, the full licence text and any Apache `NOTICE` file shipped with the package |
| `THIRD-PARTY-NOTICES.md` | `/THIRD-PARTY-NOTICES.md` in the **frontend** image, also served at `https://<your-dataq>/third-party-notices` | every runtime npm package bundled into the web app (build-only tooling is not distributed and is not listed) |
| `THIRD-PARTY-NOTICES-backend.md`, `THIRD-PARTY-NOTICES-frontend.md` | assets on each [GitHub release](https://github.com/TheurgicDuke771/DataQ/releases) | the same two files, extracted from the images that release published |
| `sbom-dataq-backend.spdx.json`, `sbom-dataq-frontend.spdx.json` | assets on each GitHub release (and a workflow artifact of every image build) | an SPDX 2.3 SBOM produced by [Syft](https://github.com/anchore/syft) over the published image: the Python / npm packages **and** the base image's OS packages, with licence data where the package declares it |

The base images' own packages (Debian for the backend, Alpine for the frontend) are
covered by the SBOM, not by the notices file; their licence texts live under
`/usr/share/doc` and `/usr/share/licenses` in the image as the distributions ship them.

## Verify

```bash
# Read the notices straight out of a published image.
docker run --rm --entrypoint cat ghcr.io/theurgicduke771/dataq-backend:latest /THIRD-PARTY-NOTICES.md | less
docker run --rm --entrypoint cat ghcr.io/theurgicduke771/dataq-frontend:latest /THIRD-PARTY-NOTICES.md | less

# From a running deployment.
curl -fsS https://<your-dataq>/third-party-notices | head

# Re-derive the SBOM yourself and compare package lists with the release asset.
syft ghcr.io/theurgicduke771/dataq-backend:v1.3.0 -o spdx-json > local.spdx.json
```

## The licence gate

Continuous integration fails a change whose dependency tree — the backend's full
runtime + test requirements, and the frontend's whole lockfile — adds a package under a
strong-copyleft or source-available licence (GPL, AGPL, SSPL, BUSL, Elastic, Commons Clause),
or whose licence cannot be determined from its metadata. Permissive licences (MIT, BSD,
Apache-2.0, ISC, PSF, Unlicense, 0BSD, Zlib, CC0, BlueOak, OFL for bundled fonts) and weak
copyleft with notice preservation (MPL-2.0, LGPL) are allowed; a dual-licensed package passes
when one of its options is allowed. The backend image build runs the same check over the
exact set it installs, so a violation cannot reach a published image either way.

A package whose packaging metadata omits or misstates a licence that is known and
permissive is admitted only through a reviewed override in the repository
(`scripts/licenses/overrides.toml`), which records the SPDX licence, a link to the
upstream licence file as evidence, and the reason. The override cannot admit a forbidden
licence; that would need an architecture-decision-level exception.
