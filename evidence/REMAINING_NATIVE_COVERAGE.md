# Extended native coverage worklist

Target: latest pinned Codex and Copilot, on the current Linux host. Use existing
CLI logins in temporary runtime directories. Preserve separate evidence for
each case, including failures and expected adapter refusals.

| Requirement | Cases | State |
| --- | --- | --- |
| HTTPS remote MCP | TLS connection, native call, request observed | Complete |
| Runtime environment references | stdio environment, remote auth header; Copilot refusal | Complete |
| Stdio process contract | Argument vector, spaces, quotes, literal shell characters | Complete |
| Instruction precedence | root, child override, sibling isolation, root compatibility link | Complete |
| Skill resources | references, assets, scripts, paths with spaces | Complete |
| Profile selection | tools/skills/hooks absent initially and removed after apply | Complete |
| Hook events | all ten canonical events, including subagents and compaction | Complete |
| Hook filtering | matching and nonmatching patterns; unsupported matcher refusal | Complete |
| Hook execution limits | timeout, failure status, output contracts | Complete |
| Native state refresh | changed instructions, tools, skills, hooks on next invocation and resumed session | Complete |
| Trust and permissions | untrusted project, denied tool, permission request and decision | Complete |
| Capability/loss boundaries | required unknown/unsupported capability, unsupported native content, no writes on refusal | Complete |

A completed test can establish a pass, a reproducible failure, or a verified
unsupported mapping. An untriggered event or missing observation remains
incomplete. Do not promote complete harness support from a subset of cases.

All 46 combinations have complete evidence. See [EXTENDED_NATIVE_TESTS.md](EXTENDED_NATIVE_TESTS.md) for passes, expected refusals, and five confirmed failures. Fixes remain open.
