# Extended native test results

This is the earlier native snapshot. See [the core completion review](CORE_COMPLETION.md)
for the subsequent refusal fixes, reviewed runs, and current release blockers.

The evidence audit confirms complete coverage for 46 vendor/case combinations:
37 native passes, 6 expected adapter refusals, and 3 native failures. No case
has missing or incomplete evidence. Complete coverage does not mean full
conformance. Neither adapter is promoted to supported status.

Codex changed from 0.153.4 to 0.154.0 in this cycle. Its baseline and all 23
extended cases were rerun. Copilot kept version 1.0.83; only its four-phase
`profile-tools` case was rerun. Other Copilot results remain from the prior
run on that same version. Earlier Codex records are preserved in `attempts/`.

## Test scope

- Codex 0.154.0 and Copilot 1.0.83, pinned to the latest npm releases checked on 2026-09-10 (Europe/Rome).
- Linux, with existing CLI logins copied into private temporary runtime directories.
- Copilot trusts only the test fixture directories. Normal user configuration is unchanged.
- The baseline suite is recorded in [LATEST_NATIVE_TESTS.md](LATEST_NATIVE_TESTS.md).
- This suite tests portable profiles and adapter refusal boundaries. It does not test all vendor features or other operating systems.
- Refresh tests cover a new invocation and a resumed session. They do not establish continuous reload during an active turn.

## Tools-profile cleanup

The shared CLI now reconciles MCP entries when `tools` is selected or previous
MCP ownership exists. Removing the profile uses an empty desired server map
without reading the canonical catalogue. Only owned entries are removed.
Modified owned entries block removal unless forced. Backup and transaction
rollback remain active. An absent native file is not created during cleanup.

The four native phases pass on Codex 0.154.0 and Copilot 1.0.83: initially
unselected, enabled, removed, and enabled again. Each native process returned
success. The unselected and removed phases recorded no server startup or tool
call. Earlier failed records remain in `attempts/`.

Deterministic tests cover all three CLI adapters, including preservation of
unowned servers and unrelated settings, absent catalogues, conflicts, forced
removal with exact backups, missing native files, repeated apply, and rollback
across vendors. Claude native testing remains deferred.

For repositories where earlier removal already cleared ownership, follow the
[reviewed reapplication procedure](../../CLI/README.md). Do not infer ownership
from server names.

## Confirmed failures

1. **Skills profile selection, both harnesses:** native discovery reads canonical `.agents/skills` when the skills profile is absent or removed.
2. **Missing stdio environment reference, Codex:** the missing variable does not prevent MCP server activation and a tool call.

## Network interruption and retries

The earlier `resumed-resources` and `remote-missing-env` Codex attempts reached
a 240-second request timeout. The user reported a network outage. That outage
is a possible cause; the test evidence does not establish the cause. Both
cases passed on retry with a 600-second request limit. Their current records
contain the required assertions and source snapshots. The earlier attempts
remain under `results/extended/codex/attempts/`.

## Results by case

`native-pass` means the native observations satisfy the case assertions.
`adapter-refusal` means an unsupported mapping was refused before writes;
it does not establish native support. `native-failure` means the case completed
and a required behavior failed.

