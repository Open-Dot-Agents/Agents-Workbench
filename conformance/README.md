# Native Adapter Conformance

This harness creates an isolated repository with canonical
`.agents/AGENTS.md` instructions and a root compatibility link, applies one
reference adapter, and runs the exact pinned native harness. The harness must
discover root and nested instructions, project and execute at least one hook,
start the marker MCP server, invoke its tool, and discover the portable skill.
Evidence records exact package provenance, platform, commands, markers, and
bounded transcripts.

Native runs default to `--auth auto`. They use an accepted environment token
when present; otherwise Codex and Copilot use the existing CLI login. Use
`--auth existing-login` to select your saved login, or `--auth environment`
for a CI run that must use an environment credential.

Existing-login runs use private temporary state directories. The runner copies
Codex's `auth.json` or Copilot's JSONC `config.json` without printing or extracting
tokens. Files have mode 0600 and directories have mode 0700; the temporary tree
is removed at the end. The user's login and runtime state are not modified.
Codex setups that store authentication only in a system keyring are not covered
by this file-based login test. Copilot can also use its system credential store.

The Copilot temporary configuration trusts only the generated fixture folders.
This is required for project MCP loading in prompt mode. User-level hooks are
omitted from that temporary configuration so they cannot affect the test.
Codex runs with explicit hook trust bypass for the generated fixture commands.
These are trusted-repository tests, not tests of permission enforcement.

Copilot accepts `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN`; Codex
accepts `OPENAI_API_KEY` or `CODEX_ACCESS_TOKEN`; Claude accepts
`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, or `CLAUDE_CODE_OAUTH_TOKEN`.
Environment authentication remains available for CI. With `CODEX_ACCESS_TOKEN`,
the runner imports the token into a temporary Codex home.

By default the harness resolves `copilot`, `codex`, or `claude` from `PATH`.
Set `COPILOT_BIN`, `CODEX_BIN`, or `CLAUDE_BIN` to use a specific pinned
binary; the preflight record stores the resolved path in `metadata.harnessPath`.

To verify local prerequisites without running an agent, use `--preflight-only`.
This writes an adapter evidence record with `metadata.runMode` set to
`preflight`. It fails when the selected prerequisites are missing. Copilot has no separate
non-interactive login-status check; a passed existing-login preflight defers
authentication verification to the actual native requests:

```sh
AGENTS_BIN=/path/to/agents python3 conformance/run_adapter.py codex \
  --preflight-only --output evidence/results/codex-preflight.json
```

The Workbench task wrapper uses the same command:

```sh
AGENTS_BIN=/path/to/agents task native:preflight VENDOR=codex
```

```sh
AGENTS_BIN=/path/to/agents CODEX_BIN=/path/to/codex \
  task native:preflight VENDOR=codex
```

```sh
AGENTS_BIN=/path/to/agents CODEX_BIN=/path/to/codex \
  CODEX_ACCESS_TOKEN=... task native:run VENDOR=codex
```

```sh
AGENTS_BIN=/path/to/agents python3 conformance/run_adapter.py codex \
  --output evidence/results/codex.json
```

```sh
AGENTS_BIN=/path/to/agents task native:run VENDOR=codex
```

```sh
AGENTS_BIN=/path/to/agents COPILOT_BIN=/path/to/copilot \
  CODEX_BIN=/path/to/codex CLAUDE_BIN=/path/to/claude \
  task native:run:all
