#!/usr/bin/env python3
"""Send one task to a worker (agy, Copilot+cloud API, or local Ollama) by role, with safe defaults.

Examples (from the project root; Windows, Linux and macOS):
  python agent/delegate.py --role builder --task-file .worker/t1.md --include src/shop.py
  python agent/delegate.py --role reviewer --task-file .worker/review.md
  python agent/delegate.py --role builder --provider nvidia --task-file .worker/t1.md   # force one worker
  python agent/delegate.py --local --task-file .worker/t1.md                            # private, text only

Roles: any agent/roles/<name>.md (frontmatter: base, models). Core rules per base: agent/bases/<base>.md.
Each role has a worker chain; workers missing on this machine (SETUP.md) are skipped. On a transient
provider error (429/503/no capacity) it retries once, then moves on. --model / --provider force one worker.
Workers run no commands. For builder/tester roles this script runs check_one itself after the worker is done
(--part fills {name}); when it fails, the worker gets the output once to fix it, then the check runs again.
For reviewer roles the check runs first and its result is pasted into the task.
--files names what the task may change: with workers in parallel, each run then reports only its own files.
Output: a short summary (full JSON saved in .worker/run-<id>.json and .worker/last.json); --json prints the JSON instead.
Exit: 0 ok, 2 no task, 3 timeout (check git status: files often exist), 4 blocked, 5 empty, 6 provider error,
7 check failed.
Project settings (check commands, worker notes) come from PROJECT.md. Standard library only.
"""
import argparse
import datetime
import fnmatch
import glob
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent      # agent/: this script, bases/, roles/
ROOT = AGENT_DIR.parent                           # project root: workers work here
IS_WIN = os.name == 'nt'

AGY_MODELS = ['gemini-3.8-flash-low', 'gemini-3.8-flash-medium', 'gemini-3.8-flash-high', 'gpt-oss-120b-medium', 'claude-sonnet-4-6']
BASES = ('builder', 'tester', 'reviewer', 'scribe')
DEFAULT_MODELS = {   # used when a role file has no models: line
    'builder': 'agy:gemini-3.8-flash-low, nvidia, openrouter:nvidia/nemotron-3-ultra-550b-a55b:free',
    'tester': 'gemini, nvidia, openrouter:nvidia/nemotron-3-ultra-550b-a55b:free',
    'reviewer': 'agy:claude-sonnet-4-6, openai:o3, agy:gpt-oss-120b-medium',
    'scribe': 'nvidia:nvidia/nemotron-3.5-lightning-30b-a3b, agy:gemini-3.8-flash-low, openrouter:nvidia/nemotron-3-ultra-550b-a55b:free',
}
PROVIDERS = {
    'openrouter': {'url': 'https://openrouter.ai/api/v1', 'key': 'OPENROUTER_API_KEY', 'model': 'openrouter/free'},
    'nvidia': {'url': 'https://integrate.api.nvidia.com/v1', 'key': 'NVIDIA_API_KEY', 'model': 'mistralai/mistral-large-4-1t-a52b'},
    'gemini': {'url': 'https://generativelanguage.googleapis.com/v1beta/openai/', 'key': 'GEMINI_API_KEY', 'model': 'gemini-flash-lite-latest'},
    # OpenAI: free daily tokens only for listed models with data sharing on (see MODELS.md). GPT-5 uses the responses API.
    'openai': {'url': 'https://api.openai.com/v1', 'key': 'OPENAI_API_KEY', 'model': 'gpt-5.4-mini'},
}
# OpenAI complimentary daily tokens (data sharing on): only these models, only up to these limits.
# Anything else is billed from the prepaid credit, so it is refused here. Reset: 00:00 UTC.
OPENAI_FREE = {
    'big': {'limit': 250000, 'models': ['gpt-5.4', 'gpt-5.2', 'gpt-5.1', 'gpt-5', 'gpt-4.1', 'gpt-4o', 'o1', 'o3']},
    'mini': {'limit': 2500000, 'models': ['gpt-5.4-mini', 'gpt-5.4-nano', 'gpt-5-mini', 'gpt-5-nano', 'gpt-4.1-mini',
                                          'gpt-4.1-nano', 'gpt-4o-mini', 'o3-mini', 'o4-mini']},
}
OPENAI_RUN_CAP = {'big': 60000, 'mini': 400000}   # max tokens per single run (normal task: 15-75k)
TRANSIENT = re.compile(r'429|503|UNAVAILABLE|RESOURCE_EXHAUSTED|capacity|rate limit|overloaded|timed out')
LABELS = {0: 'OK', 3: 'TIMEOUT (check git status)', 4: 'BLOCKED', 5: 'EMPTY', 6: 'PROVIDER ERROR', 7: 'CHECK FAILED'}
CHECK_TAIL_LINES, CHECK_TAIL_CHARS = 60, 6000   # how much check output a worker / the summary gets


