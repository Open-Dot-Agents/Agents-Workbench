"""Drive the documented Codex terminal compaction command."""
import time

def compact_tui(executable, cwd, environment, session, hook_log):
    """Run the documented /compact command in the real native CLI terminal."""
    import re
    import pexpect
    command=[executable,'resume',session,'--dangerously-bypass-approvals-and-sandbox',
             '--dangerously-bypass-hook-trust','--no-alt-screen']
    child=pexpect.spawn(command[0],command[1:],cwd=str(cwd),env={**environment,'TERM':'xterm-256color'},
                        encoding='utf-8',timeout=1,dimensions=(45,160))
    output='';sent=False;completed=False;started=time.monotonic();deadline=started+150
    try:
        while time.monotonic()<deadline and child.isalive():
            try:chunk=child.read_nonblocking(16384,timeout=.25)
            except pexpect.TIMEOUT:chunk=''
            except pexpect.EOF:break
            output+=chunk
            if '\x1b[6n' in chunk:child.send('\x1b[1;1R')
            if '\x1b]11;?' in chunk:child.send('\x1b]11;rgb:0000/0000/0000\x1b\\')
            plain=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',output)
            if not sent and time.monotonic()-started>8 and 'Ask Codex' in plain and 'gpt-' in plain:
                child.send('/compact');time.sleep(.4);child.send('\r');sent=True
            if sent and 'context compacted' in plain.lower() and hook_log.exists():
                completed=True;child.send('/exit\r');break
        return {'command':command,'compactCommandSent':sent,'completed':completed,
                'output':re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',output)[-14000:]}
    finally:child.close(force=True)
