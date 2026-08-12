# Native Adapter Conformance

This harness creates an isolated repository, applies one reference adapter,
and runs the exact pinned native harness. The harness must discover root and
nested instructions, start the marker MCP server, invoke its tool, and discover
the portable skill. Evidence records exact package provenance, platform,
commands, markers, and bounded transcripts.

Native runs require the relevant `GH_TOKEN`, `OPENAI_API_KEY`, or
`ANTHROPIC_API_KEY`. They are release evidence, not pull-request tests.

```sh
AGENTS_BIN=/path/to/agents python3 conformance/run_adapter.py codex \
  --output evidence/results/codex.json
```

Never mark an adapter supported from the deterministic projection suite or
from a native configuration listing alone.