class Fail(Exception):
    def __init__(self, msg, code):
        super().__init__(msg)
        self.code = code


def read_text(path):
    return Path(path).read_text(encoding='utf-8-sig')


def utc_day():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')


# --- Files the scripts read ----------------------------------------------------------------
def parse_env_file(text):
    """KEY=VALUE lines from .env (empty values skipped, surrounding quotes removed)."""
    out = {}
    for line in text.splitlines():
        m = re.match(r'^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$', line)
        if m and m.group(2):
            out[m.group(1)] = m.group(2).strip('"')
    return out


def load_env(root):
    """Real environment variables win; otherwise .env values are set for this process only."""
    f = Path(root) / '.env'
    if f.exists():
        for k, v in parse_env_file(read_text(f)).items():
            if not os.environ.get(k):
                os.environ[k] = v


def windows_user_env(name):
    """A user-level variable set after this shell started (Windows only)."""
    if not IS_WIN:
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def parse_project(text):
    """PROJECT.md: the ```settings block, the '## Worker notes' section and the Private flag."""
    proj = {'check_one': '', 'check_all': '', 'check_dir': ''}
    m = re.search(r'```settings\s*\r?\n(.*?)```', text, re.S)
    if m:
        for line in re.split(r'\r?\n', m.group(1)):
            lm = re.match(r'^\s*(\w+)\s*:\s*(.*?)\s*$', line)
            if lm:
                proj[lm.group(1)] = lm.group(2).strip('`')
    notes = ''
    m = re.search(r'## Worker notes[^\n]*\n(.*?)(?=\n## |\Z)', text, re.S)
    if m:
        notes = m.group(1).strip()
    if notes.startswith('- <'):   # template placeholder, not filled in
        notes = ''
    private = bool(re.search(r'\*\*Private\?\*\*\s*yes', text))   # private: only agy (and --local)
    return proj, notes, private


def parse_role(text):
    """Role file = frontmatter (base, models, does) + extra instructions. Returns (fields, body)."""
    m = re.match(r'^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?(.*)$', text, re.S)
    if not m:
        return {}, text
    fm = {}
    for line in re.split(r'\r?\n', m.group(1)):
        lm = re.match(r'^\s*(\w+)\s*:\s*(.*?)\s*$', line)
        if lm:
            fm[lm.group(1)] = lm.group(2)
    return fm, m.group(2).strip()


def to_worker(s):
    """'agy:gemini-3.8-flash-low' | 'nvidia' | 'openrouter:vendor/model:free'"""
    head, _, rest = s.strip().partition(':')
    return {'h': 'agy', 'm': rest} if head == 'agy' else {'h': 'cp', 'p': head, 'pm': rest}


def parse_chain(models):
    return [to_worker(x) for x in models.split(',') if x.strip()]


def parse_available(text):
    """SETUP.md ```available block -> {'agy': True, ...}, or None when there is no block."""
    m = re.search(r'```available\s*\r?\n(.*?)```', text, re.S)
    if not m:
        return None
    out = {}
    for line in re.split(r'\r?\n', m.group(1)):
        lm = re.match(r'^\s*(\w+)\s*:\s*(yes|no)', line)
        if lm:
            out[lm.group(1)] = lm.group(2) == 'yes'
    return out


def is_available(w, available):
    if available is None:
        return True
    return bool(available.get('agy' if w['h'] == 'agy' else w['p']))


def copilot_tools(base, check_dir):
    """Copilot tools per role: only what the role needs (each tool definition is resent on every call).
    No worker gets a shell tool: this script runs the check command itself."""
    tester_write = f'write({check_dir}/**)' if check_dir else 'write'
    return {
        'builder': {'tools': 'view,create,edit,grep,glob', 'allow': ['write']},
        'tester': {'tools': 'view,create,edit,grep,glob', 'allow': [tester_write]},
        'scribe': {'tools': 'view,create,edit,grep,glob', 'allow': ['write(**/*.md)']},
        'reviewer': {'tools': 'view,grep,glob', 'allow': []},
    }[base]


