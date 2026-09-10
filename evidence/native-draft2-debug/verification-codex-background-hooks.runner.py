import concurrent.futures,hashlib,json,pathlib,subprocess,datetime,re
root=pathlib.Path('/mnt/DATA/workspace/_/Open-Dot-Agents/Open-Dot-Agents')
base=root/'WORKBENCH/evidence/native-draft2-debug';output=base/'verification-codex-background-hooks.json';assert not output.exists()
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
initial=pathlib.Path('/mnt/DATA/tmp/oda-codex-background-checks.json')
initial_target=base/'codex-background-initial-validation.json';assert not initial_target.exists();initial_target.write_bytes(initial.read_bytes())
results=[c for c in json.loads(initial.read_text()) if c['check']!='coverage-tests']
results.append(json.loads(pathlib.Path('/mnt/DATA/tmp/oda-codex-background-coverage-final.json').read_text()))
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
native=sorted(p.name for p in base.glob('codex-background-final-*.json'));assert len(native)==34
for name in native:
 record=json.loads((base/name).read_text());assert record['passed'],name
 assert sha((base/name).with_suffix('.runner.py'))==record['runner_sha256']==sha(root/'WORKBENCH/conformance/run_native_codex_background_hooks.py'),name
 assert all(sha(root/key)==value for key,value in record['implementation_sha256'].items()),name
 assert all(sha(root/'WORKBENCH/conformance'/key)==value for key,value in record['helper_sha256'].items()),name
 assert record['native_sha256']=='3188814c35471432d4123203e0eb38e5bddc60226e3d7ddf0e59e649ea140022',name
results.append(dict(check='native-results-and-final-implementation-hashes',native_records=len(native),exit_code=0))
failed=['codex-background-first-next-project.json','codex-background-first-spill-user.json','codex-background-reviewed-background-cancel-user.json']
for name in failed:
 record=json.loads((base/name).read_text());assert not record['passed'],name
 assert sha((base/name).with_suffix('.runner.py'))==record['runner_sha256'],name
record=json.loads((base/failed[0]).read_text());assert record['error']=='native case deadline reached' and record['hook_events'][-1]['event']=='end'
record=json.loads((base/failed[1]).read_text());assert record['error']=='context preview absent' and record['context_spills'][0]['sha256']==record['context_sha256']
record=json.loads((base/failed[2]).read_text());assert record['error']=='native unsubscribe left hook processes alive' and all(state=='S' for state in record['process_states_after_unsubscribe'].values())
results.append(dict(check='retained-failed-attempts',records=failed,exit_code=0))
sources=json.loads((base/'codex-background-docs.source.json').read_text())
for source in sources['sources']:assert sha(base/source['path'])==source['sha256']
assert '30 minutes' in (base/'codex-background-app-server.md').read_text()
results.append(dict(check='official-document-hashes',exit_code=0))
runner=root/'WORKBENCH/conformance/run_native_codex_background_hooks.py';compile(runner.read_text(),str(runner),'exec')
results.append(dict(check='native-runner-syntax',exit_code=0))
files=list((root/'CLI').rglob('*.go'))+[runner,root/'.agents/features/coverage.json',root/'scripts/native_coverage.py',root/'scripts/native_coverage_test.py',root/'docs/NATIVE_CONFIGURATION.md',root/'docs/NATIVE_DEBUG_RESEARCH.md',root/'docs/VENDOR_EVIDENCE.md',root/'CLI/go.mod',root/'CLI/go.sum']
inventory=sha(root/'.agents/features/codex-copilot.json');assert inventory=='f41f70b081e3920822c125f90a1c27b5bea920061f74510ed20dc49c419ed310'
result=dict(checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),revision='uncommitted working tree',full_adapter_support=False,checks=results,native_evidence=native,failed_attempts=failed,implementation_sha256={str(p.relative_to(root)):sha(p) for p in sorted(files)},source_inventory_sha256=inventory,prior_failed_validation=str(initial_target.relative_to(root)),verification_runner_sha256=sha(pathlib.Path(__file__)))
output.with_suffix('.runner.py').write_bytes(pathlib.Path(__file__).read_bytes());output.write_text(json.dumps(result,indent=2)+'\n')
for check in results:print(check['check'],check['exit_code'],(check.get('stdout','')+check.get('stderr',''))[-300:] if check['exit_code'] else '')
raise SystemExit(any(c['exit_code'] for c in results))
