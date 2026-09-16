# Development workflow regression status

The 2026-09-16 campaign passed all 28 required cases for the recorded Codex
and Copilot executables. The current-source verifier and clean-source gate
also passed. Five additional Codex cases record guidance-only effects.

| Harness | Version | Required cases | Observations |
| --- | --- | --- | --- |
| Codex | 0.154.0 | 20 passed | 5 |
| Copilot | 1.0.84-9 | 8 passed | 0 |

The local evidence bundle is retained under
[`results/development-workflows-2026-09-16/`](results/development-workflows-2026-09-16/).
It contains the unchanged receipt, runner, source snapshots, native request
records, configuration, and observed effects. Raw evidence is ignored by Git;
it has not been published. The original receipt remains at
`/tmp/agents-workflow-check-gw09iktf/results.json`.

Receipt SHA-256:
`19a5e02b7ce369d845a07cf2de18b991bc2ecc0321343540c7f32bd523e958bd`.

Native executable SHA-256 hashes:

- Codex: `3188814c35471432d4123203e0eb38e5bddc60226e3d7ddf0e59e649ea140022`.
- Copilot: `905a39134b45d1644bcf79c1db1ca58387515207cb2cb07ff8241e871687f57a`.

The verified receipt reports `historical_integrity: true`,
`workflow_checks_passed: true`, and `current_workflow_eligible: true`.
It reports no failed checks, stale sources, or unavailable prerequisites.
`full_adapter_support` remains `false`: these bounded workflow results do
not establish complete adapter conformance or enforced operation approval.

The shared runner is `conformance/probe_development.py`. It defines 25 Codex
cases and eight Copilot cases. Five Codex cases record guidance-only operation
effects. Those five cases cannot prove enforced approval. The earlier Codex
probe remains available with its existing arguments.

The runner uses the currently installed executables. It makes private copies
at startup and records their exact versions and SHA-256 hashes. Results apply
only to those versions and hashes. This work does not upgrade a native
harness or change an adapter support claim. See the
[run instructions](../conformance/README.md#development-workflow-regression).

## First retained native run

The local receipt is `/tmp/agents-workflow-check-nz2fwyi5/results.json`.
Its SHA-256 is
`1966e3ba19efd3ae2a44d029b181d6bbd909911ddff9a806444460a007851510`.
The receipt and its captured source files retain valid historical integrity.

- The complete clean-source gate passed in the user's terminal.
- Codex 0.154.0 passed 19 cases. Five additional cases recorded guidance-only
  operation effects. They do not establish enforced approval.
- The future protected-path case failed during fixture preparation. Codex
  created an empty mode-0444 file at the previously absent `future.env` path.
  The fixture tried to write that read-only placeholder before the second
  native session could start.
- Copilot did not start. Its pinned binary prints `1.0.83.` with a terminal
  period. The runner incorrectly rejected that version output.

## Second retained native run

The local receipt is `/tmp/agents-workflow-check-fr_uax61/results.json`.
Its SHA-256 is
`3349b43127867a885d00722e62a08d934658b67e0b0c6dd1d0999e2108bdca78`.
The receipt and its captured source files retain valid historical integrity.

- The complete clean-source gate passed in the user's terminal.
- Codex 0.154.0 passed all 20 required cases. This includes the corrected
  future protected-path fixture. Five cases recorded guidance-only effects.
- Copilot 1.0.83 passed creation and update. Adoption failed because the
  adapter skipped development guidance when the root `AGENTS.md` was a
  canonical link.
- The installed Copilot executable changed before the conflict session.
  That case failed; the remaining four cases were unavailable. The retained
  receipt does not qualify a complete Copilot workflow.

## Third retained native run

The local receipt is `/tmp/agents-workflow-check-m7k3_lau/results.json`.
Its SHA-256 is
`861d619568745b41d34499150a7d2c68a7dfe232bf6573bf970b3958f787c519`.
The receipt and its captured source files retain valid historical integrity.

- The complete clean-source gate passed in the user's terminal.
- Codex 0.154.0 passed all 20 required cases. Five cases recorded guidance-only
  effects.
- Copilot 1.0.84-9 passed seven cases, including the corrected adoption case.
  Its user-scope case failed a fixture assertion. The installed binaries did
  not change during the campaign.
- The captured session moved `trustedFolders` from disposable `settings.json`
  into managed `config.json`. The following `agents plan` refused user-scope
  development and left both files unchanged. The fixture compared bytes from
  before native startup with bytes after the plan, so it attributed the native
  migration to the adapter.

## Verified corrections

The adapter now preserves the canonical link and manages development guidance
in `.github/copilot-instructions.md`. A regression test covers apply, repeated
apply, policy updates, removal, and preservation of the canonical instructions.

The runner now selects the installed versions as requested. It uses private
copies so replacement of the installed executable cannot change an active
campaign. It accepts preview version banners and checks the private copy
before each native session. The latest retained campaign used Codex 0.154.0
and Copilot 1.0.84-9.

The future-path fixture records placeholder metadata, then replaces only an
empty regular file owned by the fixture user. It starts a new native session
to test the new dummy file. The permission policy stays unchanged. The report
keeps unavailable prerequisites separate from observed failures, including
unavailable local sockets after native binary selection.

The user-scope fixture now compares complete selected user-file snapshots
before and after each project command. It records native session configuration
before and after execution separately. Regression tests replay the captured
settings migration and reject adapter changes to settings, saved permissions,
and global instructions. The verifier requires the per-command snapshots.

Regression tests reproduced these failures before the corrections. They now
pass. The passing 2026-09-16 campaign verifies the corrected source, including
Copilot adoption and all user-scope phases. Earlier failed receipts remain
historical records and do not qualify the current implementation.

The verifier checks source snapshots and current source hashes through the
shared evidence-state module. It rejects missing phases, stale sources,
missing native command results, missing guidance, and incomplete receipts.
The report keeps historical integrity separate from current eligibility.

The practical preset remains project-scoped. Tests of user defaults use only
disposable native homes. Copilot guidance delivery, saved-permission
preservation, and enforced runtime boundaries are separate claims. Strict
mode and complete adapter conformance remain outside this milestone.