```

```sh
task native:validate RESULT=evidence/results/codex.json
```

Only records with `metadata.runMode` set to `native` can be used for adapter
support review. Passing preflight records prove local prerequisites only.

Never mark an adapter supported from the deterministic projection suite or
from a native configuration listing alone.

## Version selection

Test the latest stable Codex and Copilot releases. Before a new native test
cycle, resolve each official npm package's `latest` tag and update
`versions.json` with that exact version and its `dist.integrity`. Update the
compatibility registry and CLI version declarations at the same time.
Keep the resolved versions fixed during a run so evidence remains reproducible.
Do not maintain a test matrix of older Codex or Copilot versions.

The latest tags checked on 2026-09-10 (Europe/Rome) resolve to Codex 0.154.0 and Copilot
1.0.83. Claude remains a separate, optional target.

## Current test priority

Codex and Copilot are the default native test group:

```sh
AGENTS_BIN=/path/to/agents task native:preflight:priority
AGENTS_BIN=/path/to/agents task native
```

`task native:run VENDOR=claude` selects Claude explicitly.
`task native:run:all` retains the complete three-harness run. The root native
workflow defaults to `priority` for manual runs; select `all` or `claude` when
needed. Evidence tags retain the complete group. No workflow is dispatched by
local deterministic tests.

Run `task verify` for all deterministic tests, including Claude regressions.
These tests require Python 3, the Spec conformance requirements, Go, and sibling
CLI and Spec checkouts. They do not require native credentials.

## Hook evidence

A passing native result now requires separate session-hook and tool-hook
markers. The MCP marker tool cannot write hook markers. The disabled case
requires a successful instruction-driven MCP call with no hook marker, so a
failed or idle harness cannot pass the negative check.

For Copilot, the disabled case sets `disableAllHooks: true` after an initial
apply. For Codex and Claude, it verifies refusal with no file changes, removes
the hooks profile, and applies again before starting the harness. Evidence
records the mode as `catalogue-disabled` or `profile-removal` in
`metadata.disabledHooks`, with markers, return code, and a bounded transcript.
Older passing records without these checks must be rerun. Runtime failures
produce failed evidence; failed CI runs retain available evidence artifacts.

## Evidence limits

Passing markers must match successful native MCP tool-call events. A shell
command that writes the same marker cannot satisfy an MCP check. Records
include exact commands, runner and CLI SHA-256 values, sanitized bounded
transcripts, and native MCP markers. Do not count preflight as feature evidence.

`evidence_state.py` keeps two results separate. Historical artifact integrity
checks the receipt against its captured runner and declared captured files.
Current support eligibility also checks the tracked runner, implementation,
and helper files. An intact historical receipt cannot satisfy the release gate
until a current pinned run replaces it.

Current eligibility requires a current runner path, a nonempty implementation
source map, and current paths for every declared helper. An omitted comparison
cannot pass. These omissions do not invalidate intact historical artifacts.
The summary includes `receipt_count`; an empty set returns false for both
historical integrity and current eligibility. Family-specific verifiers must
also check native results, binary pins, and the required behavior assertions.

The current suite covers root/nested instruction use, one portable skill,
stdio MCP, session/tool hooks, and deactivation. It does not establish remote
MCP, runtime environment-reference resolution, all hook events, matcher engine
compatibility, timeout enforcement, instruction precedence in conflicts, or
permission enforcement. No adapter is promoted on these tests alone.

## Extended native suite

Install `conformance/requirements.txt` and provide a built `AGENTS_BIN`.
Then run `task native:extended` to test both latest pinned targets, or
`task native:extended:run VENDOR=codex AUTH=existing-login` for one target.
Use `python3 conformance/run_extended.py codex --case hook-timeout` to repeat
one case. Local OpenSSL and loopback sockets are required for HTTPS cases.
Codex's manual compaction case drives the real terminal with `pexpect`.

The extended suite covers 23 cases per target, including expected adapter
refusals. Each case records its assertions, commands, native events, server
observations, source snapshots, and reference CLI hash. Test helpers are copied
from the captured source snapshot into private temporary directories before
execution. HTTPS cases use a private CA and separate server certificate;
certificate verification stays enabled. Test header and environment values
are synthetic. They are separate from the saved CLI login used for model access.

Results are under `evidence/results/extended/<vendor>/`. Reruns preserve the
previous record under `attempts/`; source snapshots are under `sources/`.
`task native:extended:audit` returns failure if any behavioral check fails.
`python3 conformance/summarize_extended.py --coverage-only` checks whether all
required cases and assertions have evidence, including recorded failures.
Coverage completion is not functional success or full adapter support.

Codex subagent cases use persisted sessions: ephemeral mode could not spawn a
child thread in the tested build. Copilot prompt-mode compaction uses `/compact`
on a resumed session. The suite tests restart/resume refresh, not continuous
hot reload while an agent turn is running. Non-portable features and other
operating systems are outside these portable-profile cases.

## Core completion evidence

The extended runner supports Codex and Copilot's 23 cases and eleven Claude
cases: HTTPS MCP, authenticated HTTPS MCP, stdio environment references,
instruction precedence, skill resources, tools and skills profile lifecycles,
missing stdio references, hooks profile lifecycle, missing remote references,
and literal stdio arguments. Other Claude extended behaviors remain untested.

Codex and Copilot skills-profile tests require refusal for the initially off
and removed phases, then native skill use for enabled and enabled-again phases.
Codex stdio reference cases require refusal. These outcomes are adapter refusal
evidence, not proof of native deactivation or complete capability support.

```sh
AGENTS_BIN=/absolute/path/to/agents python3 conformance/run_adapter.py codex --output evidence/codex.json
AGENTS_BIN=/absolute/path/to/agents python3 conformance/run_extended.py codex --output evidence/extended
python3 conformance/release_gate.py --evidence-dir evidence --vendors codex
```

Run each harness separately. Use `--auth environment` in CI. Claude requires an
accepted environment credential; never place its value in a result file or
command argument. Its test configuration is isolated in a temporary directory.

The release gate requires all three harnesses by default. `--release` also
requires clean source checkouts, exact source commits, and release version
1.0.0. Source snapshots and package pins are included in evidence bundles.
Workflow artifacts expire after 90 days; reviewed release archives provide
the durable public copy. No local run proves that such a copy was published.

## Security draft evidence

`run_security.py --agents /path/to/agents --result-dir /new/evidence/path
--native-probe` records draft refusals and direct local Codex sandbox probes.
Run it on Linux. It needs no model request or credential. It does not install
packages or change trust. The result directory must be new.

This runner declares `supported: false` and `enforcement_pass: false`: it does
not supply the explicit native-home context for Codex subset activation. Its direct native observations
are separate from the unrun adapter enforcement matrix. Claude is skipped by
user request. Keep this evidence separate from the stable release gate.

## Codex shell subset and Copilot assessment

`run_codex_security.py --agents /path/to/agents --result-dir /new/evidence/path`
projects the Codex Linux subset before native checks. It uses no model account,
creates trust only for its isolated fixture, and reports enforcement, refusal,
and lifecycle results separately. The earlier `run_security.py` still tests
refusals without the new explicit native-home context.

`run_copilot_security.py --slirp4netns /path/to/slirp4netns --result-dir /new/path`
assesses native settings with an approved `COPILOT_GITHUB_TOKEN` in its process
environment. It makes a model request and does not test portable projection.
It never archives raw native homes or logs. See the
[Codex report](../evidence/CODEX_SECURITY_SUBSET.md),
[Copilot report](../evidence/COPILOT_SECURITY_ASSESSMENT.md), and
[scenario record](../evidence/SECURITY_SCENARIOS.json). These are separate from
stable release support gates.

## Native model-tool approvals

`run_native_approvals.py --vendor codex|copilot --result-dir /new/path` uses a
native app-server or ACP client to test explicit allow, explicit deny, and
unattended tool requests. `--mode` selects one case. It makes model requests;
Copilot needs an approved token in the process environment, while Codex uses
an isolated copy of its existing login. Credentials are never archived.

Native tool events must agree with marker state. Model claims and marker
absence alone are insufficient. A nonzero result can mean a policy mismatch
or missing evidence; inspect `status` and `assessment_complete`. These tests
are not a runtime launcher installed by the reference CLI. See
[the assessment](../evidence/ISOLATION_APPROVALS.md).

## Development workflow regression

Run this campaign from the superproject root. It uses the Codex and Copilot
executables currently installed on `PATH`. To select a specific installed
executable, set `CODEX_BIN` or `COPILOT_BIN` to its absolute path.
At startup, the runner copies each executable into a private directory. It
checks the source and copy hashes before execution, then records the exact
version and SHA-256 hash. Preview versions such as `1.0.84-9` are accepted.
Each session must use the same private copy. Copilot auto-update is disabled
for that copy. The runner does not install, upgrade, or downgrade a harness.

```sh
(cd CLI && GOCACHE=/tmp/agents-gocache GOPATH=/tmp/agents-gopath GOFLAGS= \
  go build -trimpath -buildvcs=false -o /tmp/agents-workflow ./cmd/agents)
