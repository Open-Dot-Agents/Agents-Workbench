# Adapter Evidence: `<adapter>` `<upstream-version>`

> Status: experimental / proposed for graduation
> Test date: `YYYY-MM-DD`
> Tested by: `<name or automation identity>`

## Upstream provenance

- Adapter and exact upstream version: `<name> <version>`
- Installation source and immutable reference: `<release URL, commit, or package digest>`
- Platform: `<OS, architecture, shell or runtime versions>`
- Upstream documentation for the tested configuration path: `<URL>`

## Native configuration

- Tested native path: `<path>`
- Repository or user scope: `<scope>`
- Canonical source projected from: `.agents/tools/mcp.json`, `.agents/hooks/hooks.json`
- Projection file: `<path>`
- Path result: `<discovered / not discovered / unknown>`
  Describe the observed behavior; do not infer it from documentation alone.

## Repeatable runtime test

```sh
<exact command, including required environment setup such as AGENTS_BIN and
COPILOT_BIN, CODEX_BIN, or CLAUDE_BIN when used>
```

- Result: `<pass / fail / partial>`
- Capability result: `<servers discovered, started, tools listed or invoked, hooks executed>`
- Captured output or CI job: `<durable evidence link>`
- Evidence JSON `metadata.runMode`: `<native>`
- Evidence JSON transcript cases: `root-instruction`, `nested-instruction`,
  and `portable-skill`; `native.hook` must be from the projected `PreToolUse`
  command hook, not only from startup or prompt hooks.

## Capability boundaries

- Demonstrated capabilities: `<specific capabilities, server types, and hook events>`
- Unsupported or untested capabilities: `<list>`
- Credentials, network, approval, and security assumptions: `<list>`
- Limitations and known failures: `<list>`

## Graduation checklist

- [ ] Exact upstream version and immutable provenance recorded.
- [ ] Native path verified by the runtime at that version.
- [ ] Repeatable command and durable evidence link recorded.
- [ ] Capability result and limitations documented.
- [ ] Future conformance test added for demonstrated behavior.
- [ ] Maintainer review approves promotion.

## Hook lifecycle checks

- Record `native.hook`, `native.hook.session`, and `native.hook.disabled`.
- Attach `metadata.disabledHooks` with the deactivation mode, markers, return
  code, and bounded transcript. Require MCP activity and no hook execution.
- For Codex and Claude, verify that disabled-catalogue refusal changes no
  files, then test removal through the manifest profile.
- For Copilot, test catalogue disablement after an initial enabled apply.
