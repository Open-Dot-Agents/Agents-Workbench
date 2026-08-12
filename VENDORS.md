# Experimental Adapter Notes

This is a workbench inventory, not a support matrix. A checked-in projection
means only that the Python projection test validates its serialized fields
against `.agents/tools/mcp.json`. It does not guarantee runtime discovery,
native-path support, or MCP capability support by any upstream release.

| Experiment | Checked-in file | Narrow projection contract | Runtime status |
| --- | --- | --- | --- |
| Copilot | `.github/mcp.json` | `mcpServers` is structurally identical to canonical JSON. | Not yet evidenced for graduation. |
| Codex | `.codex/config.toml` | `[mcp_servers]` preserves command, args, env, and validates optional Codex-only timeout/approval fields. | Not yet evidenced for graduation. |
| OpenCode | `.opencode/opencode.json` | Each canonical stdio server becomes an enabled OpenCode `local` command array; env becomes `environment`. | Not yet evidenced for graduation. |

No Claude adapter is checked in or implied by this table. Do not infer a
native configuration path from this workbench for an adapter that lacks a
versioned evidence record.

## Before relying on a path

Capture the upstream release version, operating system, exact invocation,
observed capability result, limitations, and a durable evidence link using
[`evidence/ADAPTER_EVIDENCE_TEMPLATE.md`](evidence/ADAPTER_EVIDENCE_TEMPLATE.md).
Only a reviewed record meeting the [promotion requirements](README.md#promotion-requirements)
can make a mapping eligible for the future conformance suite.
