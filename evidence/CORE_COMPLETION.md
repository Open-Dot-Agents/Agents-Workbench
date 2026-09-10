# Core completion review — 2026-09-10

This review implements the approved refusal policy. It does not ratify or
publish the standard. No native adapter is promoted to supported.

## Implemented changes

| Contract or release rule | Change | Verification |
| --- | --- | --- |
| Unselected skills have no projected effect | Codex and Copilot refuse non-empty canonical skills before writes | Go tests cover initially off, enabled, removed, enabled again, repeated apply, force, export, and sync |
| Missing stdio environment sources prevent use | Codex refuses stdio references regardless of apply-time environment | No-write tests and native refusal cases; remote header references retain their mapping |
| Repository manifests are required | Public CLI validation and projection require the manifest | Go regression test and executable reproduction |
| Explicit required capabilities cannot be lost | Unsupported required capabilities are refused before writes | Plan, sync, and export tests; executable reproduction |
| Remote references retain indirection | Legacy Codex and Claude exports map remote references | Go regression tests |
| Required conformance checks cannot be skipped | Missing Workbench checkout fails CI; release requires all three support rows | Workflow lint, deterministic suite, and negative registry check |
| Native evidence must agree | Baseline/extended gate checks versions, source snapshots, binary hashes, native assertions, and clean release commits | Synthetic missing, failing, mixed, preflight-only, and dirty-source tests |
| Release evidence must be durable | Native archives include results, source snapshots, pins, checksums, and reproduction instructions; release attaches verified archives | Workflow lint; public publication remains blocked |

Canonical skills and existing native configuration remain unchanged on refusal.
Direct harness invocation can still discover unselected skills. Expected
refusals do not establish full profile or capability support.

## Deterministic validation

- Go tests: pass.
- Specification conformance: 25/25 pass.
- Workbench tests: 64 pass, with no skips.
- Canonical repository validation: pass.
- Compatibility registry, Markdown, and CLI declarations: agree.
- Actionlint v1.7.7: pass.
- Whitespace checks: pass.
- Reviewed CLI compiles for Linux, macOS, and Windows on amd64 and arm64.
  Cross compilation does not establish native support on those platforms.

The specification, schemas, and conformance fixtures are unchanged. The audit
fixed existing contract enforcement defects in the CLI; it made no normative
policy changes. Baseline native evidence remains distinct from extended
coverage and from the release-support decision.

## Reviewed native evidence

Detailed local records are under `evidence/results/core-reviewed/`.

| Harness | Baseline | Extended cases | Evidence agreement |
| --- | --- | --- | --- |
| Codex 0.154.0 | 12 assertions pass | 23/23 pass | pass |
| Copilot 1.0.83 | 11 assertions pass | 23/23 pass after one targeted network retry | pass |
| Claude 2.1.229 | unverified | skipped by maintainer request | not eligible |

The 46 extended outcomes contain 38 native-pass records and eight full
adapter-refusal records. The two skills lifecycle cases also include refusal
phases. These counts do not establish complete native capability support.

Copilot initially had eight failing cases after network timeouts to GitHub and
the model service. All eight passed one targeted retry with the same binary
and test sources. The original 23 records and baseline are retained under
`core-reviewed/attempts/copilot-network-failures/`. Earlier behavioral skill
failures remain in `core-1.0/`. Superseded, interrupted runs remain separately
in `core-final/`; they are not complete acceptance runs.

Tested CLI SHA-256:
`1efffa51ee8029b6c102a558665dce24e821bc57973196efabcf9ba99ada4c5f`.

These records come from uncommitted source changes and cannot satisfy the
clean-source release gate. They record source commits and dirty state and
include immutable runner/helper snapshots. The tested CLI source archive,
compiler version, flags, and environment are saved under `reproduction/`.
Rebuilding with the original superproject/submodule layout produced the exact
same binary hash. A standalone CLI checkout embeds different Go VCS metadata.
Only descriptive compatibility text changed after that source snapshot;
projection and validation behavior are unchanged. Current descriptions were
checked separately for registry/Markdown/CLI agreement.

The local archive `evidence/results/core-reviewed-evidence.tar.gz` contains the
results, retained failed attempt, tested binary, source inputs, and this report.
Its SHA-256 file is beside it. The local bundle is not public release evidence.
The report copy inside that original archive has incorrect harness version
labels. The version labels above were corrected from the saved baseline
metadata before commit; the raw results and original archive are unchanged.

## Remaining release blockers

1. Claude native verification is skipped at the maintainer's request because
   no account/subscription is available. The pinned executable is installed
   and version-checked, but its baseline and eleven extended cases remain
   unverified. The existing three-adapter release policy is unchanged.
2. Codex stdio references and both harnesses' unselected skill content remain
   explicit refusals, not complete native capability support.
3. Earlier Copilot selected-skill failures and the reviewed network failures
   remain part of the evidence history. The reviewed retry passes, but it does
   not erase those records or justify full support promotion.
4. All three registry rows remain below the existing supported status gate.
   No public evidence URL or checksum is invented to bypass that gate.
5. Public evidence publication and release publication have not been performed.
6. A local root `v1.0.0` tag already exists at
   `422c8596cfc684720e5c3214048de1568d4f896b`. It is unchanged. Verify remote
   release state and choose an unused root identifier before future release.

Changes are separate in the root, CLI, and Workbench repositories. SPEC is
unchanged. The native evidence records the pre-commit state. A later commit
does not convert those records into clean-source release evidence. No tag,
push, or publication was performed during this review.