| Case | Codex 0.154.0 | Copilot 1.0.83 |
| --- | --- | --- |
| remote-https | [native-pass](results/extended/codex/remote-https.json) | [native-pass](results/extended/copilot/remote-https.json) |
| remote-auth-env | [native-pass](results/extended/codex/remote-auth-env.json) | [adapter-refusal](results/extended/copilot/remote-auth-env.json) |
| stdio-env-argv | [native-pass](results/extended/codex/stdio-env-argv.json) | [adapter-refusal](results/extended/copilot/stdio-env-argv.json) |
| hook-lifecycle | [native-pass](results/extended/codex/hook-lifecycle.json) | [native-pass](results/extended/copilot/hook-lifecycle.json) |
| hook-matchers | [native-pass](results/extended/codex/hook-matchers.json) | [native-pass](results/extended/copilot/hook-matchers.json) |
| hook-timeout | [native-pass](results/extended/codex/hook-timeout.json) | [native-pass](results/extended/copilot/hook-timeout.json) |
| instruction-precedence | [native-pass](results/extended/codex/instruction-precedence.json) | [native-pass](results/extended/copilot/instruction-precedence.json) |
| skill-resources | [native-pass](results/extended/codex/skill-resources.json) | [native-pass](results/extended/copilot/skill-resources.json) |
| profile-tools | [native-pass](results/extended/codex/profile-tools.json) | [native-pass](results/extended/copilot/profile-tools.json) |
| profile-skills | [native-failure](results/extended/codex/profile-skills.json) | [native-failure](results/extended/copilot/profile-skills.json) |
| untrusted | [native-pass](results/extended/codex/untrusted.json) | [native-pass](results/extended/copilot/untrusted.json) |
| refusal-boundaries | [adapter-refusal](results/extended/codex/refusal-boundaries.json) | [adapter-refusal](results/extended/copilot/refusal-boundaries.json) |
| subagent-events | [native-pass](results/extended/codex/subagent-events.json) | [native-pass](results/extended/copilot/subagent-events.json) |
| permission-request | [native-pass](results/extended/codex/permission-request.json) | [native-pass](results/extended/copilot/permission-request.json) |
| tool-denial | [native-pass](results/extended/codex/tool-denial.json) | [native-pass](results/extended/copilot/tool-denial.json) |
| compaction | [native-pass](results/extended/codex/compaction.json) | [native-pass](results/extended/copilot/compaction.json) |
| resumed-refresh | [native-pass](results/extended/codex/resumed-refresh.json) | [native-pass](results/extended/copilot/resumed-refresh.json) |
| missing-env | [native-failure](results/extended/codex/missing-env.json) | [adapter-refusal](results/extended/copilot/missing-env.json) |
| profile-hooks | [native-pass](results/extended/codex/profile-hooks.json) | [native-pass](results/extended/copilot/profile-hooks.json) |
| hook-exit-codes | [native-pass](results/extended/codex/hook-exit-codes.json) | [native-pass](results/extended/copilot/hook-exit-codes.json) |
| resumed-resources | [native-pass](results/extended/codex/resumed-resources.json) | [native-pass](results/extended/copilot/resumed-resources.json) |
| remote-missing-env | [native-pass](results/extended/codex/remote-missing-env.json) | [adapter-refusal](results/extended/copilot/remote-missing-env.json) |
| stdio-argv | [native-pass](results/extended/codex/stdio-argv.json) | [native-pass](results/extended/copilot/stdio-argv.json) |

## Observed behavior

All ten canonical hook events passed on both harnesses, including subagent
events and compaction. Codex compaction used the terminal `/compact` command.
Copilot compaction used `/compact` in a resumed session.

The one-second hook timeout was observed at about 0.974 seconds for Codex and
0.984 seconds for Copilot. Codex allows the tool after hook exit code 1;
Copilot denies it. Both deny the tool after exit code 2. The exit-code case
checks these declared native differences.

Other passing cases include HTTPS with certificate verification, literal
stdio arguments and paths with spaces, instruction precedence and sibling
isolation, skill scripts/references/assets, hook matchers, permissions,
untrusted projects, and resumed configuration changes. Copilot environment
reference mappings remain explicit adapter refusals.

## Evidence and reproduction

JSON links in the table refer to ignored local artifacts. They are available
in this checkout and are not included in Git. Each record contains commands,
checks, observations, transcripts, version data, and source hashes. Exact
runner/helper snapshots are under `results/extended/sources/`. Earlier
attempts are retained in each vendor's `attempts/` directory.

Run the coverage audit from the repository root:

```sh
python3 WORKBENCH/conformance/summarize_extended.py --coverage-only --json
```

The coverage audit passes. The full functional audit below returns failure
because the three confirmed failures remain:

```sh
python3 WORKBENCH/conformance/summarize_extended.py --json
```

Evidence interval (UTC): 2026-09-09T21:57:09.469815+00:00 to 2026-09-09T23:20:48.150685+00:00.

The initial cleanup runs use reference CLI SHA-256
`5b78db86f29997ed37e6acc7646b645de7eddff2a5914ac690f3fe427d5fc8a1`.
After native verification, the CLI capability evidence text was updated to
record the result. The cleanup implementation did not change. Its `apply.go`
SHA-256 is `a1a82ffc3f53a95705e1a92bc5ecf6e85626e302bca3b5e52beb78388c518861`.
Go and Workbench tests passed again after the capability text update.

Before commit, both four-phase `profile-tools` cases passed again with the
complete CLI changes, including the capability evidence text. These reruns
used CLI SHA-256 `a7701e0e6ab275888a78e463255611a61378648beefbb6638be26d702a5c7387` and the installed Codex 0.154.0 and Copilot 1.0.83.
Earlier runs remain in `attempts/`. The other extended cases were not rerun
for this commit check. The coverage audit still passes; the three known
functional failures remain.

## Repository verification

Final checks passed: 59 Workbench tests, 25 specification conformance checks,
Go tests for all CLI packages, canonical repository validation, compatibility
data checks, edited JSON parsing, and whitespace checks in the root and all
three component worktrees. These checks do not replace native evidence.