def check_command(check_one, part):
    """check_one with {name} filled in -> (command, ''), or (None, why it can't run)."""
    cmd = (check_one or '').strip()
    if not cmd:
        return None, 'no check command in PROJECT.md'
    if '{name}' in cmd:
        if not part:
            return None, 'give --part <name> to fill {name} in check_one'
        cmd = cmd.replace('{name}', part)
    return cmd, ''


def check_text(check):
    """A check result as text for a worker or the summary."""
    if check.get('skipped'):
        return f"not run ({check['skipped']})"
    head = 'PASS' if check['passed'] else ('FAIL: timed out' if check['timed_out'] else f"FAIL (exit {check['exit']})")
    return f"{head} | `{check['cmd']}`"


def fix_prompt(base, check):
    who = ('If a test is wrong, fix the test. If the work itself is wrong, change nothing and list it under Failures.'
           if base == 'tester' else 'Fix the real cause, only in the files your task names.')
    return (f"The Lead's script ran the check on your part: {check_text(check)}\n\nOutput (last lines):\n```\n{check['output']}\n```\n\n"
            f'{who} Run no commands: the check runs again after you. End with the report.')


def build_prompt(task, base, role, role_body, rules, proj, notes, includes, conversation, check=None):
    if not conversation:
        if role_body:
            rules += f'\n## Your role: {role}\n{role_body}\n'
        if base == 'reviewer' and check and not check.get('skipped'):
            how = (f"Run no commands. The Lead's script just ran the check: {check_text(check)}\n"
                   f"Output (last lines):\n```\n{check['output']}\n```")
        elif base in ('builder', 'tester') and proj['check_one']:
            how = ("Run no commands. When you are done, the Lead's script runs the check on your part "
                   f"(`{proj['check_one']}`). If it fails you get the output once to fix it.")
        elif base == 'builder':
            how = 'This project has no check command: run no commands. Re-read your files against the spec instead.'
        else:
            how = 'Run no commands.'
        task = rules + f"\n---\n# PROJECT\n{how}{chr(10) + notes if notes else ''}\n\n---\n# YOUR TASK\n\n" + task
    for f in includes:
        lines = read_text(f).splitlines()
        body = '\n'.join(f'{n:>4}  {line}' for n, line in enumerate(lines, 1))
        task += f'\n\n## File: {f} (line numbers added; do not copy them)\n```\n{body}\n```'
    if includes:
        task += "\n\nThe files above are current. Don't re-read them; read others only if the task needs them."
    return task


