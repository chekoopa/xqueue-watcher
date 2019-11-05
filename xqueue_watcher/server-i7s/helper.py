import json
import os
import subprocess
import sys
import time
import traceback


class ClientResult:
    def __init__(self, code=None, stdout=None, stderr=None, timeout=False):
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        self.timeout = timeout

    def __repr__(self):
        return f"ClientResult({self.code}, {self.stdout}, " \
            f"{self.stderr}, {self.timeout})"


def eprint(*args):
    print(*args, file=sys.stderr, flush=True)


def mean(L):
    return sum(L)/len(L)


def make_fork(cmd, in_text=None):
    in_text = in_text.encode() if in_text is not None else b""
    r = subprocess.Popen(f"{cmd}", shell=True, env=os.environ,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    r.stdin.write(in_text)
    return r


def poke_fork(proc):
    retcode = proc.poll()
    stdout, stderr = proc.communicate() if retcode is not None else (None,None)
    return retcode, stdout, stderr


def update_client(state, timeout=1):
    if state['client'] is None:
        state['kill_client_at'] = time.time() + timeout
        state['client'] = make_fork("python3 client.py")
    else:
        ecode, stdout, stderr = poke_fork(state['client'])
        if ecode is not None:
            state['client'] = None
            state['kill_client_at'] = None
            return ClientResult(ecode, stdout, stderr)
        elif time.time() > state['kill_client_at']:
            state['client'].kill()
            try:
                stdout, stderr = state['client'].communicate(timeout=0.1)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", "Hangup"
            state['client'] = None
            state['kill_client_at'] = None
            return ClientResult(None, stdout, stderr, timeout=True)
    return None


def kill_client(state):
    state['client'].kill()
    state['client'] = None
    state['kill_client_at'] = None


def exit_grader(score, msg):
    print(json.dumps({'score': score, 'msg': msg}), flush=True)
    sys.exit(0)


def bail_exception():
    traceback.print_exc()
    exit(1)