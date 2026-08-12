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
- Canonical source projected from: `.agents/tools/mcp.json`
- Projection file: `<path>`
- Path result: `<discovered / not discovered / unknown>`
  Describe the observed behavior; do not infer it from documentation alone.

## Repeatable runtime test

```sh
<exact command, including required environment setup>
```

- Result: `<pass / fail / partial>`
- Capability result: `<servers discovered, started, tools listed or invoked>`
- Captured output or CI job: `<durable evidence link>`

## Capability boundaries

- Demonstrated capabilities: `<specific capabilities and server types>`
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
