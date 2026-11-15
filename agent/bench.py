#!/usr/bin/env python3
"""Rate your own worker models: the same small tasks on each worker, scored with tests the worker never sees.

  python agent/bench.py --workers agy:gemini-3.8-flash-low nvidia openrouter:vendor/model:free
  python agent/bench.py --workers nvidia:mistralai/mistral-large-4-1t-a52b --tasks textkit

A worker is written like an entry in a role's models: line (agy:<model>, <provider>, <provider>:<model id>).
Each run happens in a throw-away copy of this setup, as a builder task through delegate.py (so the check and
the fix round count too). Then the hidden tests of the task are run on what the worker made.
Tasks: agent/bench/<name>/ = task.md, task.json, start/ (files the worker starts with), hidden/ (the tests),
solution/ (a correct answer, used to test the tests). Add your own the same way.
Output: a table to paste into agent/MODELS.md; every row is also appended to .worker/bench.jsonl.
The tasks are public toy code and go to the cloud providers you name. Needs Python only. Standard library only.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
ROOT = AGENT_DIR.parent
BENCH_DIR = AGENT_DIR / 'bench'
sys.path.insert(0, str(AGENT_DIR))
import delegate  # noqa: E402


def load_tasks(names=None):
    tasks = []
    for d in sorted(p for p in BENCH_DIR.iterdir() if (p / 'task.json').exists()):
        if names and d.name not in names:
            continue
        t = json.loads((d / 'task.json').read_text(encoding='utf-8'))
        t.update({'name': d.name, 'dir': d})
        tasks.append(t)
    return tasks


def worker_flags(spec):
    """'agy:m' -> --model m; 'nvidia' -> --provider nvidia; 'openrouter:a/b:free' -> --provider openrouter --provider-model a/b:free"""
    w = delegate.to_worker(spec)
    if w['h'] == 'agy':
        return ['--model', w['m']]
    return ['--provider', w['p']] + (['--provider-model', w['pm']] if w['pm'] else [])


def prepare(task, tmp):
    """A throw-away project: this setup's scripts and rules, the task's start files, a check command, its own git."""
    tmp = Path(tmp)
    shutil.copytree(AGENT_DIR, tmp / 'agent', ignore=shutil.ignore_patterns('tests', 'bench', '__pycache__', 'bench.py'))
    if (ROOT / '.agents').exists():
        shutil.copytree(ROOT / '.agents', tmp / '.agents')
    if (task['dir'] / 'start').exists():
        shutil.copytree(task['dir'] / 'start', tmp, dirs_exist_ok=True)
    (tmp / 'tests').mkdir(exist_ok=True)
    py = f'"{sys.executable}"' if ' ' in sys.executable else sys.executable
    (tmp / 'PROJECT.md').write_text(
        f'# Benchmark project\n```settings\ncheck_one: {py} -m unittest tests.test_{{name}} -v\ncheck_all:\ncheck_dir:\nprivate: no\n```\n'
        '## Worker notes\n- Python 3, standard library only. Tests use unittest.\n', encoding='utf-8')
    (tmp / '.gitignore').write_text('.worker/\n__pycache__/\n', encoding='utf-8')
    for cmd in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=bench', '-c', 'user.email=bench@example.invalid', 'commit', '-qm', 'start']):
        subprocess.run(['git', '-C', str(tmp)] + cmd, capture_output=True)


def score(tmp, task):
    """Run the task's hidden tests on the project in tmp -> how many passed."""
    tmp = Path(tmp)
    shutil.copytree(task['dir'] / 'hidden', tmp / 'hidden_tests', dirs_exist_ok=True)
    try:
        r = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'hidden_tests', '-v'], cwd=str(tmp),
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
    except subprocess.TimeoutExpired:
        return 0
    return len(re.findall(r'^test\w* \(.*\) \.\.\. ok$', r.stderr, re.M))


def run_one(spec, task, timeout_sec):
    tmp = tempfile.mkdtemp(prefix='bench-')
    row = {'date': datetime.date.today().isoformat(), 'worker': spec, 'task': task['name'], 'passed': 0, 'tests': task['tests']}
    try:
        prepare(task, tmp)
        cmd = [sys.executable, str(Path(tmp) / 'agent' / 'delegate.py'), '--role', 'builder', '--json', '--timeout-sec', str(timeout_sec),
               '--task-file', str(task['dir'] / 'task.md'), '--part', task['part'], '--files', *task['files']] + worker_flags(spec)
        if task.get('include'):
            cmd += ['--include', *task['include']]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        try:
            res = json.loads(r.stdout)
        except ValueError:
            res = {'code': r.returncode, 'error': (r.stderr or r.stdout).strip()[-300:]}
        check = res.get('check') or {}
        row.update({'exit': delegate.LABELS.get(res.get('code'), res.get('code')), 'model': res.get('worker'), 'seconds': res.get('seconds'),
                    'calls': res.get('calls'), 'tokens_in': res.get('input_tokens'), 'tokens_out': res.get('output_tokens'),
                    'own_check': 'pass' if check.get('passed') else 'fail' if 'passed' in check else '-',
                    'fix_rounds': check.get('fix_rounds', 0), 'error': res.get('error') or ''})
        row['passed'] = score(tmp, task)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return row


def table(rows):
    cols = [('worker', 'Worker'), ('task', 'Task'), ('score', 'Hidden tests'), ('exit', 'Result'), ('own_check', 'Own check'),
            ('fix_rounds', 'Fix rounds'), ('seconds', 'Seconds'), ('calls', 'Calls'), ('tokens_in', 'Tokens in'), ('tokens_out', 'Tokens out')]
    out = ['| ' + ' | '.join(h for _, h in cols) + ' |', '|' + '---|' * len(cols)]
    for r in rows:
        r = dict(r, score=f"{r['passed']}/{r['tests']}")
        out.append('| ' + ' | '.join('' if r.get(k) is None else str(r.get(k)) for k, _ in cols) + ' |')
    return '\n'.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--workers', nargs='+', required=True, metavar='WORKER', help='agy:<model>, <provider> or <provider>:<model id>')
    ap.add_argument('--tasks', nargs='+', help='task folder names in agent/bench/ (default: all)')
    ap.add_argument('--timeout-sec', type=int, default=480)
    a = ap.parse_args(argv)
    tasks = load_tasks(a.tasks)
    if not tasks:
        print('ERROR: no such task in agent/bench/.', file=sys.stderr)
        return 2
    delegate.load_env(ROOT)   # the throw-away projects have no .env: keys travel in the environment
    (ROOT / '.worker').mkdir(exist_ok=True)
    rows = []
    for spec in a.workers:
        for task in tasks:
            print(f"running {spec} on {task['name']} ...", file=sys.stderr, flush=True)
            row = run_one(spec, task, a.timeout_sec)
            rows.append(row)
            with open(ROOT / '.worker' / 'bench.jsonl', 'a', encoding='utf-8') as f:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
            if row.get('error'):
                print(f"  {row['exit']}: {row['error'][:200]}", file=sys.stderr)
    print(table(rows))
    print()
    for spec in a.workers:
        mine = [r for r in rows if r['worker'] == spec]
        print(f"{spec}: {sum(r['passed'] for r in mine)}/{sum(r['tests'] for r in mine)} hidden tests")
    return 0


if __name__ == '__main__':
    sys.exit(main())
