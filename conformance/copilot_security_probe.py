import pathlib,json,socket
root=pathlib.Path(__file__).parent
outside=root.parent/'outside'
result={}
for key,operation,path in [('workspace-write','write',root/'allowed'),('outside-write','write',outside/'forbidden'),('private-read','read',root/'private/marker'),('readonly-write','write',root/'readonly/forbidden'),('temp-write','write',pathlib.Path('/tmp')/('oda-copilot-'+root.parent.name))]:
 try:
  if operation=='read':path.read_bytes()
  else:path.write_text('harmless marker')
  result[key]={'outcome':'allowed'}
 except OSError as error:result[key]={'outcome':'denied','errno':error.errno}
targets=json.loads((root/'network-targets.json').read_text())
for kind,address in [(socket.AF_INET,('127.0.0.1',targets['tcp_port'])),(socket.AF_UNIX,targets['unix'])]:
 try:
  with socket.socket(kind) as connection:connection.settimeout(2);connection.connect(address)
  result['network-'+str(kind)]={'outcome':'allowed'}
 except OSError as error:result['network-'+str(kind)]={'outcome':'denied','errno':error.errno}
try:
 with socket.socket() as server:
  server.bind(('127.0.0.1',0));server.listen(1)
  with socket.create_connection(server.getsockname(),timeout=2):pass
 result['self-loopback']={'outcome':'allowed'}
except OSError as error:result['self-loopback']={'outcome':'denied','errno':error.errno}
(root/'observation.json').write_text(json.dumps(result,indent=2)+'\n')
print('ODA_SANDBOX_PROBE_COMPLETE')
