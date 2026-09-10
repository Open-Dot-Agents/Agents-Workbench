import sys,json,tempfile,pathlib,time
sys.path.insert(0,str(pathlib.Path('WORKBENCH/conformance').resolve()))
from run_native_approvals import Client,PINS,sha
root=pathlib.Path(tempfile.mkdtemp(prefix='oda-skill-path-',dir='/mnt/DATA/tmp'));binary='/home/maurizio/.local/bin/codex';assert sha(binary)==PINS['codex']
result={'fixture':str(root),'native_version':'0.154.0','native_sha256':sha(binary),'cases':[]}
for kind in ('tilde',):
 home=root/kind;home.mkdir();workspace=home/'workspace';workspace.mkdir();skill=home/'skills/fixture/SKILL.md';skill.parent.mkdir(parents=True);skill.write_text('---\nname: oda-skill-path\ndescription: Isolated path fixture.\n---\nODA_SKILL_PATH\n')
 path=str(skill) if kind=='absolute-file' else str(skill.parent) if kind=='absolute-folder' else 'skills/fixture/SKILL.md' if kind=='relative-file' else '~/skills/fixture/SKILL.md'
 (home/'config.toml').write_text('[[skills.config]]\npath = '+json.dumps(path)+'\nenabled = false\n')
 c=Client([binary,'app-server','--listen','stdio://'],workspace,{'PATH':'/usr/bin:/bin','HOME':str(home),'CODEX_HOME':str(home)},'deny',str(root/'unused'))
 r={'case':kind,'path':path}
 try:
  deadline=time.monotonic()+20;c.response(c.request('initialize',{'clientInfo':{'name':'oda-skill-path','version':'0'},'capabilities':{'experimentalApi':True}}),deadline);c.send({'method':'initialized'});v=c.response(c.request('skills/list',{'cwds':[str(workspace)],'forceReload':True}),deadline);r['response']=v;r['fixture_skills']=[s for d in v['data'] for s in d['skills'] if s['name']=='oda-skill-path']
 except Exception as e:r['error']=str(e)
 finally:c.close();r.update(stderr=c.errors,events=c.events);result['cases'].append(r)
print(json.dumps({'fixture':str(root),'cases':[{k:v for k,v in r.items() if k in ('case','error','fixture_skills')} for r in result['cases']]},indent=2));p=pathlib.Path('/mnt/DATA/tmp/oda-codex-skill-tilde-probe.json');assert not p.exists();p.write_text(json.dumps(result,indent=2)+'\n')
