# Public release audit

Status: `public-review-ready` after the checks below complete on the final commit.

## What is publishable

- [MEASURED] The repository contains source code, frozen configurations, reports,
  tests, plots, and machine-readable derived artifacts.
- [MEASURED] Raw NOAA/NCEI, Natural Earth, CyclObs, and TC PRIMED downloads remain
  excluded under ignored `data/raw/` directories. Provenance records retain source
  URLs, response hashes, sizes, and frozen output summaries.
- [MEASURED] The rejected v0.1 baseline remains reachable through the annotated tag
  `v0.1-rejected-baseline`.
- [MEASURED] The largest tracked file is below 8 MB; no tracked object approaches
  GitHub's 100 MB per-file limit.

## Secret finding and remediation

- [MEASURED] Gitleaks 8.30.1 initially scanned 30 commits and 16.65 MB of history.
  It reported seven generic-key findings in one derived JSON file.
- [MEASURED] All seven findings were public TC PRIMED S3 pagination continuation
  tokens. They were request state rather than authentication credentials. The public
  release removes their values anyway.
- [MEASURED] The source-audit generator now stores page number and
  `continuation_token_sha256`; its public request URL omits the token value. A regression
  test verifies that the original token cannot enter serialized evidence.
- [MEASURED] `git filter-repo` removed the seven values from every reachable commit.
  The old-to-new provenance mapping is frozen in
  `history/public-sanitization-commit-map.txt`; all current report and artifact commit
  references were remapped and manifest hashes refreshed.

## Final scan evidence

- [MEASURED] Main repository release candidate: 34 reachable commits, 16.68 MB scanned,
  zero findings.
- [MEASURED] Embedded pre-monorepo IBTrACS bundle: 4 commits, 8.12 MB scanned,
  zero findings.
- [MEASURED] Current working tree directory scan: 474.12 MB including local virtual
  environments, zero findings.
- [MEASURED] The unified verification entry point enforces the permanent C-track
  semantic boundary before release.
- [MEASURED] Manual tracked-text patterns for API keys, client secrets, access tokens,
  passwords, authorization headers, bearer tokens, private-key markers, GitHub tokens,
  and AWS access-key IDs returned zero matches.
- [MEASURED] Git author identity uses `taozhe6@users.noreply.github.com`.

Local absolute paths inside frozen provenance identify the original execution environment.
They contain no credential values and remain for reproducibility.

## Reproduce the release checks

```bash
./scripts/verify_all.sh
gitleaks git --redact --exit-code 1 .
gitleaks dir --redact --exit-code 1 .
git show --no-patch v0.1-rejected-baseline
git fsck --full --no-dangling
```

The embedded bundle is audited by cloning
`history/ibtracs-agency-disagreement-pre-monorepo.bundle` into a temporary directory and
running the same `gitleaks git` command there.

## Remaining scientific limits

- Public availability increases auditability and does not change any branch to
  `validated`.
- Independent landfall truth coverage remains 0/108.
- The C-proxy v2 intensity waveform remains a proxy.
- C-structure labels remain incomplete outside quality-controlled observable periods.
- `theta=Ck/h` remains a bounded synthetic sensitivity without probability semantics.
