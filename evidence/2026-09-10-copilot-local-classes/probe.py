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
  with socket.socket(kind) as connection:
   connection.settimeout(2);connection.connect(address)
   if kind==socket.AF_UNIX:connection.sendall(b'ODA_NATIVE_UNIX')
  result['network-'+str(kind)]={'outcome':'allowed'}
 except OSError as error:result['network-'+str(kind)]={'outcome':'denied','errno':error.errno}
try:
 with socket.socket(socket.AF_UNIX) as client:
  client.settimeout(2);client.connect(targets['abstract']);client.sendall(b'ODA_NATIVE_ABSTRACT')
 result['host-abstract-unix']={'outcome':'allowed'}
except OSError as error:result['host-abstract-unix']={'outcome':'denied','errno':error.errno}
try:
 with socket.socket(socket.AF_UNIX) as server:
  server.bind('\0oda-self-'+root.parent.name);server.listen(1)
  with socket.socket(socket.AF_UNIX) as client:
   client.settimeout(2);client.connect(server.getsockname());client.sendall(b'fixture')
   connection,_=server.accept()
   with connection:
    connection.settimeout(2);assert connection.recv(20)==b'fixture'
 result['self-abstract-unix']={'outcome':'allowed'}
except OSError as error:result['self-abstract-unix']={'outcome':'denied','errno':error.errno}
try:
 with socket.socket() as server:
  server.bind(('127.0.0.1',0));server.listen(1)
  with socket.create_connection(server.getsockname(),timeout=2):pass
 result['self-loopback']={'outcome':'allowed'}
except OSError as error:result['self-loopback']={'outcome':'denied','errno':error.errno}
for key,family,address in [('self-loopback-ipv6',socket.AF_INET6,'::1'),('self-loopback-udp',socket.AF_INET,'127.0.0.1')]:
 try:
  kind=socket.SOCK_DGRAM if key.endswith('udp') else socket.SOCK_STREAM
  with socket.socket(family,kind) as server:
   server.bind((address,0));server.settimeout(2)
   if kind==socket.SOCK_DGRAM:
    with socket.socket(family,kind) as client:client.sendto(b'fixture',server.getsockname())
    assert server.recv(20)==b'fixture'
   else:
    server.listen(1)
    with socket.socket(family,kind) as client:
     client.settimeout(2);client.connect(server.getsockname())
  result[key]={'outcome':'allowed'}
 except OSError as error:result[key]={'outcome':'denied','errno':error.errno}
(root/'observation.json').write_text(json.dumps(result,indent=2)+'\n')
print('ODA_SANDBOX_PROBE_COMPLETE')
