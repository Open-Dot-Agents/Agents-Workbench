# MCP Server Package Conformance

This directory owns pinned package installation and startup checks for MCP
servers referenced by the canonical examples. Package-manager state belongs in
Workbench, not in a portable `.agents` tree.

Run from the Workbench root:

```sh
task mcp-servers
```

The task performs a clean install with lifecycle scripts disabled, audits the
resolved graph, and checks that each stdio server remains alive after startup.
This is package and process evidence only; it is not native adapter evidence.