def git_snapshot(root):
    """Every file git sees as changed or new -> {path: (status, content hash)}.
    The hash also shows a second edit to a file that was already changed before the worker started."""
    r = subprocess.run(['git', '-C', str(root), 'status', '--porcelain', '-z', '-uall'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    snap, parts, i = {}, r.stdout.split('\0'), 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        if 'R' in xy or 'C' in xy:   # rename/copy: the next field is the old path
            i += 1
        if re.search(r'__pycache__|(^|/)\.worker/', path):
            continue
        try:
            digest = hashlib.sha1((Path(root) / path).read_bytes()).hexdigest()
        except OSError:
            digest = None   # deleted
        snap[path] = (xy, digest)
    return snap


def changed_since(before, after):
    """['?? new.py', ' M edited.py', '-- back_to_clean.py'] for everything that differs between two snapshots."""
    out = [f'{after[p][0]} {p}' for p in sorted(after) if after[p] != before.get(p)]
    return out + [f'-- {p}' for p in sorted(before) if p not in after]


def in_scope(line, patterns):
    """Is this changed-file line one of the files (or globs) the task may touch?"""
    path = line[3:].replace('\\', '/')
    return any(fnmatch.fnmatchcase(path, re.sub(r'^\./', '', pat.replace('\\', '/'))) for pat in patterns)


def split_scope(lines, patterns):
    """(own changes, changes outside --files). Without --files everything counts as own."""
    if not patterns:
        return lines, []
    return [l for l in lines if in_scope(l, patterns)], [l for l in lines if not in_scope(l, patterns)]


def kill_tree(proc):
    """Stop a worker and everything it started."""
    if proc.poll() is not None:
        return
    if IS_WIN:
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)   # started with start_new_session=True
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_tool(args, *, stdin_bytes=None, env=None, timeout=None, watch=None, keep_stderr=True):
    """Run a worker CLI with stdout (+stderr unless keep_stderr=False) in a temp file (no pipe deadlocks).
    watch() is called every 3 s and returns True to stop early. Returns (output text, 'timeout' | 'stopped' | None)."""
    exe = shutil.which(args[0])
    if not exe:
        raise Fail(f'{args[0]} not found on PATH (installed just now? restart the terminal/app).', 3)
    text, why, _ = _run([exe] + args[1:], stdin_bytes=stdin_bytes, env=env, timeout=timeout, watch=watch, keep_stderr=keep_stderr)
    return text, why


def run_check(cmd, timeout):
    """Run the project's check command (a shell line from PROJECT.md) in the project root."""
    text, why, rc = _run(cmd, shell=True, timeout=timeout)
    tail = '\n'.join(text.strip().splitlines()[-CHECK_TAIL_LINES:])[-CHECK_TAIL_CHARS:]
    return {'cmd': cmd, 'passed': rc == 0 and not why, 'exit': rc, 'timed_out': why == 'timeout', 'output': tail, 'fix_rounds': 0}


def _run(cmd, *, shell=False, stdin_bytes=None, env=None, timeout=None, watch=None, keep_stderr=True):
    """Returns (output text, 'timeout' | 'stopped' | None, exit code)."""
    out_f = tempfile.TemporaryFile()
    in_f = None
    if stdin_bytes is not None:
        in_f = tempfile.TemporaryFile()
        in_f.write(stdin_bytes)
        in_f.seek(0)
    kw = {} if IS_WIN else {'start_new_session': True}
    proc = subprocess.Popen(cmd, shell=shell, stdin=in_f or subprocess.DEVNULL, stdout=out_f,
                            stderr=subprocess.STDOUT if keep_stderr else subprocess.DEVNULL, env=env, cwd=str(ROOT), **kw)
    start = time.monotonic()
    why = None
    while True:
        try:
            proc.wait(timeout=3)
            break
        except subprocess.TimeoutExpired:
            pass
        if timeout and time.monotonic() - start >= timeout:
            why = 'timeout'
        elif watch and watch():
            why = 'stopped'
        if why:
            kill_tree(proc)
            break
    out_f.seek(0)
    text = out_f.read().decode('utf-8', errors='replace')
    out_f.close()
    if in_f:
        in_f.close()
    return text, why, proc.returncode


# --- agy -----------------------------------------------------------------------------------------
def parse_agy_stream(stream, model):
    result = None
    for line in stream.splitlines():
        if line.startswith('{"event":"result"'):
            try:
                result = json.loads(line).get('result')
            except ValueError:
                pass
    if result:
        err = str(result.get('error') or '')
    else:
        err = ' '.join(stream.splitlines()[-3:])
    r = result or {}
    u = r.get('usage') or {}
    o = {
        'worker': f'agy {model}', 'conversation_id': r.get('conversation_id'), 'response': r.get('response'), 'error': err,
        # agy doesn't report model calls (num_turns counts user turns), so calls = '?'
        'calls': '?', 'input_tokens': u.get('input_tokens'), 'cache_read_tokens': u.get('cache_read_tokens'),
        'output_tokens': u.get('output_tokens'), 'thinking_tokens': u.get('thinking_tokens'),
        'denied': [d.get('display_name') for d in (r.get('denied_actions') or []) if d],
    }
    if str(r.get('finished_reason')) == 'print_timeout':
        o['code'] = 3
    elif not result or (str(r.get('status')) == 'ERROR' and not r.get('response')):
        o['code'] = 6
    elif o['denied']:
        o['code'] = 4
    elif not r.get('response'):
        o['code'] = 5
    else:
        o['code'] = 0
    return o


def invoke_agy(w, ctx):
    a = ctx.args
    prompt = ctx.task
    if ctx.base in ('builder', 'tester'):
        prompt += '\n\nREMINDER: run no commands. End with the report.'
    cmd = ['agy', '--input-format', 'stream-json', '--output-format', 'stream-json', '--model', w['m'],
           '--print-timeout', f'{a.timeout_sec}s', '--mode', 'accept-edits']
    if not a.no_agent:
        cmd += ['--agent', f'w-{ctx.base}']
    if not a.no_sandbox:
        cmd += ['--sandbox']
    if a.conversation:
        cmd += ['--conversation', a.conversation]
    if a.schema:
        cmd += ['--json-schema', str(Path(a.schema).resolve())]
    # Prompt via stdin: one NDJSON message (no argv length limit, no quote escaping).
    line = json.dumps({'event': 'user', 'message': {'content': prompt}}) + '\n'
    # agy stops itself at --print-timeout; the extra minute only catches a hung process.
    stream, why = run_tool(cmd, stdin_bytes=line.encode('utf-8'), timeout=a.timeout_sec + 60)
    o = parse_agy_stream(stream, w['m'])
    if why == 'timeout':
        o['code'] = 3
    return o


# --- Copilot + cloud API ----------------------------------------------------------------------
def log_tokens(log_dir):
    """Copilot writes token counts only to its debug log (also while running)."""
    r = {'in': 0, 'out': 0, 'cached': 0, 'last': 0}
    last_in = last_out = 0
    for f in sorted(glob.glob(os.path.join(str(log_dir), '*.log'))):
        try:
            text = Path(f).read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for v in re.findall(r'"prompt_tokens": (\d+)', text):
            last_in = int(v)
            r['in'] += last_in
        for v in re.findall(r'"completion_tokens": (\d+)', text):
            last_out = int(v)
            r['out'] += last_out
        r['cached'] += sum(int(v) for v in re.findall(r'"cached_tokens": (\d+)', text))
    r['last'] = last_in + last_out   # size of the latest call
    r['total'] = r['in'] + r['out']
    return r


def openai_tier(model):
    for t in ('big', 'mini'):
        if model in OPENAI_FREE[t]['models']:
            return t
    return None


def openai_used_today(log_file, tier):
    day, total = utc_day(), 0
    if Path(log_file).exists():
        for line in read_text(log_file).splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get('day') == day and e.get('tier') == tier:
                total += int(e.get('tokens') or 0)
    return total


def parse_copilot_events(text):
    ev = []
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            ev.append(e)
    final = next((e for e in reversed(ev) if e.get('type') == 'result'), {})
    err = next((e for e in reversed(ev) if e.get('type') == 'session.error'), None)
    starts = {(e.get('data') or {}).get('toolCallId'): e.get('data') or {} for e in ev if e.get('type') == 'tool.execution_start'}
    denied = []
    for e in ev:
        d = e.get('data') or {}
        if e.get('type') == 'tool.execution_complete' and ((d.get('error') or {}).get('code')) == 'denied':
            s = starts.get(d.get('toolCallId'), {})
            args = s.get('arguments') or {}
            denied.append(f"{s.get('toolName')}: {args.get('command') or ''}{args.get('path') or ''}")
    msgs = [e for e in ev if e.get('type') == 'assistant.message' and (e.get('data') or {}).get('content')]
    return {
        'conversation_id': final.get('sessionId'),
        'response': msgs[-1]['data']['content'] if msgs else None,
        'error': str(((err or {}).get('data') or {}).get('message') or ''),
        'calls': sum(1 for e in ev if e.get('type') == 'model.call_start'),
        'denied': denied,
    }


def invoke_copilot(w, ctx):
    a = ctx.args
    pv = PROVIDERS.get(w['p'])
    if not pv:
        raise Fail(f"Unknown provider '{w['p']}' (use {', '.join(PROVIDERS)}).", 2)
    model = pv['model'] if not w['pm'] or w['pm'] == 'free' else w['pm']
    tier = limit = used = None
    if w['p'] == 'openai':
        tier = openai_tier(model)
        if not tier:
            return {'worker': f'openai {model}', 'code': 6, 'calls': 0, 'denied': [],
                    'error': f'REFUSED: {model} is not on the free list (would bill the prepaid credit). See MODELS.md.'}
        used, limit = openai_used_today(ctx.openai_log, tier), OPENAI_FREE[tier]['limit']
        if used >= 0.9 * limit:
            return {'worker': f'openai {model}', 'code': 6, 'calls': 0, 'denied': [],
                    'error': f'REFUSED: OpenAI {tier} free tier at {used} of {limit} tokens today (stop at 90%; capacity).'}
    if not shutil.which('copilot'):
        raise Fail('copilot not found on PATH.', 3)
    key = os.environ.get(pv['key']) or windows_user_env(pv['key'])
    if not key:
        raise Fail(f"Put {pv['key']}=... in .env (see .env.example).", 3)
    # Only the worker's environment: the user's own copilot setup is untouched.
    env = dict(os.environ)
    for k in ('COPILOT_PROVIDER_BEARER_TOKEN', 'COPILOT_PROVIDER_WIRE_MODEL', 'COPILOT_PROVIDER_MODEL_ID', 'COPILOT_PROVIDER_WIRE_API'):
        env.pop(k, None)
    env.update({'COPILOT_PROVIDER_BASE_URL': pv['url'], 'COPILOT_PROVIDER_TYPE': 'openai', 'COPILOT_PROVIDER_API_KEY': key,
                'COPILOT_MODEL': model, 'COPILOT_PROVIDER_MAX_PROMPT_TOKENS': '128000'})
    if w['p'] == 'openai':
        env['COPILOT_PROVIDER_WIRE_API'] = 'responses' if re.match(r'^(gpt-5|o\d)', model) else 'completions'

    t = copilot_tools(ctx.base, ctx.proj['check_dir'])
    log_dir = Path(tempfile.gettempdir()) / f'cp-log-{uuid.uuid4()}'
    cmd = ['copilot', '-p', ctx.task, '--output-format', 'json', '--no-color', '--no-ask-user', '--no-custom-instructions',
           '--disable-builtin-mcps', '--disable-mcp-server', 'ddg-search',
           '--secret-env-vars', 'COPILOT_PROVIDER_API_KEY,OPENROUTER_API_KEY,NVIDIA_API_KEY,GEMINI_API_KEY,OPENAI_API_KEY',
           f"--available-tools={t['tools']}", '--allow-tool', 'read', '--deny-tool', 'shell(git:*)', '--deny-tool', 'url',
           '--log-level', 'debug', '--log-dir', str(log_dir)]
    for x in t['allow']:
        cmd += ['--allow-tool', x]
    if not a.no_sandbox:
        cmd += ['--sandbox']
    if a.conversation:
        cmd += ['--resume', a.conversation]

    # OpenAI: cap tokens DURING the run (a looping worker once used 1.4M in one run).
    # Cap = what is left below 90% of today's free tier, and at most OPENAI_RUN_CAP per run.
    cap = 0
    if w['p'] == 'openai':
        run_cap = int(os.environ.get('DELEGATE_OPENAI_RUN_CAP') or OPENAI_RUN_CAP[tier])   # env: for testing
        cap = min(int(0.9 * limit) - used, run_cap)

    def over_cap():   # look one call ahead: calls only grow (history is resent)
        if cap <= 0:
            return False
        lt = log_tokens(log_dir)
        return lt['total'] + lt['last'] >= cap

    text, why = run_tool(cmd, env=env, timeout=a.timeout_sec, watch=over_cap)
    p = parse_copilot_events(text)
    tok = log_tokens(log_dir)
    shutil.rmtree(log_dir, ignore_errors=True)
    o = {'worker': f"{w['p']} {model}", 'conversation_id': p['conversation_id'], 'response': p['response'], 'error': p['error'],
         'calls': p['calls'], 'input_tokens': tok['in'], 'cache_read_tokens': tok['cached'], 'output_tokens': tok['out'],
         'thinking_tokens': 0, 'denied': p['denied']}
    if why == 'stopped':
        o['error'] = f'STOPPED: OpenAI token cap {cap} reached during the run (free-tier guard).'
    o['code'] = (3 if why == 'timeout' else 6 if why == 'stopped' or p['error'] else 4 if p['denied']
                 else 5 if not p['response'] else 0)
    if w['p'] == 'openai':   # count free-tier usage per UTC day
        with open(ctx.openai_log, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'day': utc_day(), 'tier': tier, 'model': model, 'tokens': tok['in'] + tok['out']}) + '\n')
    return o


