import json, os, pathlib, sys, time
p = pathlib.Path('calls.jsonl')
with p.open('a') as f: f.write(json.dumps({'cwd': os.getcwd(), 'args': sys.argv[1:]})+'\n')
mode = sys.argv[1]
if mode == 'timeout': time.sleep(2)
if mode == 'exit': sys.exit(7)
if mode == 'empty': sys.exit(0)
if mode == 'invalid-utf8': sys.stdout.buffer.write(b'\xff'); sys.exit(0)
print('oda-command-synthetic-token-'+str(len(p.read_text().splitlines())))
