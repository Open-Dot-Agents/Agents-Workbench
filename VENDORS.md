# Vendors and Harness Mapping

| Vendor | AGENTS entrypoint | MCP config | Skills | Docs |
| --- | --- | --- | --- | --- |
| Copilot | `AGENTS.md` (repo root) | `.github/mcp.json` | `.agents/skills/<skill>/SKILL.md` | [AGENTS.md](https://docs.github.com/en/enterprise-cloud%40latest/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions)<br>[Skills](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills)<br>[MCP](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/invoke-custom-agents) |
| Claude Code | `AGENTS.md` (repo root) | `.mcp.json` | `.claude/skills/<skill>/SKILL.md` | [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md.md)<br>[MCP](https://learn.chatgpt.com/docs/extend/mcp.md)<br>[Skills](https://code.claude.com/docs/en/skills) |
| Codex | `AGENTS.md` (repo root) | `~/.codex/config.toml` (`[mcp_servers]`) | `.agents/skills/<skill>/SKILL.md` | [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md.md)<br>[MCP](https://learn.chatgpt.com/docs/extend/mcp.md)<br>[Skills](https://learn.chatgpt.com/docs/build-skills.md) |
