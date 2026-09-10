# Draft.2 native observations, 2026-09-10

These tests do not establish full adapter support. The CLI projection tests and
native behavior tests are separate evidence classes.

## Codex trust-derived approval

Pin: Codex CLI `0.154.0`, binary SHA-256
`3188814c35471432d4123203e0eb38e5bddc60226e3d7ddf0e59e649ea140022`.

The fixture uses a private native home and a preconfigured untrusted workspace.
It omits `approval_policy` from both configuration and the app-server thread
request. The project configuration has a model sentinel and an explicit `never`
policy. The effective thread reports `approvalPolicy: untrusted` and model
`gpt-6-astra`; the project model sentinel does not take effect.

`2026-09-10-codex-trust-derived/` records an explicit Python execution rule with
`decision="prompt"`. Approval acceptance produced a correlated completed native
tool event and the expected marker. Approval denial produced a correlated
declined event and no marker. The unattended attempt has a native router policy
rejection, but no correlated terminal execution event. Its result stays
`inconclusive`.

`2026-09-10-codex-trust-safe/` and
`2026-09-10-codex-trust-safe-bare/` retain safe-read attempts with absolute and bare
`cat` commands. Raw events contain approval requests and declined command events.
The runner looked for completed reads, so its summaries stay inconclusive.
Neither attempt establishes execution without approval.

`2026-09-10-codex-trust-builtin/` retains an `apply_patch` attempt. It has a declined
`fileChange` event, no marker, and no approval request received by the test client.
The cause of this approval-channel failure is unresolved. Its result stays
inconclusive.

Mandatory portable `ask` remains refused. Timeout, disconnect, shell composition,
and background descendant coverage are not established by these tests.

## Copilot local connection classes

Pin: Copilot CLI `1.0.83`, binary SHA-256
`a3262c4513ef1fc2ca21485261ca73196977ad76bd5e7990fb572f6134aaeedd`.

`2026-09-10-copilot-local-classes/result.json` records the separate local cases.
The native probe source was unchanged. Correlated `tool.execution_start` and
`tool.execution_complete` events identify successful probe execution.

| Connection class | Observation |
| --- | --- |
| Host TCP service | Connection denied |
| Host filesystem Unix socket, with path denial | Connection denied; no host marker |
| Host abstract Unix socket | Connection denied; no host marker |
| Sandbox abstract Unix socket | Connection and data transfer allowed |
| Sandbox IPv4 TCP loopback | Connection allowed |
| Sandbox IPv6 TCP loopback | Connection allowed |
| Sandbox IPv4 UDP loopback | Data transfer allowed |

The host listeners passed reachability checks before the native run. These
observations keep `network.local: deny` refused. Host-service denial does not
establish denial of sandbox-local connections. The result also records filesystem
observations; it does not claim filesystem policy conformance.

Existing native homes, trust stores, account data, and historical evidence were
not modified. Authentication copies remained in private temporary fixture homes;
account files were not added to the evidence directories.

## Deterministic model follow-up

The loopback model fixture uses the Responses event format from Codex's exact
`rust-v0.154.0` test support. It supplies deterministic model output while the
pinned native binary loads configuration and performs discovery, tool routing,
approvals, delegation, and sandbox execution. Every fixture uses a new native
home and workspace. No account credentials are copied.

| Fixture | Native observation | Limit |
| --- | --- | --- |
| [User agent](native-draft2-debug/codex-projected-agent-execution-v2.json) | CLI-projected agent is discovered; the child request uses its instructions, model, and reasoning; a correlated child command creates the expected file | Recorded agent settings and fixture sandbox only |
| [Project agent](native-draft2-debug/codex-projected-project-agent-execution.json) | Same behavior from the trusted project's agent directory; projection leaves user config unchanged | Trust is preconfigured by the test, outside apply |
| [Safe command](native-draft2-debug/codex-local-trust-safe-v2.json) | Untrusted thread requests approval for `cat`; refusal gives a correlated declined command | Earlier contrary expectation is retained in [first attempt](native-draft2-debug/codex-local-trust-safe.json) |
| [Explicit rule allow](native-draft2-debug/codex-local-trust-rule-allow.json) | Approval accepted, correlated command completes, fixture file exists | One exact command rule |
| [Explicit rule deny](native-draft2-debug/codex-local-trust-rule-deny.json) | Approval declined, correlated command is declined, fixture file is absent | One exact command rule |
| [Built-in image read](native-draft2-debug/codex-local-trust-image.json) | `imageView` completes and the image enters the next model request without approval | Refutes mandatory portable `ask` for this native mode; does not test every built-in |

The security cases omit the approval-policy key. The native response reports
`untrusted`; project configuration with a model/approval sentinel is disabled.
The new observations resolve the earlier safe-command and built-in uncertainty
for these exact actions. The earlier unsuccessful attempts remain unchanged.
Timeout, disconnect, unattended execution, shell composition, background
descendants, and broader native artifact families still need work.

Repeat a case with a new evidence path:

```sh
python3 conformance/run_native_local_model.py --scenario agent-execution --scope user --output evidence/new-agent-run.json
python3 conformance/run_native_local_model.py --scenario trust-image --output evidence/new-image-run.json
```

Each result has a matching `.runner.py` snapshot. Native binary hashes are checked
before the fixture starts. The agent runs project `.agents/native` artifacts
through the current reference CLI; the recorded projection output identifies
its targets. These observations do not change adapter support status.