# --- Main ------------------------------------------------------------------------------------------
class Ctx:
    pass


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--task', help='the task text')
    ap.add_argument('--task-file', help='file with the task')
    ap.add_argument('--role', default='builder', help='any agent/roles/<name>.md (default builder)')
    ap.add_argument('--include', nargs='+', default=[], metavar='FILE',
                    help='files appended to the prompt with line numbers (saves exploring calls)')
    ap.add_argument('--model', choices=AGY_MODELS, help='force one agy worker (no chain, no fallback)')
    ap.add_argument('--provider', choices=list(PROVIDERS), help='force one Copilot+API worker')
    ap.add_argument('--provider-model', help='model id for --provider')
    ap.add_argument('--openrouter', metavar='ID', help='shortcut: --provider openrouter --provider-model ID')
    ap.add_argument('--conversation', help='follow-up on an earlier result (max 2)')
    ap.add_argument('--schema', help='agy: JSON schema file for the final result')
    ap.add_argument('--files', nargs='+', default=[], metavar='FILE',
                    help='the files (or globs) this task may change; other changes are listed apart')
    ap.add_argument('--part', metavar='NAME', help='the part this task is about: fills {name} in check_one')
    ap.add_argument('--no-check', action='store_true', help='do not run check_one for this task')
    ap.add_argument('--no-fix', action='store_true', help='run the check, but do not send a failure back to the worker')
    ap.add_argument('--check-timeout-sec', type=int, default=300)
    ap.add_argument('--timeout-sec', type=int, default=480)
    ap.add_argument('--no-sandbox', action='store_true', help='only if the check must write outside this folder')
    ap.add_argument('--no-agent', action='store_true', help='agy: full default tool set (debugging only)')
    ap.add_argument('--json', action='store_true', help='print the full JSON instead of the summary')
    ap.add_argument('--local', action='store_true', help='Ollama, text only')
    ap.add_argument('--local-model', default='gemma4:12b')
    return ap.parse_args(argv)


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except AttributeError:
            pass
    a = parse_args(argv)
    os.chdir(ROOT)
    (ROOT / '.worker').mkdir(exist_ok=True)   # git-ignored, so missing in a fresh clone
    load_env(ROOT)

    proj, notes, private = {'check_one': '', 'check_all': '', 'check_dir': ''}, '', False
    if (ROOT / 'PROJECT.md').exists():
        proj, notes, private = parse_project(read_text(ROOT / 'PROJECT.md'))
    if a.part and not re.fullmatch(r'[\w./\\-]+', a.part):   # it becomes part of a shell line
        raise Fail(f"--part '{a.part}': only letters, digits and _ . / - are allowed.", 2)

    role_file = AGENT_DIR / 'roles' / f'{a.role}.md'
    if not role_file.exists():
        have = ', '.join(sorted(p.stem for p in (AGENT_DIR / 'roles').glob('*.md')))
        raise Fail(f"No role '{a.role}'. Roles: {have} (agent/roles/). New role: copy templates/roles/ROLE-TEMPLATE.md.", 2)
    fm, role_body = parse_role(read_text(role_file))
    base = fm.get('base') or 'builder'
    if base not in BASES:
        raise Fail(f"Role '{a.role}': base must be builder, tester, reviewer or scribe (got '{base}').", 2)
    model_list = fm.get('models') or DEFAULT_MODELS[base]

    available = parse_available(read_text(ROOT / 'SETUP.md')) if (ROOT / 'SETUP.md').exists() else None
    if private and (a.provider or a.openrouter):
        raise Fail('PROJECT.md says this project is private: cloud API workers are not allowed. Use agy (--model), --local, or do it yourself.', 6)
    if a.openrouter:
        a.provider = 'openrouter'
        a.provider_model = a.provider_model or a.openrouter
    if a.model:
        chain = [{'h': 'agy', 'm': a.model}]   # forced: no availability filter, no fallback
    elif a.provider:
        chain = [{'h': 'cp', 'p': a.provider, 'pm': a.provider_model or ''}]
    else:
        chain = [w for w in parse_chain(model_list) if is_available(w, available) and not (private and w['h'] != 'agy')]
        if not chain:
            raise Fail(f"Role '{a.role}': none of its workers ({model_list}) is available on this machine (SETUP.md). Add one, or do it yourself.", 6)

    task = read_text(a.task_file) if a.task_file else a.task
    if not task:
        raise Fail('Give --task "..." or --task-file <path>.', 2)
    rules = read_text(AGENT_DIR / 'bases' / f'{base}.md')

    # The check is run here, never by a worker. Reviewer: before, so it can read the result.
    check = None
    check_cmd, why_not = (None, '') if a.no_check or a.local or base == 'scribe' else check_command(proj['check_one'], a.part)
    if why_not and proj['check_one'].strip():
        check = {'skipped': why_not}
    if check_cmd and base == 'reviewer' and not a.conversation:
        check = run_check(check_cmd, a.check_timeout_sec)
    task = build_prompt(task, base, a.role, role_body, rules, proj, notes, a.include, a.conversation, check)

    if a.local:   # local Ollama, text only
        if not shutil.which('ollama'):
            raise Fail('ollama not found on PATH.', 3)
        task += ('\n\nNOTE: you cannot edit files or run commands. Reply with the COMPLETE contents of every file you change, '
                 'each in a fenced block whose first line is a comment with its path. Then the report.')
        # stderr (spinner, a freshly started Ollama server's log) is dropped; terminal codes are stripped.
        text, why = run_tool(['ollama', 'run', a.local_model, '--hidethinking', '--nowordwrap'],
                             stdin_bytes=task.encode('utf-8'), timeout=a.timeout_sec, keep_stderr=False)
        print(re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', text).strip())
        if why == 'timeout':
            print(f'ERROR: no answer within {a.timeout_sec}s (TIMEOUT).', file=sys.stderr)
            return 3
        return 0

    ctx = Ctx()
    ctx.args, ctx.task, ctx.base, ctx.proj = a, task, base, proj
    ctx.openai_log = ROOT / '.worker' / 'openai-usage.jsonl'

    # Run the chain: transient provider error -> retry once -> next worker.
    before = git_snapshot(ROOT)
    start = time.monotonic()
    tried, res, changed = [], None, False
    for w in chain:
        for attempt in (1, 2):
            res = invoke_agy(w, ctx) if w['h'] == 'agy' else invoke_copilot(w, ctx)
            tried.append(f"{res['worker']}={res['code']}")
            # With --files, another worker's changes elsewhere don't count as this worker's.
            changed = bool(split_scope(changed_since(before, git_snapshot(ROOT)), a.files)[0])
            transient = res['code'] == 6 and not changed and TRANSIENT.search(str(res.get('error') or ''))
            if not transient or attempt == 2:
                break
            time.sleep(20)
        if res['code'] != 6 or changed:
            break

    # Builder/tester: run the check now; on failure the same worker gets the output once, then it runs again.
    if check_cmd and base in ('builder', 'tester') and res['code'] in (0, 3):
        check = run_check(check_cmd, a.check_timeout_sec)
        if not check['passed'] and res['code'] == 0 and res.get('conversation_id') and not a.no_fix:
            a.conversation, ctx.task = res['conversation_id'], fix_prompt(base, check)
            fix = invoke_agy(w, ctx) if w['h'] == 'agy' else invoke_copilot(w, ctx)
            tried.append(f"{fix['worker']}=fix:{fix['code']}")
            for k in ('calls', 'input_tokens', 'cache_read_tokens', 'output_tokens', 'thinking_tokens'):
                if isinstance(res.get(k), int) and isinstance(fix.get(k), int):
                    res[k] += fix[k]
            res['denied'] = (res.get('denied') or []) + (fix.get('denied') or [])
            if fix.get('response'):
                res['response'] = fix['response']
            if fix['code'] != 0:
                res['error'] = f"fix round ended with {LABELS.get(fix['code'], fix['code'])}: {fix.get('error') or ''}".strip()
            check = run_check(check_cmd, a.check_timeout_sec)
            check['fix_rounds'] = 1
        if res['code'] == 0 and not check['passed']:
            res['code'] = 7
    res['check'] = check
    own, outside = split_scope(changed_since(before, git_snapshot(ROOT)), a.files)
    run_file = f'.worker/run-{uuid.uuid4().hex[:8]}.json'   # one per run: parallel workers don't overwrite each other
    res.update({'role': a.role, 'seconds': int(time.monotonic() - start), 'tried': tried, 'run_file': run_file,
                'files_changed': [c.strip() for c in own], 'files_outside': [c.strip() for c in outside]})
    for k in ('conversation_id', 'response', 'error', 'input_tokens', 'cache_read_tokens', 'output_tokens', 'thinking_tokens'):
        res.setdefault(k, None)

    json_out = json.dumps(res, indent=2, ensure_ascii=False)
    for f in (run_file, '.worker/last.json'):
        (ROOT / f).write_text(json_out, encoding='utf-8')
    if a.json:
        print(json_out)
    else:
        none = lambda v: '' if v is None else v
        print(f"[{LABELS.get(res['code'], res['code'])}] {a.role} via {res['worker']} | {res['seconds']}s | {res['calls']} calls | "
              f"in {none(res['input_tokens'])} (+cache {none(res['cache_read_tokens'])}) out {none(res['output_tokens'])}")
        if len(tried) > 1:
            print('tried: ' + ', '.join(tried))
        print('files: ' + ('; '.join(res['files_changed']) or 'none'))
        if res['files_outside']:
            print('NOT IN --files (another worker, or this one broke its scope): ' + '; '.join(res['files_outside']))
        if check:
            print('check: ' + check_text(check) + (' | after 1 fix round' if check.get('fix_rounds') else ''))
            if check.get('passed') is False:
                print(check['output'])
        if res['denied']:
            print('denied: ' + '; '.join(res['denied']))
        if res['error']:
            print(f"error: {res['error']}")
        print(f"conversation: {none(res['conversation_id'])} | saved: {run_file}")
        print('--- report ---')
        print(str(none(res['response'])).strip())
    return int(res['code'])


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Fail as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(e.code)
