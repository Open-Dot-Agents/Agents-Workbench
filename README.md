# Agents-Workbench

## Mapping between `.agents` and tool runtimes

Detailed mappings for each harness are now in [VENDORS.md](/mnt/DATA/workspace/_/Open-Dot-Agents/Open-Dot-Agents/WORKBENCH/VENDORS.md).

## Quick summary

- `.agents/AGENTS.md` maps to `AGENTS.md` in Copilot, Claude Code, and Codex.
- `.agents/tools/mcp.json` maps to `.github/mcp.json` (Copilot), `.mcp.json` (Claude Code), and `.codex/config.toml` (`[mcp_servers]`) for Codex.
- `.agents/skills/<skill>/SKILL.md` is natively supported by Copilot and Codex while it maps to `.claude/skills/<skill>/SKILL.md` for Claude Code.