python3 WORKBENCH/conformance/probe_development.py \
  --vendor all --agents-cli /tmp/agents-workflow \
  --output /tmp/agents-development-workflows.json
python3 WORKBENCH/conformance/verify_development.py \
  /tmp/agents-development-workflows.json --require-current
```

`--vendor codex` and `--vendor copilot` select one harness. The output path
must be new. The earlier `probe_development_codex.py` command is unchanged.
The supplied CLI must match a fresh build with the flags shown above. The
runner also executes the clean-source gate. It uses disposable Git projects,
native homes, dummy protected data, and a local model server. It needs local
sockets, the installed executables, Python, Git, and the Go toolchain. It copies no
account credentials and changes no real user configuration. Native trust is
set only for the disposable projects in their disposable native homes.
The fixture retains an existing Codex command rule and Copilot saved
permissions. It compares their bytes before and after each adapter command.
This proves preservation by the adapter, not native activation of each grant.
User-scope checks compare all selected user files around each project command.
Native sessions capture configuration before and after execution separately:
Copilot can migrate settings into its managed state file. These native changes
are retained as observations and are not attributed to adapter commands.
The Copilot fixture uses the documented
[saved-permissions file](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference#permissions-configjson).

The shared cases cover creation, adoption, update, repeated apply, conflict,
removal, rollback, project/user interaction, and relocation of canonical
content. Adoption preserves the canonical `AGENTS.md` link and emits Copilot
development guidance in a separate native instruction file.
Each phase starts a new native process. Copilot tests instruction
composition and removal; they do not claim model-setting precedence. Codex
also tests legacy migration, edits, tests, builds, formatting, local commits,
submodule commits, existing and future protected paths, aliases, local
network denial, and a read-only session override. The rollback fixture uses
an internal Go test hook; the public CLI has no failure-injection option.

Codex can leave an empty read-only placeholder for a protected path that did
not exist. The future-path case records its type, mode, and size. After the
native process closes, the fixture replaces only that empty regular file with
dummy data, then starts a new session to test denial. It refuses to replace
nonempty files, directories, symlinks, or hard links. This fixture step does
not remove the native side effect or change the permission policy.

Exit status is `0` for a current passing campaign, `1` for failed checks, and
`2` for an unavailable prerequisite. Each case has a separate result.
Failures take precedence when failures and unavailable prerequisites coexist.
Inspect the receipt for the failure or prerequisite reason. A host that cannot create
local sockets cannot run this campaign. Change the test execution context;
the runner does not change host permissions.

Keep the JSON receipt, its `.runner.py` file, and its `.artifacts` directory
together. They contain source snapshots and hashes, disposable configuration,
native request/event records, observed effects, and clean-source results.
The verifier uses the shared evidence integrity checks. It checks every
required phase and compares the complete source inventory with current files.
Old evidence can retain valid historical integrity without qualifying the
current implementation. Results apply only to the recorded native versions
and hashes; they do not qualify later system updates. Synthetic tests never
qualify as native evidence.

Push, script deletion, and history rewrite cases are observations of
guidance-only decisions. The local fixture model deliberately emits those
commands to expose native limits. All remotes and files are disposable.
These cases never count as enforced operation approval. A passing campaign
does not establish complete adapter conformance or strict-policy support.
The practical preset remains project-scoped. Publication is a separate step.
