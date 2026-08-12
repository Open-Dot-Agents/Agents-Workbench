# Agents Workbench

This submodule is an **experimental adapter laboratory**, not a catalog of
supported Open-Dot-Agents standard adapters. Its checked-in MCP files are
projections of the canonical `.agents/tools/mcp.json` configuration for
experimentation and future conformance-suite inputs.

## Current experiments

| Experiment | Checked-in projection | What is checked |
| --- | --- | --- |
| Copilot | `.github/mcp.json` | JSON shape and canonical server data |
| Codex | `.codex/config.toml` | TOML shape and canonical server data |
| OpenCode | `.opencode/opencode.json` | OpenCode local-server shape and canonical server data |

The projection tests do **not** prove that a particular upstream version
discovers a repository-local configuration, starts every server, or supports
every MCP capability. They also do not establish native paths as stable
integration contracts. There is no Claude adapter in this workbench.

See [VENDORS.md](VENDORS.md) for the experimental mapping boundaries and
[`evidence/`](evidence/) for the evidence required to graduate an experiment.

## Promotion requirements

An experiment can be proposed for a supported standard adapter only after all
of the following are recorded and reviewed:

1. An evidence record based on
   [`evidence/ADAPTER_EVIDENCE_TEMPLATE.md`](evidence/ADAPTER_EVIDENCE_TEMPLATE.md)
   names the exact upstream version, platform, and test date.
2. A repeatable, real-runtime test command and its capability result are
   preserved in an accessible evidence link.
3. The native configuration path is verified for that version, with upstream
   documentation cited; unverified paths remain experimental.
4. Supported capabilities, omissions, security or credential assumptions, and
   known limitations are explicit.
5. A future conformance test covers the demonstrated behavior and a maintainer
   approves the promotion.

## Validation

Run `python3 task/test/mcp_projections_test.py` to verify the checked-in
projections. The suite validates the narrow configuration contracts described
above, including canonical schema, duplicate-key rejection, and provider
projection fields. `task test` additionally asks locally installed harness
CLIs to list MCP servers; that command is an environment-dependent experiment,
not graduation evidence by itself.

For release-readiness checks, run `task verify`. It only runs deterministic
projection validation and does not call native harness CLIs.
