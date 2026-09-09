# Baseline native test results

For the later extended tests and known conformance failures, see
[the extended report](EXTENDED_NATIVE_TESTS.md).

Both priority harnesses passed the local native suite on Linux with existing
CLI logins. The evidence validator also passed. These results cover the
listed fixture behaviors; they do not establish complete profile support.

| Harness | Version observed | Completed (UTC) | Result |
| --- | --- | --- | --- |
| codex | 0.154.0 | 2026-09-09T23:07:26.796330+00:00 | PASS |
| copilot | 1.0.83 | 2026-09-09T21:21:46.972402+00:00 | PASS |

The earlier Codex 0.153.4 baseline remains in [its preserved record](results/codex-0.153.4.json).
Copilot has the same version pin; its baseline was not rerun in this cycle.

## Passed behaviors

| Check | Codex | Copilot |
| --- | --- | --- |
| Root instruction use | PASS | PASS |
| Nested instruction use | PASS | PASS |
| Portable skill use | PASS | PASS |
| Stdio MCP startup and successful native tool calls | PASS | PASS |
| SessionStart command hook | PASS | PASS |
| PreToolUse command hook | PASS | PASS |
| Deactivation followed by a successful MCP call with no hook markers | Profile removal: PASS | Catalogue disablement: PASS |

The UserPromptSubmit marker was also observed in the enabled runs. The suite
does not require a separate assertion for that event.

## Conditions and evidence

- Codex used a temporary copy of its saved ChatGPT login. Copilot used its
  saved login with a private temporary configuration.
- Only the temporary Copilot fixture folders were added to trustedFolders.
  Both harnesses ran with automatic tool approval for the fixture commands.
- No API key or manually exported token was required. Temporary login and
  runtime copies were removed when each run ended.
- MCP results require successful native tool-call events. Direct shell writes
  to marker files cannot satisfy that check.
- JSON records and bounded transcripts are local, ignored artifacts:
  [Codex](results/codex.json) and [Copilot](results/copilot.json).
- The installed harness versions were checked with --version. Package
  integrity fields identify the selected npm releases; these local runs are
  not a signed release or installed-package attestation.

## Source and binary identity

- codex runner SHA-256: `acd25fc42c906142fbfb04494516b35f3c83f69fa8918984b6ba73fea91cbcf0`
- codex reference CLI SHA-256: `5b78db86f29997ed37e6acc7646b645de7eddff2a5914ac690f3fe427d5fc8a1`
- copilot runner SHA-256: `acd25fc42c906142fbfb04494516b35f3c83f69fa8918984b6ba73fea91cbcf0`
- copilot reference CLI SHA-256: `80aef7e3f18bd74a5167945c1ac0b875d83d5f56b856790f57b6d173d1cabbce`

## Not established by these runs

- Remote MCP connections and authentication.
- Runtime resolution of portable environment or secret references.
- All hook events, general matcher equivalence, or timeout enforcement.
- Conflicting instruction precedence, every skill resource pattern, or
  native configuration reload behavior.
- Permission enforcement or behavior in untrusted repositories.
- Claude, other operating systems, or older harness versions.

The compatibility registry keeps not-conformance-supported until broader
coverage and the required evidence are complete.

## Runner corrections found during testing

The original preflight accepted only environment tokens and did not use saved
CLI logins. Initial runs also encountered read-only native state directories.
Temporary runtime directories resolved that constraint. Copilot initially
skipped project MCP and hooks because the disposable repository was not
trusted. Adding trust in its temporary configuration resolved that failure.

Deterministic validation after the corrections: 53 Workbench tests, all Go
tests, 25 specification checks, compatibility consistency, and workflow lint.
