import json,subprocess,pathlib,tempfile,hashlib
b=pathlib.Path(tempfile.mkdtemp(prefix='oda-mcp-paths-',dir='/mnt/DATA/tmp'));home=b/'home';home.mkdir();binary=pathlib.Path('/home/maurizio/.local/bin/copilot');sha=hashlib.sha256(binary.read_bytes()).hexdigest();assert sha=='a3262c4513ef1fc2ca21485261ca73196977ad76bd5e7990fb572f6134aaeedd'
env={'PATH':'/usr/bin:/bin','HOME':str(home),'COPILOT_HOME':str(home),'COPILOT_OFFLINE':'true','COPILOT_PROVIDER_BASE_URL':'http://127.0.0.1:9/v1','COPILOT_MODEL':'fixture-model'}
cases=[]
for name,relative in [('github','.github/mcp.json'),('root','.mcp.json'),('user','mcp-config.json')]:
 work=b/name;work.mkdir();subprocess.run(['git','init','-q',str(work)],check=True)
 path=(home if name=='user' else work)/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps({'mcpServers':{'oda-'+name:{'type':'local','command':'/usr/bin/false','args':[],'tools':['*']}}}))
 command=[str(binary),'plugins','list','--kind','mcp','--json'];r=subprocess.run(command,cwd=work,env=env,capture_output=True,text=True,timeout=15)
 cases.append({'source':str(path),'command':command,'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
p=pathlib.Path('WORKBENCH/evidence/native-draft2-debug/copilot-mcp-paths.json');assert not p.exists();p.write_text(json.dumps({'native_version':'1.0.83','native_sha256':sha,'fixture':str(b),'cases':cases},indent=2)+'\n');p.with_suffix('.runner.py').write_bytes(pathlib.Path(__file__).read_bytes());print(json.dumps(cases,indent=2))
