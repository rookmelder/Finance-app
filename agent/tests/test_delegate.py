"""Tests for agent/delegate.py and agent/check_setup.py. Run: python -m unittest discover -s agent/tests"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_setup  # noqa: E402
import delegate as d  # noqa: E402


class ParseFiles(unittest.TestCase):
    def test_env_file(self):
        env = d.parse_env_file('# comment\nA_KEY = "abc"\nEMPTY=\nlower=x\nB=1 2 \n')
        self.assertEqual(env, {'A_KEY': 'abc', 'B': '1 2'})

    def test_project_settings_notes_private(self):
        text = ('**Private?** yes\n## Settings\n```settings\ncheck_one: `python -m unittest tests.test_{name}`\n'
                'check_all: python -m unittest\ncheck_dir:\n```\n## Worker notes (x)\n- Python 3.12\n- no deps\n\n## Other\nz')
        proj, notes, private = d.parse_project(text)
        self.assertEqual(proj['check_one'], 'python -m unittest tests.test_{name}')
        self.assertEqual(proj['check_dir'], '')
        self.assertEqual(notes, '- Python 3.12\n- no deps')
        self.assertTrue(private)

    def test_project_template_placeholders(self):
        proj, notes, private = d.parse_project(d.read_text(d.ROOT / 'PROJECT.md')) if (d.ROOT / 'PROJECT.md').exists() else ({}, '', False)
        self.assertFalse(private)
        self.assertNotIn('- <', notes)

    def test_role(self):
        fm, body = d.parse_role('---\r\nbase: tester\r\nmodels: gemini, nvidia:x/y\r\n---\r\nExtra rule.\r\n')
        self.assertEqual(fm, {'base': 'tester', 'models': 'gemini, nvidia:x/y'})
        self.assertEqual(body, 'Extra rule.')
        self.assertEqual(d.parse_role('no frontmatter'), ({}, 'no frontmatter'))

    def test_every_role_file_parses(self):
        for f in (d.AGENT_DIR / 'roles').glob('*.md'):
            fm, _ = d.parse_role(d.read_text(f))
            self.assertIn(fm.get('base', 'builder'), d.BASES, f.name)
            self.assertTrue(d.parse_chain(fm.get('models') or d.DEFAULT_MODELS[fm.get('base', 'builder')]), f.name)

    def test_chain(self):
        c = d.parse_chain('agy:gemini-3.8-flash-low, nvidia, openrouter:nvidia/nemotron:free,')
        self.assertEqual(c, [{'h': 'agy', 'm': 'gemini-3.8-flash-low'}, {'h': 'cp', 'p': 'nvidia', 'pm': ''},
                             {'h': 'cp', 'p': 'openrouter', 'pm': 'nvidia/nemotron:free'}])

    def test_available(self):
        av = d.parse_available('x\n```available\nagy: yes\nnvidia: no\n```\n')
        self.assertEqual(av, {'agy': True, 'nvidia': False})
        self.assertIsNone(d.parse_available('nothing'))
        self.assertTrue(d.is_available({'h': 'agy', 'm': 'x'}, av))
        self.assertFalse(d.is_available({'h': 'cp', 'p': 'nvidia', 'pm': ''}, av))
        self.assertFalse(d.is_available({'h': 'cp', 'p': 'gemini', 'pm': ''}, av))
        self.assertTrue(d.is_available({'h': 'cp', 'p': 'gemini', 'pm': ''}, None))


class Prompt(unittest.TestCase):
    def test_build_prompt_and_include(self):
        with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write('a = 1\nb = 2\n')
        try:
            proj = {'check_one': 'python -m unittest', 'check_all': '', 'check_dir': ''}
            p = d.build_prompt('Do X.', 'builder', 'builder', 'Role rule.', 'BASE RULES\n', proj, '- note', [f.name], None)
            self.assertTrue(p.startswith('BASE RULES\n\n## Your role: builder\nRole rule.\n'))
            self.assertIn('`python -m unittest`', p)
            self.assertIn('- note\n\n---\n# YOUR TASK\n\nDo X.', p)
            self.assertIn('   1  a = 1\n   2  b = 2\n```', p)
            self.assertTrue(p.endswith("only if the task needs them."))
        finally:
            os.unlink(f.name)

    def test_scribe_and_no_check_and_conversation(self):
        proj = {'check_one': '', 'check_all': '', 'check_dir': ''}
        self.assertIn('Run no commands.', d.build_prompt('T', 'scribe', 's', '', 'R', proj, '', [], None))
        self.assertIn('no check command', d.build_prompt('T', 'builder', 'b', '', 'R', proj, '', [], None))
        self.assertEqual(d.build_prompt('T', 'builder', 'b', '', 'R', proj, '', [], 'conv1'), 'T')

    def test_copilot_tools_have_no_shell(self):
        self.assertEqual(d.copilot_tools('scribe', '')['allow'], ['write(**/*.md)'])
        self.assertEqual(d.copilot_tools('tester', 'tests'), {'tools': 'view,create,edit,grep,glob', 'allow': ['write(tests/**)']})
        self.assertEqual(d.copilot_tools('builder', ''), {'tools': 'view,create,edit,grep,glob', 'allow': ['write']})
        self.assertEqual(d.copilot_tools('reviewer', 'tests'), {'tools': 'view,grep,glob', 'allow': []})

    def test_reviewer_gets_check_result(self):
        proj = {'check_one': 'x {name}', 'check_all': '', 'check_dir': ''}
        check = {'cmd': 'x shop', 'passed': False, 'exit': 1, 'timed_out': False, 'output': 'AssertionError: 3 != 4'}
        p = d.build_prompt('T', 'reviewer', 'r', '', 'R', proj, '', [], None, check)
        self.assertIn('FAIL (exit 1) | `x shop`', p)
        self.assertIn('AssertionError: 3 != 4', p)
        self.assertNotIn('`x shop`', d.build_prompt('T', 'reviewer', 'r', '', 'R', proj, '', [], None, {'skipped': 'why'}))


class Check(unittest.TestCase):
    def test_check_command(self):
        self.assertEqual(d.check_command('test {name} -v', 'shop'), ('test shop -v', ''))
        self.assertEqual(d.check_command('test all', None), ('test all', ''))
        self.assertIsNone(d.check_command('', 'shop')[0])
        cmd, why = d.check_command('test {name}', None)
        self.assertIsNone(cmd)
        self.assertIn('--part', why)

    def test_run_check(self):
        ok = d.run_check('python -c "print(1)"', 60)
        self.assertEqual((ok['passed'], ok['exit'], ok['output']), (True, 0, '1'))
        bad = d.run_check('python -c "import sys; [print(i) for i in range(200)]; sys.exit(3)"', 60)
        self.assertEqual((bad['passed'], bad['exit'], bad['timed_out']), (False, 3, False))
        self.assertEqual(bad['output'].splitlines()[0], str(200 - d.CHECK_TAIL_LINES))
        self.assertIn('FAIL (exit 3)', d.check_text(bad))
        slow = d.run_check('python -c "import time; time.sleep(30)"', 1)
        self.assertEqual((slow['passed'], slow['timed_out']), (False, True))

    def test_fix_prompt(self):
        check = {'cmd': 'c', 'passed': False, 'exit': 1, 'timed_out': False, 'output': 'boom'}
        self.assertIn('boom', d.fix_prompt('builder', check))
        self.assertIn('change nothing', d.fix_prompt('tester', check))

    def run_main(self, fake, extra=()):
        """main() in a temp project whose check passes once ok.txt exists; the worker is faked."""
        import contextlib
        import io
        old = (d.ROOT, d.invoke_agy, os.getcwd())
        with tempfile.TemporaryDirectory() as t:
            Path(t, 'PROJECT.md').write_text(
                '```settings\ncheck_one: python -c "import os,sys; sys.exit(0 if os.path.exists(\'{name}.txt\') else 1)"\n```\n',
                encoding='utf-8')
            d.ROOT, d.invoke_agy = Path(t), fake
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    code = d.main(['--model', 'gemini-3.8-flash-low', '--task', 'x', *extra])
                last = json.loads(Path(t, '.worker', 'last.json').read_text(encoding='utf-8'))
            finally:
                os.chdir(old[2])
                d.ROOT, d.invoke_agy = old[0], old[1]
        return code, last, out.getvalue()

    def test_failed_check_goes_back_to_the_worker_once(self):
        prompts = []

        def fake(w, ctx):
            prompts.append(ctx.task)
            if len(prompts) == 2:   # the fix round
                Path(d.ROOT, 'ok.txt').write_text('x', encoding='utf-8')
            return {'worker': 'agy fake', 'code': 0, 'conversation_id': 'c1', 'response': f'report {len(prompts)}',
                    'calls': '?', 'input_tokens': 10, 'output_tokens': 1, 'denied': []}

        code, last, out = self.run_main(fake, ['--part', 'ok'])
        self.assertEqual(code, 0)
        self.assertEqual(len(prompts), 2)
        self.assertIn('FAIL (exit 1)', prompts[1])
        self.assertEqual((last['check']['passed'], last['check']['fix_rounds'], last['input_tokens'], last['response']),
                         (True, 1, 20, 'report 2'))
        self.assertIn('check: PASS', out)
        self.assertIn('after 1 fix round', out)

    def test_check_still_failing_is_exit_7(self):
        calls = []

        def fake(w, ctx):
            calls.append(1)
            return {'worker': 'agy fake', 'code': 0, 'conversation_id': 'c1', 'response': 'r', 'calls': '?', 'denied': []}

        code, last, out = self.run_main(fake, ['--part', 'ok'])
        self.assertEqual((code, len(calls)), (7, 2))
        self.assertIn('[CHECK FAILED]', out)
        code, last, _ = self.run_main(fake, ['--part', 'ok', '--no-fix'])
        self.assertEqual((code, len(calls)), (7, 3))
        code, last, out = self.run_main(fake)   # no --part: {name} can't be filled, so no check
        self.assertEqual(code, 0)
        self.assertIn('check: not run', out)
        code, last, _ = self.run_main(fake, ['--part', 'ok', '--no-check'])
        self.assertEqual((code, last['check']), (0, None))

    def test_bad_part_refused(self):
        with self.assertRaises(d.Fail) as e:
            d.main(['--task', 'x', '--part', 'a; rm -rf .'])
        self.assertEqual(e.exception.code, 2)


class Results(unittest.TestCase):
    def agy(self, result):
        return d.parse_agy_stream('noise\n' + json.dumps({'event': 'result', 'result': result}, separators=(',', ':')) + '\n', 'm1')

    def test_agy_codes(self):
        ok = self.agy({'response': 'done', 'conversation_id': 'c1', 'usage': {'input_tokens': 10, 'output_tokens': 2}})
        self.assertEqual((ok['code'], ok['conversation_id'], ok['input_tokens'], ok['worker']), (0, 'c1', 10, 'agy m1'))
        self.assertEqual(self.agy({'finished_reason': 'print_timeout'})['code'], 3)
        self.assertEqual(self.agy({'status': 'ERROR', 'error': '429 quota'})['code'], 6)
        self.assertEqual(self.agy({'response': 'x', 'denied_actions': [{'display_name': 'rm -rf'}]})['denied'], ['rm -rf'])
        self.assertEqual(self.agy({'response': ''})['code'], 5)
        bad = d.parse_agy_stream('a\nb\nc\nd\n', 'm1')
        self.assertEqual((bad['code'], bad['error']), (6, 'b c d'))

    def test_copilot_events(self):
        ev = [{'type': 'model.call_start'}, {'type': 'model.call_start'},
              {'type': 'tool.execution_start', 'data': {'toolCallId': 't1', 'toolName': 'shell', 'arguments': {'command': 'git push'}}},
              {'type': 'tool.execution_complete', 'data': {'toolCallId': 't1', 'error': {'code': 'denied'}}},
              {'type': 'assistant.message', 'data': {'content': 'first'}},
              {'type': 'assistant.message', 'data': {'content': 'final report'}},
              {'type': 'result', 'sessionId': 's9'}]
        p = d.parse_copilot_events('not json\n' + '\n'.join(json.dumps(e) for e in ev))
        self.assertEqual(p, {'conversation_id': 's9', 'response': 'final report', 'error': '', 'calls': 2,
                             'denied': ['shell: git push']})

    def test_log_tokens(self):
        with tempfile.TemporaryDirectory() as t:
            Path(t, 'a.log').write_text('"prompt_tokens": 100, "completion_tokens": 5\n"prompt_tokens": 200, '
                                        '"completion_tokens": 7, "cached_tokens": 50', encoding='utf-8')
            r = d.log_tokens(t)
            self.assertEqual((r['in'], r['out'], r['cached'], r['last'], r['total']), (300, 12, 50, 207, 312))
        self.assertEqual(d.log_tokens(Path(t) / 'gone')['total'], 0)

    def test_openai_guard(self):
        self.assertEqual(d.openai_tier('o3'), 'big')
        self.assertEqual(d.openai_tier('gpt-5.4-mini'), 'mini')
        self.assertIsNone(d.openai_tier('gpt-5.5'))
        with tempfile.TemporaryDirectory() as t:
            log = Path(t, 'u.jsonl')
            log.write_text('﻿' + json.dumps({'day': d.utc_day(), 'tier': 'big', 'tokens': 100}) + '\n'
                           + json.dumps({'day': '2000-01-01', 'tier': 'big', 'tokens': 999}) + '\nbroken\n'
                           + json.dumps({'day': d.utc_day(), 'tier': 'mini', 'tokens': 5}) + '\n', encoding='utf-8')
            self.assertEqual(d.openai_used_today(log, 'big'), 100)
            self.assertEqual(d.openai_used_today(Path(t, 'none'), 'big'), 0)

    def test_paid_openai_model_refused_without_running(self):
        ctx = d.Ctx()
        ctx.args = None
        ctx.openai_log = Path(tempfile.gettempdir()) / 'never-written.jsonl'
        r = d.invoke_copilot({'h': 'cp', 'p': 'openai', 'pm': 'gpt-5.5'}, ctx)
        self.assertEqual(r['code'], 6)
        self.assertIn('REFUSED', r['error'])


class Processes(unittest.TestCase):
    def test_run_tool_output_and_stdin(self):
        out, why = d.run_tool([sys.executable, '-c', 'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read().decode().upper().encode()); '
                               'sys.stdout.flush(); print("err", file=sys.stderr)'],
                              stdin_bytes='héllo'.encode('utf-8'))
        self.assertIsNone(why)
        self.assertIn('HÉLLO', out)
        self.assertIn('err', out)

    def test_timeout_kills_child_processes_too(self):
        with tempfile.TemporaryDirectory() as t:
            marker = Path(t, 'late.txt')
            # The parent starts a child that would write a file after 8 s; both must be killed at the timeout.
            child = f'import time; time.sleep(8); open({str(marker)!r}, "w").write("x")'
            parent = f'import subprocess, sys, time; subprocess.Popen([sys.executable, "-c", {child!r}]); time.sleep(30)'
            start = time.monotonic()
            _, why = d.run_tool([sys.executable, '-c', parent], timeout=2)
            self.assertEqual(why, 'timeout')
            self.assertLess(time.monotonic() - start, 15)
            time.sleep(9)
            self.assertFalse(marker.exists(), 'child process survived the kill')

    def test_watch_stops_early(self):
        _, why = d.run_tool([sys.executable, '-c', 'import time; time.sleep(30)'], watch=lambda: True)
        self.assertEqual(why, 'stopped')

    def test_missing_tool(self):
        with self.assertRaises(d.Fail) as e:
            d.run_tool(['no-such-tool-xyz'])
        self.assertEqual(e.exception.code, 3)

    def test_cli_errors(self):
        import subprocess
        script = str(d.AGENT_DIR / 'delegate.py')
        r = subprocess.run([sys.executable, script, '--role', 'nope'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("No role 'nope'", r.stderr)
        r = subprocess.run([sys.executable, script, '--model', 'gemini-3.8-flash-low'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn('--task', r.stderr)


class CheckSetup(unittest.TestCase):
    def test_keeps_confirmed_answers(self):
        new = check_setup.build_md([('git', 'found', 'n')], {'agy': True, 'nvidia': False}, '', '2026-01-01 00:00')
        self.assertIn('```available\nagy: yes\nnvidia: no\n```', new)
        self.assertIn('Ollama models: none', new)
        old = new.replace('<which plan>', 'Max')
        merged = check_setup.keep_confirmed(new, old)
        self.assertIn('- Claude plan: Max', merged)
        self.assertEqual(merged.count('## Confirmed by the user'), 1)
        self.assertEqual(check_setup.keep_confirmed(new, ''), new)


if __name__ == '__main__':
    unittest.main()
