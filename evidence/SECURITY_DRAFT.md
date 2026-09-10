# Experimental security evidence, 2026-09-10

The reference CLI validates and normalizes the 1.1.0-draft.1 proposal. Native
security activation is refused. These results do not support an adapter or a
release claim. The stable 1.0 contract and its release gates remain unchanged.

This is the initial, historical report. The later
[Codex subset report](CODEX_SECURITY_SUBSET.md),
[Copilot assessment](COPILOT_SECURITY_ASSESSMENT.md), and
[60-scenario record](SECURITY_SCENARIOS.json) describe the next phase.
The results below retain their original scope and counts.

## Results

| Evidence class | Result | Limit |
| --- | --- | --- |
| Stable specification | 25/25 checks pass | Existing 1.0 fixture validation. |
| Draft specification | 53/53 cases pass | Schema and strict JSON semantics only. |
| Go CLI | All tests pass | Includes decision precedence, exact arguments, unattended ask, overlaps, symlinks, import/export refusal, removal, and idempotence. |
| Workbench | 67 tests pass | Includes shared draft fixtures checked through the public CLI and result schemas. |
| Adapter refusal | 8/8 checks pass | Codex and Copilot plan/apply/sync/import refuse without writes, including conflicting config and force/backup requests. |
| Direct Codex Linux sandbox | 8/8 observations match expected native behavior | Direct native command, no portable policy projection. |
| Portable security enforcement | 60 scenarios not run | Both security adapters refuse activation. |
| Copilot native sandbox | Not run | No direct probe adapter; Linux prerequisite `slirp4netns` is missing. |
| Claude native | Skipped | User has no account or subscription. |

Repository validation, compatibility checks, JSON checks, whitespace checks,
and Actionlint 1.7.7 also pass. No package was installed and no native trust,
managed policy, or credential store was changed.

The direct Codex tests observed workspace reads and writes, outside reads,
outside write denial, symlink and traversal write denial, read-only write
denial, and loopback connection denial. Actual marker files were checked after
the process ended. The loopback listener was reachable from the host before
the sandbox check. Outside reads were **allowed** by the native workspace
preset. This is a documented limit, not evidence of draft read isolation.

The 60 unrun adapter scenarios include allow/deny execution, approval refusal,
redirect and private network targets, web tools, local/remote MCP, LSP,
delegation, background processes, credential exposure, conflicting settings,
bypass settings, prerequisites, removal, rollback, and idempotence. Some have
unit coverage; none are claimed as portable native enforcement.

## Exact inputs

- OS: `Linux-7.0.0-31-generic-x86_64-with-glibc2.43`.
- Codex: 0.154.0; binary SHA-256
  `3188814c35471432d4123203e0eb38e5bddc60226e3d7ddf0e59e649ea140022`.
- Copilot: 1.0.83; binary SHA-256
  `a3262c4513ef1fc2ca21485261ca73196977ad76bd5e7990fb572f6134aaeedd`.
- Reference CLI SHA-256:
  `23905b40a3987891a04a6a65601802938b4e56b47c67c3c885ddee46d2a94513`.
- Native runner SHA-256:
  `c714a8db1fbb2a9e6c14746b661ee97ea3dfa63c79f60c3e4cc8b3e0039af37c`.
- Result JSON SHA-256:
  `d8dd5e13e9068314154107d46a66e6ca3b3d03c21bfcfb68cc225071a7704409`.

Source commits before the uncommitted changes: root `b83f020e2fa2c2bc672907c3b0ae50f2ca1cbea1`,
CLI `79d60def85a5e19a5424c6d5840b5bdd3b6fd6b0`, SPEC `279a67bb8bc82e1af074308a1d1eb6cb802323d2`, and WORKBENCH
`b256f2cc647eac452c8fd5882361144d57faeac3`. All four source trees were dirty at test time.
This is local development evidence. No commit or publication is implied.

## Reproduction and retained artifacts

Run from the superproject:

```sh
cd CLI
go build -o bin/agents-security-draft ./cmd/agents
cd ..
python3 WORKBENCH/conformance/run_security.py \
  --agents CLI/bin/agents-security-draft \
  --result-dir /mnt/DATA/tmp/oda-security-new-run --native-probe
```

The result directory must be new. The runner uses harmless local fixtures and
an isolated Codex configuration. It makes no model request and uses no account
credential. Only the local loopback listener is used for network observation.
A direct native command is test tooling, not a runtime launcher installed by
the reference CLI.

The ignored local directory `evidence/results/security-draft-final/` contains
the result, command output, validation logs, source references, fixture
snapshots, tested CLI binary, source snapshot, and build metadata. Its archive
is `evidence/results/security-draft-final.tar.gz`; the adjacent `.sha256` file
records its checksum. Earlier `security-draft` and `security-draft-reviewed`
runs are retained separately. No failed or superseded run was replaced.

Source files can be restored over the four recorded commits in the original
superproject/submodule layout. Preserve the dirty source state and use the
recorded Go toolchain for a rebuild. No bit-for-bit rebuild was performed in
this run. The saved tested binary is identified by its hash above.

The inventory uses live official references and pinned native help. Source
hashes and original text are in the bundle. These references can describe
features newer than the installed harnesses. No secret values or user
configuration stores are included in the source corpus.
