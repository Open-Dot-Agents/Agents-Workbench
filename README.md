# Agents-Workbench

## Mapping between `.agents` and tool runtimes

Detailed mappings for each harness are now in [VENDORS.md](/mnt/DATA/workspace/_/Open-Dot-Agents/Open-Dot-Agents/WORKBENCH/VENDORS.md).

## Quick summary

- `.agents/AGENTS.md` maps to `AGENTS.md` in Copilot, Claude Code, and Codex.
- `.agents/tools/mcp.json` maps to `.github/mcp.json` (Copilot), `.mcp.json` (Claude Code), and `.codex/config.toml` (`[mcp_servers]`) for Codex.
- `.agents/skills/<skill>/SKILL.md` is natively supported by Copilot and Codex while it maps to `.claude/skills/<skill>/SKILL.md` for Claude Code.

## Validation

Run `task test:projections` to confirm the Copilot, Codex, and OpenCode MCP
adapters preserve the canonical server names, commands, arguments, and
environment. The standard-library Python suite also validates canonical schema,
rejects duplicate JSON keys, and checks provider-specific fields. `task test`
runs this projection check before asking each harness CLI to enumerate its
configured MCP servers.
