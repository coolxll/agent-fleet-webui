"""Self-contained POSIX bridge sent over SSH; no package install on target.

Only protocol evidence is emitted. EOF from the Fleet connection terminates
this bridge's own process group; existing Agent processes are never touched.
"""
BRIDGE = r'''
import json, os, signal, subprocess, sys, threading

child = None
finished = threading.Event()
def stop(*args):
    finished.set()
    if child is not None and child.poll() is None:
        try: os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        try: child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
    if args: sys.exit(1)
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
def emit(event, **kw):
    print(json.dumps(dict(event=event, **kw)), flush=True)
def send(msg):
    child.stdin.write(json.dumps(msg) + '\n'); child.stdin.flush()
def monitor():
    # Parent keeps stdin open; SSH disconnect or parent cancellation closes it.
    sys.stdin.read()
    if not finished.is_set():
        stop()
def stderr_drain():
    # Drain, but never persist raw diagnostics that can contain credentials.
    for _ in child.stderr: pass
try:
    config = json.loads(sys.stdin.readline())
    cwd = config['cwd']
    if not os.path.isabs(cwd) or not os.path.isdir(cwd):
        raise RuntimeError('Target working directory does not exist or is not absolute')
    agent = config['agent']
    if agent == 'codex':
        argv = [config.get('executable') or 'codex', 'app-server']
    else:
        argv = [config.get('executable') or 'claude', '-p', '--output-format', 'stream-json', '--verbose']
        if config.get('resume_session_id'): argv += ['--resume', config['resume_session_id']]
    child = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=stderr_drain, daemon=True).start()
    if agent == 'codex':
        send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'agent_fleet', 'title': 'Agent Fleet', 'version': '0.1.0'}}})
    else:
        child.stdin.write(config['prompt']); child.stdin.close()
    session_id = None
    got_result = False
    for line in child.stdout:
        try: msg = json.loads(line)
        except ValueError: continue
        if not isinstance(msg, dict): continue
        if agent == 'codex':
            if msg.get('error') and msg.get('id') in (1, 2, 3):
                raise RuntimeError('Codex rejected ' + str(msg['id']) + ': ' + str(msg['error'].get('message', 'protocol error'))[:500])
            if msg.get('id') == 1 and 'result' in msg:
                send({'method': 'initialized'})
                resume = config.get('resume_session_id')
                params = {'cwd': cwd, 'approvalPolicy': 'never', 'sandbox': 'workspaceWrite'}
                if resume: params['threadId'] = resume
                send({'id': 2, 'method': 'thread/resume' if resume else 'thread/start', 'params': params})
            elif msg.get('id') == 2 and 'result' in msg:
                session_id = msg['result']['thread']['id']
                emit('session', session_id=session_id)
                send({'id': 3, 'method': 'turn/start', 'params': {'threadId': session_id, 'input': [{'type': 'text', 'text': config['prompt']}]}})
            elif msg.get('id') == 3 and 'result' in msg:
                emit('started', session_id=session_id)
            elif msg.get('method') == 'item/agentMessage/delta':
                emit('output', text=msg.get('params', {}).get('delta', ''))
            elif msg.get('method') == 'turn/completed':
                turn = msg.get('params', {}).get('turn', {})
                state = turn.get('status')
                emit('result', status='completed' if state == 'completed' else 'failed', error=None if state == 'completed' else str(turn.get('error') or state))
                got_result = True
                break
            elif 'id' in msg and 'method' in msg:
                # Never approve an unexpected request. Stop with recoverable state.
                emit('result', status='blocked', error='Agent requires interactive input: ' + msg['method'])
                got_result = True
                break
        else:
            if msg.get('type') == 'system' and msg.get('subtype') == 'init':
                session_id = msg.get('session_id')
                if session_id: emit('started', session_id=session_id)
            elif msg.get('type') == 'assistant':
                for block in msg.get('message', {}).get('content', []):
                    if block.get('type') == 'text': emit('output', text=block.get('text', '') + '\n')
            elif msg.get('type') == 'result':
                got_result = True
                denied = bool(msg.get('permission_denials'))
                failed = bool(msg.get('is_error')) or msg.get('subtype') not in ('success', None)
                emit('result', status='blocked' if denied else 'failed' if failed else 'completed', error='Some tools need interactive permission; resume the native session' if denied else str(msg.get('subtype')) if failed else None)
    if not got_result:
        code = child.wait()
        raise RuntimeError('Agent exited without a final protocol result (exit ' + str(code) + ')')
except Exception as exc:
    emit('result', status='failed', error=str(exc)[:1000])
finally:
    stop()
'''
