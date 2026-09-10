import concurrent.futures,hashlib,json,pathlib,subprocess,datetime,re
root=pathlib.Path('/mnt/DATA/workspace/_/Open-Dot-Agents/Open-Dot-Agents')
base=root/'WORKBENCH/evidence/native-draft2-debug';output=base/'verification-codex-mcp-hooks.json';assert not output.exists()
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
old=json.loads((base/'verification-codex-hooks.json').read_text())
checks=[c for c in old['checks'] if 'command' in c and c['check'] not in ['go-race','coverage-tests']]
def run(c):
 r=subprocess.run(c['command'],cwd=root/c['cwd'],capture_output=True,text=True)
 return dict(check=c['check'],cwd=c['cwd'],command=c['command'],exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr)
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(run,checks))
results.extend(json.loads(pathlib.Path('/mnt/DATA/tmp/oda-codex-mcp-hook-prior-checks.json').read_text()))
for checkout in [root,root/'CLI',root/'SPEC',root/'WORKBENCH']:
 listing=subprocess.check_output(['git','ls-files','-co','--exclude-standard','-z'],cwd=checkout).decode().split('\0');count=0
 for name in set(listing):
  path=checkout/name
  if name.endswith('.json') and path.is_file():json.loads(path.read_text());count+=1
 results.append(dict(check='json-'+str(checkout.relative_to(root)),files=count,exit_code=0))
for name in ['NATIVE_DEBUG_RESEARCH.md','NATIVE_CONFIGURATION.md','VENDOR_EVIDENCE.md']:
 report=root/'docs'/name
 for link in re.findall(r'\]\(([^)]+)\)',report.read_text()):
  if '://' not in link and not link.startswith('#'):
   path=(report.parent/link.split('#')[0]).resolve();assert path==output or path.exists(),link
results.append(dict(check='report-links',exit_code=0))
native=sorted(p.name for p in base.glob('codex-mcp-hooks-final-*.json'));assert len(native)==20
for name in native:
 record=json.loads((base/name).read_text());assert record['passed'],name
 assert sha((base/name).with_suffix('.runner.py'))==record['runner_sha256']==sha(root/'WORKBENCH/conformance/run_native_codex_mcp_hooks.py'),name
 assert all(sha(root/key)==value for key,value in record['implementation_sha256'].items()),name
 assert all(sha(root/'WORKBENCH/conformance'/key)==value for key,value in record['helper_sha256'].items()),name
 assert record['native_sha256']=='3188814c35471432d4123203e0eb38e5bddc60226e3d7ddf0e59e649ea140022',name
results.append(dict(check='native-results-and-final-implementation-hashes',native_records=len(native),exit_code=0))
parser=json.loads((base/'codex-mcp-hooks-parser.json').read_text())
assert sha(base/'codex-mcp-hooks-parser.runner.py')==parser['runner_sha256']
cases={c['case']:c['response']['data'][0] for c in parser['cases']}
for name in ['null','nested-null','array-null']:
 assert not cases[name]['hooks'] and 'TOML' in str(cases[name]['warnings']),name
for name in ['object','uint64','above-int64','int64','negative-int64','float']:
 assert len(cases[name]['hooks'])==1 and not cases[name]['warnings'],name
assert not cases['session-end']['hooks'] and 'SessionEnd MCP hooks are not supported' in str(cases['session-end']['warnings'])
results.append(dict(check='native-parser-matrix',cases=len(cases),exit_code=0))
failed=['codex-mcp-hooks-first-project.json','codex-mcp-hooks-first-missing-tool-user.json','codex-mcp-hooks-first-timeout-user.json']
for name in failed:
 record=json.loads((base/name).read_text());assert not record['passed'],name
 assert sha((base/name).with_suffix('.runner.py'))==record['runner_sha256'],name
record=json.loads((base/failed[0]).read_text());assert 'unsupported unit type' in str(record['hooks_list'])
record=json.loads((base/failed[1]).read_text());assert any(e.get('method')=='tools/call' and e['params']['name']=='missing' for e in record['mcp_events'])
record=json.loads((base/failed[2]).read_text());assert len([e for e in record['events'] if e.get('method')=='hook/completed' and e['params']['run']['status']=='failed'])==2
results.append(dict(check='retained-failed-attempts',records=failed,exit_code=0))
runner=root/'WORKBENCH/conformance/run_native_codex_mcp_hooks.py';compile(runner.read_text(),str(runner),'exec')
results.append(dict(check='native-runner-syntax',exit_code=0))
files=list((root/'CLI').rglob('*.go'))+[runner,root/'.agents/features/coverage.json',root/'scripts/native_coverage.py',root/'scripts/native_coverage_test.py',root/'docs/NATIVE_CONFIGURATION.md',root/'docs/NATIVE_DEBUG_RESEARCH.md',root/'docs/VENDOR_EVIDENCE.md',root/'CLI/go.mod',root/'CLI/go.sum']
inventory=sha(root/'.agents/features/codex-copilot.json');assert inventory=='f41f70b081e3920822c125f90a1c27b5bea920061f74510ed20dc49c419ed310'
result=dict(checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),revision='uncommitted working tree',full_adapter_support=False,checks=results,native_evidence=native,failed_attempts=failed,implementation_sha256={str(p.relative_to(root)):sha(p) for p in sorted(files)},source_inventory_sha256=inventory,verification_runner_sha256=sha(pathlib.Path(__file__)))
output.with_suffix('.runner.py').write_bytes(pathlib.Path(__file__).read_bytes());output.write_text(json.dumps(result,indent=2)+'\n')
for check in results:print(check['check'],check['exit_code'],(check.get('stdout','')+check.get('stderr',''))[-300:] if check['exit_code'] else '')
raise SystemExit(any(c['exit_code'] for c in results))
