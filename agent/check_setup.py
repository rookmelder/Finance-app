#!/usr/bin/env python3
"""Find which worker tools and API keys this machine has, test each key with a free call,
and write SETUP.md (git-ignored). delegate.py skips workers marked "no" there.
Run it at the start of a project and whenever you add a tool or key:
  python agent/check_setup.py            (--offline: only check that keys exist)
Never prints key values. Standard library only; Windows, Linux and macOS.
"""
import datetime
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from delegate import IS_WIN, ROOT, parse_env_file, read_text  # noqa: E402

TOOLS = {
    'git': 'Required: tracks what workers change.',
    'agy': 'Google Antigravity CLI: runs Gemini and agy Claude/gpt-oss workers (needs a Google AI Pro plan for real quota).',
    'copilot': 'GitHub Copilot CLI: runs every API worker (NVIDIA, Gemini API, OpenRouter, OpenAI).',
    'ollama': 'Optional: private local model.',
    'python': 'Only if the project uses Python.',
    'node': 'Only if the project uses JavaScript/TypeScript.',
}
# Each test is free: a model list or a 1-token call. (url, header name, header prefix, POST body or None)
PROBES = {
    'nvidia': ('NVIDIA_API_KEY', 'https://integrate.api.nvidia.com/v1/chat/completions', 'Authorization', 'Bearer ',
               b'{"model":"mistralai/mistral-large-4-1t-a52b","messages":[{"role":"user","content":"hi"}],"max_tokens":1}', 60),
    'gemini': ('GEMINI_API_KEY', 'https://generativelanguage.googleapis.com/v1beta/models?pageSize=1', 'x-goog-api-key', '', None, 20),
    'openrouter': ('OPENROUTER_API_KEY', 'https://openrouter.ai/api/v1/key', 'Authorization', 'Bearer ', None, 20),
    'openai': ('OPENAI_API_KEY', 'https://api.openai.com/v1/models', 'Authorization', 'Bearer ', None, 20),
}


def refresh_path():
    """Windows: add the saved Machine/User PATH, so tools installed after this shell started are found."""
    if not IS_WIN:
        return
    import winreg
    parts = []
    for hive, sub in ((winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'),
                      (winreg.HKEY_CURRENT_USER, 'Environment')):
        try:
            with winreg.OpenKey(hive, sub) as k:
                parts.append(os.path.expandvars(winreg.QueryValueEx(k, 'Path')[0]))
        except OSError:
            pass
    os.environ['PATH'] = os.pathsep.join(parts + [os.environ.get('PATH', '')])


def has(cmd):
    return bool(shutil.which(cmd) or (cmd == 'python' and shutil.which('python3')))


def ollama_models():
    # Read the model folder instead of `ollama list`: that can start the Ollama server (hang).
    man = Path(os.environ['OLLAMA_MODELS']) / 'manifests' if os.environ.get('OLLAMA_MODELS') else Path.home() / '.ollama' / 'models' / 'manifests'
    if not has('ollama') or not man.exists():
        return ''
    return ', '.join(f'{p.parent.name}:{p.name}' for p in sorted(man.rglob('*')) if p.is_file())


def test_key(key, url, header, prefix, body, timeout):
    req = urllib.request.Request(url, data=body, headers={header: prefix + key, 'Content-Type': 'application/json'},
                                 method='POST' if body else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return 'key works', True
    except urllib.error.HTTPError as e:
        return f'key FAILED (HTTP {e.code})', False
    except Exception:
        return 'key FAILED (no answer)', False


def build_md(rows, avail, models, now):
    table = '\n'.join(f'| {i} | {s} | {n} |' for i, s, n in rows)
    block = '\n'.join(f"{k}: {'yes' if v else 'no'}" for k, v in avail.items())
    return f"""# Setup on this machine

Made by `agent/check_setup.py` on {now}. Not committed (each machine has its own).
Run it again after installing a tool or adding a key. How to set each one up: `agent/setup/`.

| Item | Status | Note |
|---|---|---|
{table}

Ollama models: {models or 'none'}

## Available workers (read by delegate.py; "no" = skipped)
```available
{block}
```

## Confirmed by the user (the Lead asks once and fills this in)
- Claude plan: <which plan>
- Google AI Pro (agy quota): <yes/no>
- OpenAI data sharing ON (needed for the free daily tokens): <yes/no/no key>
- OpenRouter credits ever bought >= $10 (1000 instead of 50 free requests/day): <yes/no/no key>
- Private project allowed on free cloud tiers: see PROJECT.md
"""


def keep_confirmed(new_md, old_md):
    """Keep the user's answers from a previous run (only the detected part is refreshed)."""
    m = re.search(r'(## Confirmed by the user.*)$', old_md or '', re.S)
    if not m:
        return new_md
    return re.sub(r'## Confirmed by the user.*$', '', new_md, flags=re.S) + m.group(1).rstrip() + '\n'


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    offline = '--offline' in argv
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass
    os.chdir(ROOT)
    refresh_path()
    keys = parse_env_file(read_text('.env')) if Path('.env').exists() else {}

    rows, avail = [], {}
    for t, note in TOOLS.items():
        rows.append((t, 'found' if has(t) else 'missing', note))
    avail['agy'] = has('agy')
    models = ollama_models()
    avail['ollama'] = bool(models)

    for p, (key_name, url, header, prefix, body, timeout) in PROBES.items():
        k = os.environ.get(key_name) or keys.get(key_name)
        if not k:
            status, ok = 'no key', False
        elif offline:
            status, ok = 'key present (not tested)', True
        else:
            status, ok = test_key(k, url, header, prefix, body, timeout)
        note = 'Copilot CLI missing: API workers need it.' if ok and not has('copilot') else f'Key name in .env: {key_name}'
        rows.append((f'{p} API', status, note))
        avail[p] = ok and has('copilot')

    md = build_md(rows, avail, models, datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))
    setup = ROOT / 'SETUP.md'
    md = keep_confirmed(md, read_text(setup) if setup.exists() else '')
    setup.write_text(md, encoding='utf-8', newline='\n')

    w = [max(len(r[i]) for r in rows + [('Item', 'Status', 'Note')]) for i in range(2)]
    print(f"{'Item':<{w[0]}}  {'Status':<{w[1]}}  Note")
    for i, s, n in rows:
        print(f'{i:<{w[0]}}  {s:<{w[1]}}  {n}')
    print('\nAvailable: ' + ', '.join(k for k, v in avail.items() if v))
    print('Written: SETUP.md')
    return 0


if __name__ == '__main__':
    sys.exit(main())
