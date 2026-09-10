# Copilot native security assessment, 2026-09-10

Copilot 1.0.83 could connect to a live host Unix socket with
`allowLocalNetwork=false`. The draft defines Unix sockets as local network
access. The tested native settings therefore do not preserve that requirement.
The reference CLI continues to refuse Copilot security projection.

This assessment uses native settings. It is not portable enforcement after
`agents apply`. All 30 Copilot projection scenarios remain pending in
[SECURITY_SCENARIOS.json](SECURITY_SCENARIOS.json). No full adapter or release
support claim follows from these observations.

## Host, prerequisites, and authority

- OS: `Linux-7.0.0-31-generic-x86_64-with-glibc2.43`.
- Copilot: `1.0.83`; binary SHA-256
  `a3262c4513ef1fc2ca21485261ca73196977ad76bd5e7990fb572f6134aaeedd`.
- Local prerequisite: Ubuntu `slirp4netns=1.3.3-1`, extracted under
  `/mnt/DATA/tmp/oda-copilot-prerequisites`; host libslirp is 4.9.1.
  No system package was installed. The default system PATH still lacks this
  executable; test processes added its extracted directory.
- Authentication used the existing approved GitHub login. The token entered
  only the native subprocess environment. Exact token values were removed
  from captured output before it was retained. No credential is in this report.
- Tests used an isolated `COPILOT_HOME`. Native bypass, developer-tool grants,
  Git/gh auth injection, builtin MCPs, remote mode, automatic updates, custom
  instructions, and bash environment loading were disabled for the fixture.
  MCP/LSP sandbox coverage was not tested.

The final filesystem settings made `/` and the fixture's `readonly` directory
read-only, the workspace and `/dev/null` writable, and `private` and `/tmp`
denied. Network settings had both `allowOutbound` and `allowLocalNetwork`
false. `allowBypass` and `addCurrentWorkingDirectory` were false.
The retained settings JSON records the complete input.

## Four retained attempts

| Attempt | Change | Observation |
| --- | --- | --- |
| 1 | Allow the shell tool with native sandbox settings. | Probe execution was refused by the native permission flow. No enforcement result. |
| 2 | Allow all tools. | Probe execution was still refused. Native exit status 0 did not mean that the probe ran. |
| 3 | Add `/dev/null` access and allow all tool paths. | The probe ran; outside/read-only writes failed and private reads failed. A temporary marker appeared on the host. `/tmp` was not explicitly denied in this attempt. |
| 4 | Explicitly deny `/tmp`; add independently reachable host TCP and Unix listeners. | The probe ran unchanged. Host Unix and self-loopback connections succeeded. The inside-sandbox temporary write succeeded, but its marker did not appear on the host. |

Attempts 3 and 4 used `--allow-all-tools --allow-all-paths` to separate tool
approval checks from native OS sandbox observations. No native sandbox bypass
was requested. The probe output and marker files, not the model's conclusions,
are the observation source. The model was allowed to inspect the final probe.

Final probe results:

| Operation | Result | Interpretation |
| --- | --- | --- |
| Workspace write | Allowed | Native writable workspace observation. |
| Outside write | Denied, errno 30 | Read-only filesystem result. |
| Read-only subtree write | Denied, errno 30 | Read-only filesystem result. |
| Private read | Denied, errno 2 | The native sandbox hid the existing fixture path. |
| `/tmp` write | Allowed inside; host marker absent | No evidence of a shared-host-temp bypass in this attempt. Private or redirected temporary storage needs separate investigation. |
| Host TCP connect | Errno 111 | Host baseline was reachable, but connection refusal alone is not a permission-denial result. |
| Host Unix-socket connect | Allowed | Confirmed mismatch with the draft local-network denial. |
| Self-loopback bind/connect | Allowed | Additional mismatch with the draft local-network denial. |

The attempt-3 host temporary marker was removed after the observation was
recorded. The attempt-4 result does not support the earlier provisional claim
that explicit `/tmp` denial still permitted a shared host write.

## Reproduction

The checked-in [probe](../conformance/copilot_security_probe.py) and
[assessment runner](../conformance/run_copilot_security.py) create harmless
filesystem markers and live local listeners. Prepare the prerequisite without
system installation:

```sh
mkdir -p /mnt/DATA/tmp/oda-copilot-prerequisites
cd /mnt/DATA/tmp/oda-copilot-prerequisites
apt-get download slirp4netns=1.3.3-1
dpkg-deb -x slirp4netns_1.3.3-1_amd64.deb unpacked
```

Provide an approved `COPILOT_GITHUB_TOKEN` through the secret mechanism, then
run from the superproject:

```sh
python3 WORKBENCH/conformance/run_copilot_security.py \
  --slirp4netns /mnt/DATA/tmp/oda-copilot-prerequisites/unpacked/usr/bin/slirp4netns \
  --result-dir /mnt/DATA/tmp/oda-copilot-new-evidence
```

This makes a model request. A new result directory is required. The runner
checks the native binary hash, records live listener baselines, captures the
probe output, checks its hash, and excludes raw native homes and logs from the
result directory. A zero runner status means the probe ran unchanged; it does
not mean that security policy passed.

The reusable runner was added after the four recorded attempts. Its syntax and
help were checked; no fifth model run was made. The original attempts are
retained as `results/copilot-security-assessment/`, including sanitized command
output, attempts 3/4 settings, and their observations. They are included in
`results/security-native-final.tar.gz`. The archive also contains prerequisite
hash/version metadata and official source snapshots. No raw credential,
session, or authentication store is archived.

## Next work

Keep local-network-deny projection refused unless a tested native control can
block both host Unix sockets and loopback. Assess other policy subsets only
with explicit coverage and native evidence. Do not infer tool, MCP, LSP,
credential, or approval coverage from these filesystem observations. Claude
native verification remains skipped at the user's request.
