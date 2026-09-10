# Agent Plugins environment requirements

Excerpt from Agent Plugins 1.0.0, sections 9.1 and 9.2.
Source: https://github.com/agentplugins/agent-plugins-spec/blob/ff8ab5e392cc87bd88d87c060815a87490e51003/spec/1.0.0.md
Copyright: Agent Plugins contributors. License: CC-BY-4.0.

### 9.1 Subprocess environment

Clients that launch plugin subprocesses (i.e., stdio MCP servers) MUST provide `PLUGIN_ROOT` and `PLUGIN_DATA` in each subprocess environment. `PLUGIN_ROOT` is the absolute path to the filesystem-resolved plugin root. `PLUGIN_DATA` is the absolute path to a client-managed persistent data directory dedicated to that installed plugin instance.

The client chooses the `PLUGIN_DATA` location. It MUST create the directory before launching a plugin subprocess, MUST make it writable to that subprocess, and MUST preserve its contents across plugin updates. The client MAY delete the directory when the plugin is uninstalled.

Use `PLUGIN_DATA` for: installed dependencies (node_modules, virtual environments), generated code, caches, and other plugin state that should persist across updates. Use `PLUGIN_ROOT` for referencing bundled scripts, binaries, and config files that ship with the plugin.

The client chooses the base subprocess environment and MAY inherit, omit, or sanitize ambient variables. After placeholder expansion, entries in a stdio server's `env` object MUST overlay the base environment and replace same-name entries according to platform environment-name semantics. The client MUST then set `PLUGIN_ROOT` and `PLUGIN_DATA` to the values defined above, replacing any entries with equivalent names according to platform environment-name semantics.

Except for the platform executable search used to resolve a bare `command`, plugins claiming conformance MUST NOT depend on a base-environment variable unless this specification requires that variable or the server configuration supplies it explicitly.

Example: a client loading the plugin `devtools` from `/home/alex/.agents/plugins/devtools` sets:

```text
PLUGIN_ROOT=/home/alex/.agents/plugins/devtools
PLUGIN_DATA=/home/alex/.agents/plugins/data/devtools
```

### 9.2 Placeholder expansion

Clients that launch plugin subprocesses MUST expand `${PLUGIN_ROOT}` and `${PLUGIN_DATA}` in supported configuration fields. Expansion is a single, non-recursive textual replacement of every exact occurrence of either placeholder. Text introduced by a replacement MUST NOT be scanned for further placeholders.

Expansion applies to every string element of `args`, every string value in `env`, and the `cwd` string. It does not apply to `env` keys, `command`, or fixed component locations.

Unrecognized placeholder-like text MUST remain literal. Clients MUST NOT perform any other placeholder or environment-variable expansion.

Configured `env` values are visible package data, not a portable secret mechanism. Plugins MUST NOT embed credentials or other secrets in `env`.

An MCP server's `env` object MUST NOT contain entries named `PLUGIN_ROOT` or `PLUGIN_DATA`. Such an entry makes that server configuration invalid under §7.2.2. Clients MUST supply the reserved environment variables themselves.

Example: plugin variable expansion in MCP

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
  "mcpServers": {
    "database": {
      "type": "stdio",
      "command": "npx",
      "args": ["--config", "${PLUGIN_ROOT}/config/db.json"],
      "cwd": "${PLUGIN_ROOT}",
      "env": {
        "DATA_DIR": "${PLUGIN_DATA}/database"
      }
    }
  }
}
```

