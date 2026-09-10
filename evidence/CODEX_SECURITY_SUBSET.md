# Codex Linux security subset, 2026-09-10

The reference CLI now projects a narrow `1.1.0-draft.1` security policy into
Codex 0.154.0. The native tests use the projected policy after `agents apply`.
This establishes direct sandbox shell evidence on Linux amd64. It does not
establish full adapter support, model-tool coverage, or approval-flow behavior.

## Results and scope

| Evidence class | Result | Limit |
| --- | --- | --- |
| Native observations after apply | 27/27 pass | Filesystem, process, network, and explicit credential-inheritance observations in the shell subset. |
| Adapter refusal | 23/23 pass | Unsupported policy, unknown authority, missing/unverified binary, and existing file aliases refuse without repository writes. |
| Lifecycle and integrity | 9/9 pass | Plan preserves repository state; apply, idempotence, removal, re-enable, trust preservation, and denied-side-effect checks pass. |
| Stable specification | 25/25 pass | Existing 1.0 baseline. |
| Draft specification | 59/59 pass | Strict schema fixtures, including runtime grants and the Codex example. |
| Go CLI | Pass | Includes security ownership and injected transaction rollback. |
| Workbench | 67 tests pass | Includes shared schema/CLI fixtures and result-schema checks. |
| Cross-build | Darwin arm64 and Windows amd64 pass | Compilation only; no native enforcement claim. |

The repository tree validation, compatibility check, edited JSON validation,
and whitespace checks pass. Stable support and release gates are unchanged.
Claude native verification remains skipped at the user's request.

The 27 observations cover allowed and denied reads/writes, overlapping path
rules, symlink/traversal paths, new hard links from denied/read-only/outside
files, read-only tree rename, process-root aliases, protected configuration
paths, shell composition, detached children, and network denial. TCP, UDP,
IPv6, private-address, and Unix-socket targets have reachable host fixtures.
Only permission errors count as native denial. Credential marker inheritance
is observed explicitly; this is not credential isolation.

The 60 original scenario rows are classified in
[SECURITY_SCENARIOS.json](SECURITY_SCENARIOS.json). For Codex, 12 rows have
verified enforcement, 14 have verified refusal, and 2 have verified lifecycle
results. One rollback row has deterministic tests only. The redirect row is
outside the deny-all network subset: there is no allowed initial connection.
The 30 Copilot projection rows remain pending. Its separate
[native assessment](COPILOT_SECURITY_ASSESSMENT.md) records a local-network
mismatch. Refusal does not count as enforcement.

## Invocation and authority

See [security profiles](../../docs/SECURITY_PROFILES.md) in the superproject
for policy requirements, authority checks, use, and removal.

The policy requires shell-only coverage, read access by default, explicit
process runtime grants, credential inheritance, and subprocess network denial.
Optional permissions allow shell commands without rules. Exact-command rules,
ask/deny permissions, credential isolation, other scopes, and default-deny
filesystem policy refuse.

The adapter requires an existing trusted, configuration-only native home
outside the workspace. It rejects unknown authority layers and native keys.
It verifies the exact native binary hash before a read-only startup probe.
Plan can create native temporary state but does not modify trust configuration.
The isolated test harness prepares trust only for its own temporary fixture;
ordinary CLI projection does not write trust, managed policy, or credentials.

Use the exact plan invocation and replacement environment after a fresh plan
check. No runtime launcher is installed. External changes to authority, files,
mounts, or binaries invalidate this preflight. Existing workspace hard links,
alternate mount views, nested mounts, and symlinks in restricted trees refuse.
No claim covers concurrent changes by outside processes.

The CLI owns marked selectors and the named permissions profile in
`.codex/config.toml`. Removal preserves unrelated bytes and makes the recorded
explicit-profile command fail. Other native invocation modes remain outside
this subset.

## Exact inputs

- OS: `Linux-7.0.0-31-generic-x86_64-with-glibc2.43`; unprivileged Linux amd64.
- Codex: `0.154.0`; binary SHA-256
  `3188814c35471432d4123203e0eb38e5bddc60226e3d7ddf0e59e649ea140022`.
- Tested CLI SHA-256:
  `65baa5e4334f3836986346b97c07ce315f251a8722513fb640e9cd5f8884b4e7`.
- Runner SHA-256:
  `9b54719af3ccd4fe03ee5820750752ecebfcdae574e35fa2d1e7c0b2a57edc12`.
- Result `results/codex-security-04/result.json` SHA-256:
  `c90cbf37f551f50f22eb130d246a544bb41f44ace1881383a27f8771977a445e`.

Base commits: root `b83f020e2fa2c2bc672907c3b0ae50f2ca1cbea1`,
CLI `79d60def85a5e19a5424c6d5840b5bdd3b6fd6b0`,
SPEC `279a67bb8bc82e1af074308a1d1eb6cb802323d2`, and
WORKBENCH `b256f2cc647eac452c8fd5882361144d57faeac3`.
All four checkouts have uncommitted development changes. This is local evidence;
no commit, publication, or bit-for-bit rebuild is implied.

## Reproduction and retained attempts

From the superproject, build the CLI from `CLI` and run the harness:

```sh
(cd CLI && go build -o bin/agents-codex-security ./cmd/agents)
python3 WORKBENCH/conformance/run_codex_security.py \
  --agents CLI/bin/agents-codex-security \
  --result-dir /mnt/DATA/tmp/oda-codex-new-evidence
```

The result directory must be new. Fixtures live under `/mnt/DATA/tmp` to avoid
ancestor project configuration. No model account is used.

Run 01 refused because its fixture inherited Workbench native configuration.
Run 02 passed 22 observations, 21 refusals, and 9 lifecycle checks. Run 03
passed 26 observations, 22 refusals, and 9 lifecycle checks. Final run 04 adds
the outside-file hard-link case and passes the counts above. All are retained.

The local archive `results/security-native-final.tar.gz` contains these native
results, the tested CLI binary, validation logs, source snapshots and base
commit metadata, and official documentation with source hashes. The adjacent
`.sha256` file identifies the archive. Raw Copilot homes and session/auth logs
are excluded. The earlier [initial report](SECURITY_DRAFT.md) and its archive
remain historical evidence.
