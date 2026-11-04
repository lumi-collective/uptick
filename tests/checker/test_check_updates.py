#!/usr/bin/env python3
"""Unit tests of the checker's pure parts. No network, no update commands: every tool call is faked.

Run: python3 -I tests/checker/test_check_updates.py
"""
import base64
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', '..')
sys.path.insert(0, os.path.join(ROOT, 'plugin', 'checker'))
sys.dont_write_bytecode = True  # no __pycache__ beside the checker
import check_updates as cu  # noqa: E402

DATA = os.path.join(HERE, 'testdata')
OUTDATED = {
    'formulae': [
        {'name': 'gh', 'installed_versions': ['2.81.0'], 'current_version': '2.82.1', 'pinned': False},
        {'name': 'jq', 'installed_versions': ['1.7'], 'current_version': '1.8', 'pinned': False},
        {'name': 'widget', 'installed_versions': ['1.0'], 'current_version': '1.1', 'pinned': False},
        {'name': 'git-delta', 'installed_versions': ['0.17'], 'current_version': '0.18', 'pinned': True},
    ],
    'casks': [{'name': 'example-app', 'installed_versions': ['3'], 'current_version': '4'}],
}


def fake_brew(cmd, timeout=120):
    if cmd[:2] == ['brew', 'outdated']:
        return json.dumps(OUTDATED)
    raise AssertionError(f'unexpected command {cmd}')


class Brewfile(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(cu.parse_brewfile(os.path.join(DATA, 'Brewfile')),
                         {'gh', 'git-delta', 'example-app', 'example/tools/widget', 'widget'})

    def brew_items(self, brewfile):
        rep = cu.Report({})
        with mock.patch.object(cu, 'run', fake_brew), mock.patch.object(cu.shutil, 'which', lambda t: '/bin/' + t):
            cu.check_brew(rep, False, brewfile)
        return rep

    def test_filter_by_brewfile(self):
        rep = self.brew_items(os.path.join(DATA, 'Brewfile'))
        # jq is not in the Brewfile; git-delta is pinned; widget matches its tap-qualified entry.
        self.assertEqual([i['id'] for i in rep.items], ['gh', 'widget', 'example-app'])
        self.assertEqual(rep.items[2].get('cask'), True)
        self.assertEqual(rep.checked, ['Homebrew'])

    def test_no_brewfile_shows_all_outdated(self):
        self.assertEqual([i['id'] for i in self.brew_items(None).items], ['gh', 'jq', 'widget', 'example-app'])

    def test_unreadable_brewfile_is_a_problem(self):
        rep = self.brew_items(os.path.join(DATA, 'no-such-Brewfile'))
        self.assertEqual(rep.items, [])
        self.assertIn('Brewfile not readable', rep.problems[0]['text'])

    def test_missing_brew_is_skipped(self):
        rep = cu.Report({})
        with mock.patch.object(cu.shutil, 'which', lambda t: None):
            cu.check_brew(rep, False, None)
        self.assertEqual((rep.items, rep.problems, rep.skipped), ([], [], ['Homebrew not found, skipped']))


class UvTools(unittest.TestCase):
    # As uv 0.13.7 prints it with --show-version-specifiers: a tool line, then one `- ` line per executable.
    OUTDATED = ('example-tool v0.9.102 [latest: 0.9.110]\n- example-tool\n- example-tool-mcp\n'
                'black v24.2.0 [required: >=24] [latest: 24.3.0]\n- black\n- blackd')
    # Real lines (uv 0.13.7, a throwaway tool folder) of installs uv tool upgrade cannot move to `latest`.
    HELD = ('httpie v1.0.0 [required: file:///nonexistent/httpie] [latest: 3.2.4]\n- http\n'
            'ruff v0.1.0 [required:  git+https://github.com/astral-sh/ruff] [latest: 0.17.3]\n- ruff\n'
            'cowsay v5.0 [required: ==5.0] [latest: 6.1]\n- cowsay')
    # Real (uv 0.13.7, `uv tool install 'cowsay<6'` in a throwaway tool folder): uv tool upgrade stays in the range.
    RANGED = 'cowsay v5.0 [required: <6] [latest: 6.1]\n- cowsay'
    NO_FLAG = "error: unexpected argument '--outdated' found\n\nUsage: uv tool list [OPTIONS]\n"
    OFFLINE = ('error: Failed to fetch: https://pypi.org/simple/cowsay/\n'
               '  cause: error sending request for url (https://pypi.org/simple/cowsay/)\n'
               '  cause: Connection refused (os error 61)\n')

    def uv_items(self, out, which=lambda t: '/bin/' + t, stderr='', known_broken=None):
        rep, ran = cu.Report(known_broken or {}), []

        def fake(cmd, timeout=120, err=None):
            ran.append(cmd)
            if err is not None:
                err.append(stderr)
            return out
        with mock.patch.object(cu, 'run', fake), mock.patch.object(cu.shutil, 'which', which):
            cu.check_uv(rep)
        return rep, ran

    def test_outdated_tools_are_items(self):
        rep, ran = self.uv_items(self.OUTDATED)
        self.assertEqual(ran, [['uv', 'tool', 'list', '--outdated', '--show-version-specifiers', '--color', 'never']])
        self.assertEqual(rep.items, [
            {'id': 'example-tool', 'kind': 'uv', 'name': 'example-tool', 'have': '0.9.102', 'latest': '0.9.110'},
            {'id': 'black', 'kind': 'uv', 'name': 'black', 'have': '24.2.0', 'latest': '24.3.0'}])
        self.assertEqual((rep.checked, rep.problems), (['uv tools'], []))

    def test_installs_upgrade_cannot_move_are_not_compared(self):
        rep, _ = self.uv_items(self.HELD)
        self.assertEqual([(i['id'], i['kind'], i['have'], i['latest'], i['note']) for i in rep.items], [
            ('httpie', 'info', '1.0.0', '?', 'not compared: installed from file:///nonexistent/httpie'),
            ('ruff', 'info', '0.1.0', '?', 'not compared: installed from git+https://github.com/astral-sh/ruff'),
            ('cowsay', 'info', '5.0', '6.1', 'not compared: installed as ==5.0, which uv tool upgrade keeps')])
        self.assertEqual(rep.problems, [])
        # Not updates: alone they exit 0, so the daily check does not notify.
        result = {'checkedAt': '2026-01-01T09:00:00+00:00', 'checked': [], 'skipped': [], 'problems': [], 'items': rep.items}
        with mock.patch.object(cu, 'check', lambda *a: result), mock.patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(cu.main([]), 0)

    def test_a_range_is_offered_and_only_an_exact_pin_is_held(self):
        self.assertEqual(self.uv_items(self.RANGED)[0].items,
                         [{'id': 'cowsay', 'kind': 'uv', 'name': 'cowsay', 'have': '5.0', 'latest': '6.1'}])
        lines = '\n'.join(f'tool{n} v1.0 [required: {spec}] [latest: 2.0]' for n, spec in enumerate(
            ['<=1.5', '!=2.0', '~=1.0', '>=1,<2', '==1.*', '==1.0', '===1.0']))
        self.assertEqual([i['kind'] for i in self.uv_items(lines)[0].items], ['uv'] * 5 + ['info'] * 2)

    def test_nothing_outdated_is_no_items(self):
        rep, _ = self.uv_items('')
        self.assertEqual((rep.items, rep.problems, rep.checked), ([], [], ['uv tools']))

    def test_lines_of_another_shape_are_skipped(self):
        rep, _ = self.uv_items('example-tool v1.0 [latest: 2.0]\nwarning: something\nx v1\n\x1b[1my v1 [latest: 2]\x1b[0m')
        self.assertEqual([(i['id'], i['have'], i['latest']) for i in rep.items], [('example-tool', '1.0', '2.0')])

    def test_missing_uv_is_skipped(self):
        rep, ran = self.uv_items(self.OUTDATED, which=lambda t: None)
        self.assertEqual((rep.items, rep.problems, ran), ([], [], []))
        self.assertEqual(rep.skipped, ['uv not found, skipped'])

    def test_a_uv_without_outdated_is_skipped_with_how_to_turn_it_off(self):
        rep, _ = self.uv_items(None, stderr=self.NO_FLAG)
        self.assertEqual((rep.items, rep.problems), ([], []))
        self.assertEqual(rep.skipped, ['uv < 0.10.10: update it, or drop uv from kinds'])
        self.assertLessEqual(len(rep.skipped[0]), 56)  # whole in the 64-column pane, after its indent

    def test_a_network_failure_is_an_offline_problem(self):
        rep, _ = self.uv_items(None, stderr=self.OFFLINE)
        self.assertEqual(rep.problems, [{'text': 'uv tools not checked: Failed to fetch: https://pypi.org/simple/cowsay/',
                                         'offline': True}])

    def test_another_failure_is_a_problem(self):
        self.assertEqual(self.uv_items(None, stderr='error: Failed to parse `uv.toml`\n')[0].problems,
                         [{'text': 'uv tools not checked: Failed to parse `uv.toml`'}])
        self.assertEqual(self.uv_items(None)[0].problems, [{'text': 'uv tools not checked: uv tool list failed'}])

    def test_known_broken_holds_back_a_uv_version(self):
        rep, _ = self.uv_items(self.OUTDATED, known_broken={'black': {'match': '24.3', 'kind': 'uv', 'note': 'breaks'}})
        self.assertEqual([i['kind'] for i in rep.items], ['uv', 'broken'])

    def test_run_hands_back_stderr(self):
        failed = subprocess.CompletedProcess([], 2, stdout='', stderr='error: boom\n')
        err = []
        with mock.patch.object(cu.subprocess, 'run', return_value=failed):
            self.assertIsNone(cu.run(['uv'], err=err))
        self.assertEqual(err, ['error: boom\n'])

    def test_text_names_the_upgrade_command(self):
        result = {'checkedAt': '2026-01-01T09:00:00+00:00', 'checked': [], 'skipped': [], 'problems': [],
                  'items': [{'id': 'example-tool', 'kind': 'uv', 'name': 'example-tool', 'have': '1.0', 'latest': '1.1'}]}
        self.assertIn('  ↑ example-tool: 1.0 → 1.1   [uv tool upgrade example-tool]', cu.text(result))


class KnownBroken(unittest.TestCase):
    def setUp(self):
        self.rep = cu.Report(cu.load_known_broken(os.path.join(DATA, 'known-broken.json')))

    def test_listed_upstream_becomes_a_note(self):
        self.rep.offer({'id': 'example-skill', 'kind': 'skill', 'name': 'example-skill', 'have': 'a1b2c3d', 'latest': '0000000'},
                       '0000000abcdef')
        item = self.rep.items[0]
        self.assertEqual(item['kind'], 'broken')
        self.assertEqual(item['note'], 'upstream known broken, keeping installed')
        self.assertEqual(item['detail'], 'since 2026-01-01: SKILL.md has invalid YAML front matter')

    def test_other_upstream_is_an_update(self):
        self.rep.offer({'id': 'example-skill', 'kind': 'skill', 'name': 'example-skill', 'have': 'a', 'latest': 'b'}, '0123abc')
        self.rep.offer({'id': 'lint-helper@community', 'kind': 'plugin', 'name': 'x', 'have': '1.0', 'latest': '2.1'}, '2.1')
        self.assertEqual([i['kind'] for i in self.rep.items], ['skill', 'plugin'])

    def test_plugin_version_match(self):
        self.rep.offer({'id': 'lint-helper@community', 'kind': 'plugin', 'name': 'x', 'have': '1.0', 'latest': '2.0.0'}, '2.0.0')
        self.assertEqual(self.rep.items[0]['detail'], 'crashes on start')

    def test_version_match_is_exact_or_a_prefix_before_a_non_digit(self):
        brew = {'id': 'example-tool', 'kind': 'brew', 'name': 'example-tool', 'have': '1.0'}
        for upstream, held in (('1.1', True), ('1.1.3', True), ('1.1-rc.1', True), ('1.10.0', False), ('1.12', False)):
            rep = cu.Report(self.rep.known_broken)
            rep.offer({**brew, 'latest': upstream}, upstream)
            self.assertEqual(rep.items[0]['kind'] == 'broken', held, upstream)

    def test_kind_narrows_a_match(self):
        # A skill named like the held-back formula is not held back.
        self.rep.offer({'id': 'example-tool', 'kind': 'skill', 'name': 'example-tool', 'have': 'a', 'latest': 'b'}, '1.1')
        self.assertEqual(self.rep.items[0]['kind'], 'skill')

    def test_missing_default_file_is_empty(self):
        with mock.patch.dict(os.environ, {'XDG_CONFIG_HOME': os.path.join(DATA, 'no-such-dir')}):
            self.assertEqual(cu.load_known_broken(None), {})

    def test_missing_named_file_is_a_problem(self):
        with mock.patch.multiple(cu, check_claude=lambda r: None, check_plugins=lambda r, f: None,
                                 check_skills=lambda r: None, check_brew=lambda r, f, b: None, check_uv=lambda r: None):
            result = cu.check(known_broken=os.path.join(DATA, 'missing.json'))
        self.assertIn('known-broken list not readable', result['problems'][0]['text'])


class Extra(unittest.TestCase):
    def merged(self, command):
        rep = cu.Report({})
        cu.merge_extra(rep, command)
        return rep

    def script(self, code):
        """A command line that runs code with this Python. Double quotes split alike under shlex and on Windows."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = os.path.join(folder.name, 'extra.py')
        with open(path, 'w') as f:
            f.write(code)
        return f'"{sys.executable}" -I "{path}"'

    def printing(self, out):
        return self.script(f'print({json.dumps(json.dumps(out))})\n')

    def test_merges_items_problems_and_checked(self):
        out = {'items': [{'id': 'example-app', 'kind': 'self', 'name': 'Example App'}, {'kind': 'self'}],
               'problems': ['plain text', {'text': 'x', 'hint': 'y'}], 'checked': ['Example App 1.0']}
        rep = self.merged(self.printing(out))
        self.assertEqual([i['id'] for i in rep.items], ['example-app'])  # an item without an id is dropped
        self.assertEqual(rep.problems, [{'text': 'plain text'}, {'text': 'x', 'hint': 'y'}])
        self.assertEqual(rep.checked, ['Example App 1.0'])

    def test_known_broken_holds_back_extra_items(self):
        out = {'items': [{'id': 'lint-helper@community', 'kind': 'plugin', 'name': 'x', 'have': '1.0', 'latest': '2.0.0'}]}
        rep = cu.Report(cu.load_known_broken(os.path.join(DATA, 'known-broken.json')))
        cu.merge_extra(rep, self.printing(out))
        self.assertEqual((rep.items[0]['kind'], rep.items[0]['detail']), ('broken', 'crashes on start'))

    def test_failure_is_one_problem(self):
        rep = self.merged(self.script('import sys\nsys.stderr.write("boom\\n")\nraise SystemExit(3)\n'))
        self.assertEqual(rep.problems[0]['text'], 'extra check exited 3: boom')
        self.assertEqual(rep.items, [])

    def test_missing_command_is_one_problem(self):
        self.assertIn('could not run', self.merged('no-such-command-xyz --json').problems[0]['text'])

    def test_windows_gets_the_command_line_whole(self):
        command = r'"C:\Program Files\checks\extra.exe" --json'
        done = subprocess.CompletedProcess(command, 0, stdout='{"checked": ["extra"]}', stderr='')
        with mock.patch.object(cu.os, 'name', 'nt'), mock.patch.object(cu.subprocess, 'run', return_value=done) as ran:
            self.assertEqual(self.merged(command).checked, ['extra'])
        self.assertEqual(ran.call_args.args[0], command)


class Run(unittest.TestCase):
    def test_runs_the_command_where_which_finds_it(self):
        # Windows' npm is npm.cmd: subprocess starts it only by the path which() returns.
        done = subprocess.CompletedProcess([], 0, stdout=' 2.1.310\n', stderr='')
        with mock.patch.object(cu.shutil, 'which', lambda t: r'C:\nodejs\npm.cmd'), \
                mock.patch.object(cu.subprocess, 'run', return_value=done) as ran:
            self.assertEqual(cu.run(['npm', 'view', '@anthropic-ai/claude-code', 'version']), '2.1.310')
        self.assertEqual(ran.call_args.args[0], [r'C:\nodejs\npm.cmd', 'view', '@anthropic-ai/claude-code', 'version'])


def at_home(home):
    """Runs a check as if HOME were a test folder; every tool exists, every command is faked."""
    return mock.patch.multiple(cu, HOME=os.path.join(DATA, home)), mock.patch.object(cu.shutil, 'which', lambda t: '/bin/' + t)


class Skills(unittest.TestCase):
    def test_pinned_ref_is_queried_at_that_ref(self):
        asked = []

        def fake(cmd, timeout=120):
            asked.append(cmd[2])
            pinned = cmd[2].endswith('?ref=abc1234')
            return json.dumps({'pinned-skill': '1' * 40 if pinned else '8' * 40, 'free-skill': '9' * 40 if pinned else '3' * 40})

        rep = cu.Report({})
        home, which = at_home('home')
        with home, which, mock.patch.object(cu, 'run', fake):
            cu.check_skills(rep)
        self.assertIn('repos/example/skills/contents/skills?ref=abc1234', asked)
        self.assertIn('repos/example/skills/contents/skills', asked)
        # The pinned skill matches its ref: no phantom update; the unpinned one follows the default branch.
        self.assertEqual([(i['id'], i['have'], i['latest']) for i in rep.items], [('free-skill', '2222222', '3333333')])
        self.assertEqual(rep.checked, ['2 skills'])

    def test_malformed_lock_is_one_problem(self):
        rep = cu.Report({})
        home, which = at_home('home-malformed')
        with home, which:
            cu.check_skills(rep)
        self.assertEqual((rep.items, rep.problems[0]['text']), ([], 'skills not checked: ~/.agents/.skill-lock.json not readable'))


class Robustness(unittest.TestCase):
    def test_non_dict_plugin_entry_is_skipped(self):
        listed = ['oops', 42, {'id': 'lint-helper@community', 'version': '1.4.0', 'enabled': True}]
        rep = cu.Report({})
        home, which = at_home('home')
        with home, which, mock.patch.object(cu, 'run', lambda cmd, timeout=120: json.dumps(listed)):
            cu.check_plugins(rep, False)
        self.assertEqual([(i['id'], i['latest']) for i in rep.items], [('lint-helper@community', '1.5.0')])

    def test_github_sourced_plugin_reads_its_repo_plugin_json(self):
        listed = [{'id': 'repo-helper@community', 'version': '2.3.1', 'enabled': True},
                  {'id': 'sub-helper@community', 'version': '2.4.0', 'enabled': True}]
        asked = []

        def fake(cmd, timeout=120):
            if cmd[:2] == ['gh', 'api']:
                asked.append(cmd[2])
                return base64.encodebytes(json.dumps({'version': '2.4.0'}).encode()).decode()  # gh wraps it in lines
            return json.dumps(listed)

        rep = cu.Report({})
        home, which = at_home('home')
        with home, which, mock.patch.object(cu, 'run', fake):
            cu.check_plugins(rep, False)
        self.assertEqual(asked, ['repos/example/repo-helper/contents/.claude-plugin/plugin.json',
                                 'repos/example/mods/contents/plugins/sub-helper/.claude-plugin/plugin.json'])
        self.assertEqual([(i['id'], i['have'], i['latest']) for i in rep.items], [('repo-helper@community', '2.3.1', '2.4.0')])

    def test_directory_marketplace_is_found_through_known_marketplaces(self):
        listed = [{'id': 'dir-helper@local', 'version': '0.1.0', 'enabled': True}]
        with tempfile.TemporaryDirectory() as home:
            market = os.path.join(home, 'elsewhere', 'market')
            os.makedirs(os.path.join(market, '.claude-plugin'))
            with open(os.path.join(market, '.claude-plugin', 'marketplace.json'), 'w') as f:
                json.dump({'name': 'local', 'plugins': [{'name': 'dir-helper', 'version': '0.2.0'}]}, f)
            os.makedirs(os.path.join(home, '.claude', 'plugins'))
            with open(os.path.join(home, '.claude', 'plugins', 'known_marketplaces.json'), 'w') as f:
                json.dump({'local': {'source': {'source': 'directory', 'path': market}, 'installLocation': market}}, f)
            rep = cu.Report({})
            with mock.patch.object(cu, 'HOME', home), mock.patch.object(cu.shutil, 'which', lambda t: '/bin/' + t), \
                    mock.patch.object(cu, 'run', lambda cmd, timeout=120: json.dumps(listed)):
                cu.check_plugins(rep, False)
        self.assertEqual([(i['id'], i['latest']) for i in rep.items], [('dir-helper@local', '0.2.0')])

    def test_uncomparable_plugin_is_a_not_compared_info_row(self):
        listed = [{'id': 'lint-helper@community', 'version': 'dbd4588f9e10', 'enabled': True},
                  {'id': 'unlisted@community', 'version': '1.0.0', 'enabled': True}]
        rep = cu.Report({})
        home, which = at_home('home')
        with home, which, mock.patch.object(cu, 'run', lambda cmd, timeout=120: json.dumps(listed)):
            cu.check_plugins(rep, False)
        self.assertEqual([(i['id'], i['kind'], i['have'], i['latest'], i['note']) for i in rep.items],
                         [('lint-helper@community', 'info', 'dbd4588f9e10', '1.5.0', 'not compared'),
                          ('unlisted@community', 'info', '1.0.0', '?', 'not compared')])
        # Not an update: alone it exits 0, and its text row is not an ↑ row.
        result = {'checkedAt': '2026-10-22T09:00:00+02:00', 'checked': [], 'skipped': [], 'problems': [], 'items': rep.items}
        with mock.patch.object(cu, 'check', lambda *a: result), mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(cu.main([]), 0)
        self.assertIn('  · lint-helper@community: dbd4588f9e10 (not compared)', out.getvalue())
        self.assertNotIn('↑', out.getvalue())
        other = {**result, 'items': [*rep.items, {'id': 'ops', 'kind': 'info', 'name': 'ops', 'have': '1', 'latest': '2', 'note': 'run x'}]}
        with mock.patch.object(cu, 'check', lambda *a: other), mock.patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(cu.main([]), 1)  # any other info item is a manual step: it counts

    def test_bad_brew_output_is_skipped(self):
        bad = {'formulae': 'not a list', 'casks': [42, None, {'name': 'example-app', 'installed_versions': ['3'], 'current_version': '4'}]}
        rep = cu.Report({})
        with mock.patch.object(cu, 'run', lambda cmd, timeout=120: json.dumps(bad)), \
                mock.patch.object(cu.shutil, 'which', lambda t: '/bin/' + t):
            cu.check_brew(rep, False, None)
        self.assertEqual([i['id'] for i in rep.items], ['example-app'])
        self.assertEqual(rep.problems, [])

    def test_a_crashing_check_costs_only_itself(self):
        def boom(rep):
            raise RuntimeError('boom')

        def brew(rep, fetch, brewfile):
            rep.items.append({'id': 'gh', 'kind': 'brew', 'name': 'gh', 'have': '1', 'latest': '2'})

        with mock.patch.multiple(cu, check_claude=boom, check_plugins=lambda r, f: None, check_skills=boom, check_brew=brew,
                                 check_uv=lambda r: None):
            result = cu.check()
        self.assertEqual([p['text'] for p in result['problems']],
                         ['Claude Code not checked: RuntimeError: boom', 'skills not checked: RuntimeError: boom'])
        self.assertEqual([i['id'] for i in result['items']], ['gh'])
        json.dumps(result)  # still emits valid JSON


class Newer(unittest.TestCase):
    def test_versions(self):
        self.assertTrue(cu.newer('2.1.310', '2.1.306'))
        self.assertFalse(cu.newer('2.1.306', '2.1.310'))
        self.assertFalse(cu.newer('1.0', '1.0'))
        self.assertFalse(cu.newer('1.2', '1.2.0'))
        self.assertFalse(cu.newer('1.2.0', '1.2'))
        self.assertTrue(cu.newer('v1.10.0', '1.9'))

    def test_pre_releases(self):
        self.assertFalse(cu.newer('1.0.0-beta', '1.0.0'))
        self.assertFalse(cu.newer('1.3.0-beta', '1.2.0'))  # never a pre-release over a stable version
        self.assertTrue(cu.newer('1.0.0', '1.0.0-rc.1'))
        self.assertTrue(cu.newer('1.0.0-rc.2', '1.0.0-rc.1'))
        self.assertTrue(cu.newer('1.0.0-rc.10', '1.0.0-rc.9'))
        self.assertTrue(cu.newer('1.0.0-beta', '1.0.0-alpha'))
        self.assertTrue(cu.newer('1.1.0-beta', '1.0.0-beta'))

    def test_unparseable_is_none(self):
        self.assertIsNone(cu.newer('dbd4588f9e10', '1.0.0'))
        self.assertIsNone(cu.newer('2.0', 'latest'))
        self.assertIsNone(cu.newer(None, '1.0'))

    def test_a_plugin_ahead_of_its_marketplace_is_not_offered(self):
        listed = [{'id': 'lint-helper@community', 'version': '1.6.0', 'enabled': True}]
        rep = cu.Report({})
        home, which = at_home('home')
        with home, which, mock.patch.object(cu, 'run', lambda cmd, timeout=120: json.dumps(listed)):
            cu.check_plugins(rep, False)
        self.assertEqual(rep.items, [])  # the marketplace's 1.5.0 is older

    def test_claude_code_version_not_readable_as_a_version_is_info(self):
        answers = {'--version': 'nightly-abc (Claude Code)', 'view': '2.1.310'}
        rep = cu.Report({})
        with mock.patch.object(cu.shutil, 'which', lambda t: '/bin/' + t), \
                mock.patch.object(cu, 'run', lambda cmd, timeout=120: answers[cmd[1]]):
            cu.check_claude(rep)
        self.assertEqual([(i['kind'], i['note']) for i in rep.items], [('info', 'not compared')])


class Kinds(unittest.TestCase):
    def test_excluded_kinds_are_not_checked(self):
        ran = []
        with mock.patch.multiple(cu, check_claude=lambda r: ran.append('claude'), check_plugins=lambda r, f: ran.append('plugin'),
                                 check_skills=lambda r: ran.append('skill'), check_brew=lambda r, f, b: ran.append('brew'),
                                 check_uv=lambda r: ran.append('uv')):
            cu.check(kinds={'skill'})
            self.assertEqual(ran, ['claude', 'skill'])
            ran.clear()
            cu.check(kinds={'uv'})
            self.assertEqual(ran, ['claude', 'uv'])
            ran.clear()
            cu.check()
            self.assertEqual(ran, ['claude', 'plugin', 'skill', 'brew', 'uv'])

    def test_kinds_flag(self):
        seen = []
        result = {'checkedAt': '2026-01-01T09:00:00+00:00', 'checked': [], 'skipped': [], 'problems': [], 'items': []}

        def fake(*args):
            seen.append(args[4])
            return result

        with mock.patch.object(cu, 'check', fake), mock.patch('sys.stdout', new_callable=io.StringIO):
            cu.main(['--kinds=plugin, brew'])
            cu.main(['--kinds='])
            cu.main([])
            cu.main(['--kinds=uv'])
        # uv is opt-in: without --kinds it is not checked, so no tool names go to the index unasked.
        self.assertEqual(seen, [{'plugin', 'brew'}, set(), {'plugin', 'skill', 'brew'}, {'uv'}])

    def test_unknown_kind_is_reported(self):
        seen = []

        def fake(*args):
            seen.append(args[4])
            return {'checkedAt': '2026-01-01T09:00:00+00:00', 'checked': [], 'skipped': [], 'problems': [], 'items': []}

        out = io.StringIO()
        with mock.patch.object(cu, 'check', fake), mock.patch('sys.stdout', out):
            code = cu.main(['--json', '--kinds=plugin, skill;,,x'])
        self.assertEqual(seen, [{'plugin'}])
        problems = json.loads(out.getvalue())['problems']
        self.assertEqual(len(problems), 1)
        # The exact text also proves the empty entry between the two commas is not reported.
        self.assertEqual(problems[0]['text'], 'kinds: ignored unknown "skill;", "x" (valid: plugin, skill, brew, uv)')
        self.assertEqual(problems[0]['hint'], 'fix kinds in /config, UPTICK_KINDS or --kinds')
        self.assertEqual(code, 2)


class TimeBudget(unittest.TestCase):
    def tearDown(self):
        cu.DEADLINE = None

    def test_budget_is_well_under_the_mods_ten_minutes(self):
        self.assertLessEqual(cu.TOTAL_SECONDS, 480)
        # The fetch steps leave the checks time of their own.
        self.assertLessEqual(cu.FETCH_MARKETPLACES_SECONDS + cu.FETCH_BREW_SECONDS, cu.TOTAL_SECONDS - 120)

    def test_run_waits_no_longer_than_the_budget(self):
        done = subprocess.CompletedProcess([], 0, stdout='x', stderr='')
        cu.DEADLINE = cu.time.monotonic() + 5
        with mock.patch.object(cu.subprocess, 'run', return_value=done) as ran:
            cu.run(['true'], timeout=120)
        self.assertLessEqual(ran.call_args.kwargs['timeout'], 5)

    def test_out_of_time_runs_nothing(self):
        cu.DEADLINE = cu.time.monotonic() - 1
        with mock.patch.object(cu.subprocess, 'run') as ran:
            self.assertIsNone(cu.run(['true']))
        ran.assert_not_called()
        rep = cu.Report({})
        cu.merge_extra(rep, 'anything --json')
        self.assertIn('out of time', rep.problems[0]['text'])

    def test_out_of_time_still_writes_the_cache(self):
        clock = iter(range(0, 100_000, 500))  # every look at the clock is 500 s later
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(cu.time, 'monotonic', lambda: next(clock)), \
                mock.patch.object(cu.shutil, 'which', lambda t: '/bin/' + t), mock.patch.object(cu, 'HOME', tmp), \
                mock.patch.object(cu.subprocess, 'run') as ran, mock.patch('sys.stdout', new_callable=io.StringIO):
            path = os.path.join(tmp, 'last.json')
            cu.main(['--fetch', f'--cache={path}'])
            with open(path) as f:
                problems = json.load(f)['problems']
        ran.assert_not_called()
        # One problem names the cause; nothing reads as offline or as a missing login.
        self.assertIn('check stopped after 7 minutes: some results are missing', [p['text'] for p in problems])
        self.assertFalse(any(p.get('offline') for p in problems))
        self.assertFalse(any('gh auth' in p.get('hint', '') or 'npm registry' in p['text'] for p in problems))


class SelfItems(unittest.TestCase):
    def test_self_items_alone_exit_0(self):
        result = {'checkedAt': '2026-01-01T09:00:00+00:00', 'checked': [], 'skipped': [], 'problems': [],
                  'items': [{'id': 'claude-code', 'kind': 'self', 'name': 'Claude Code', 'have': '2.1.0', 'latest': '2.2.0'}]}
        with mock.patch.object(cu, 'check', lambda *a: result), mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(cu.main([]), 0)
        self.assertIn('  · Claude Code: 2.1.0 → 2.2.0 (updates itself; new sessions get it)', out.getvalue())

    def test_a_note_replaces_the_default_text(self):
        result = {'checkedAt': '2026-01-01T09:00:00+00:00', 'checked': [], 'skipped': [], 'problems': [],
                  'items': [{'id': 'claude-code', 'kind': 'self', 'name': 'Claude Code', 'have': '2.1.0', 'latest': '2.2.0',
                             'note': 'auto-update is off: run claude update'}]}
        self.assertIn('  · Claude Code: 2.1.0 → 2.2.0 (auto-update is off: run claude update)', cu.text(result))


class SelfUpdateOff(unittest.TestCase):
    """Claude Code skips its own updates when a flag is set, in its environment or in settings.json's env."""
    FLAGS = ('DISABLE_UPDATES', 'DISABLE_AUTOUPDATER', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC')
    OFF = 'auto-update is off: run claude update'
    BLOCKED = 'updates are turned off (DISABLE_UPDATES)'

    def note(self, settings=None, env=None):
        answers = {'claude': '2.1.0 (Claude Code)', 'npm': '2.2.0'}
        clean = {k: v for k, v in os.environ.items() if k not in self.FLAGS}
        with tempfile.TemporaryDirectory() as home:
            if settings is not None:
                os.makedirs(os.path.join(home, '.claude'))
                with open(os.path.join(home, '.claude', 'settings.json'), 'w') as f:
                    f.write(settings if isinstance(settings, str) else json.dumps(settings))
            rep = cu.Report({})
            with mock.patch.object(cu, 'HOME', home), mock.patch.object(cu.shutil, 'which', lambda t: '/bin/' + t), \
                    mock.patch.object(cu, 'run', lambda cmd, timeout=120: answers[cmd[0]]), \
                    mock.patch.dict(os.environ, {**clean, **(env or {})}, clear=True):
                cu.check_claude(rep)
        self.assertEqual([i['kind'] for i in rep.items], ['self'])
        return rep.items[0].get('note')

    def test_nothing_set_keeps_the_default(self):
        self.assertIsNone(self.note())
        self.assertIsNone(self.note({'env': {'OTHER': '1'}}))

    def test_settings_env_turns_it_off(self):
        self.assertEqual(self.note({'env': {'DISABLE_AUTOUPDATER': '1'}}), self.OFF)
        self.assertEqual(self.note({'env': {'DISABLE_AUTOUPDATER': True}}), self.OFF)

    def test_process_env_turns_it_off(self):
        for value in ('1', 'true', 'YES', ' on '):
            self.assertEqual(self.note(env={'DISABLE_AUTOUPDATER': value}), self.OFF, value)

    def test_other_values_leave_it_on(self):
        for value in ('0', 'false', 'off', ''):
            self.assertIsNone(self.note(env={'DISABLE_AUTOUPDATER': value}), value)

    def test_settings_env_wins_over_the_process_env(self):
        self.assertIsNone(self.note({'env': {'DISABLE_AUTOUPDATER': '0'}}, {'DISABLE_AUTOUPDATER': '1'}))

    def test_nonessential_traffic_off_names_no_command(self):
        # Claude Code takes any non-empty value of this one, and then claude update cannot look up a version either.
        quiet = 'auto-update is off (CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC)'
        self.assertEqual(self.note(env={'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '0'}), quiet)
        self.assertEqual(self.note({'env': {'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_AUTOUPDATER': '1'}}), quiet)

    def test_disable_updates_blocks_claude_update_too(self):
        self.assertEqual(self.note({'env': {'DISABLE_UPDATES': '1', 'DISABLE_AUTOUPDATER': '1'}}), self.BLOCKED)
        self.assertIsNone(self.note({'env': {'DISABLE_UPDATES': '0'}}))

    def test_unreadable_settings_keep_the_default(self):
        for settings in ('{', '[]', '{"env": null}', '{"env": ["DISABLE_AUTOUPDATER"]}', '[' * 100000 + ']' * 100000):
            self.assertIsNone(self.note(settings), settings[:20])


class TextOutput(unittest.TestCase):
    def test_control_characters_never_reach_the_terminal(self):
        evil = 'x\x1b]52;c;aGk=\x07\x1b[2J\x9b31m\x00\ty'
        result = {'checkedAt': '2026-01-01T09:00:00+00:00', 'checked': [], 'skipped': [evil],
                  'problems': [{'text': evil, 'hint': evil}],
                  'items': [{'id': 'a@b', 'kind': 'plugin', 'name': evil, 'have': evil, 'latest': evil, 'note': evil},
                            {'id': 'r', 'kind': 'broken', 'name': evil, 'note': evil, 'detail': evil}]}
        out = cu.text(result)
        self.assertEqual([c for c in out if ord(c) < 32 and c != '\n' or 127 <= ord(c) < 160], [])

    def test_skill_hint_names_the_pinned_cli(self):
        result = {'checkedAt': '2026-01-01T09:00:00+00:00', 'checked': [], 'skipped': [], 'problems': [],
                  'items': [{'id': 'example-skill', 'kind': 'skill', 'name': 'example-skill', 'have': 'a', 'latest': 'b'}]}
        self.assertIn(f'[npx --yes {cu.SKILLS_CLI} update -g example-skill]', cu.text(result))
        self.assertRegex(cu.SKILLS_CLI, r'^skills@\d+\.\d+\.\d+$')


class SilentSkips(unittest.TestCase):
    def test_skill_at_its_repository_root_is_a_not_compared_row(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, '.agents'))
            with open(os.path.join(home, '.agents', '.skill-lock.json'), 'w') as f:
                json.dump({'skills': {'root-skill': {'source': 'example/root-skill', 'sourceType': 'github',
                                                     'skillPath': 'SKILL.md', 'skillFolderHash': '1' * 40}}}, f)
            rep = cu.Report({})
            with mock.patch.object(cu, 'HOME', home), mock.patch.object(cu.shutil, 'which', lambda t: '/bin/' + t), \
                    mock.patch.object(cu, 'run', lambda cmd, timeout=120: '{"SKILL.md": "x"}'):
                cu.check_skills(rep)
        # Shown with its reason, but not a problem: nothing the user can fix, so no exit 2 and no daily notification.
        self.assertEqual(rep.problems, [])
        self.assertEqual([(i['id'], i['kind'], i['note']) for i in rep.items],
                         [('root-skill', 'info', 'not compared: the skill sits at its repository root')])
        self.assertFalse(cu.is_update(rep.items[0]))

    def test_git_subdir_without_a_path_reads_the_repository_root(self):
        asked = []

        def fake(cmd, timeout=120):
            asked.append(cmd[2])
            return base64.encodebytes(json.dumps({'version': '1.0.0'}).encode()).decode()

        with tempfile.TemporaryDirectory() as market:
            os.makedirs(os.path.join(market, '.claude-plugin'))
            with open(os.path.join(market, '.claude-plugin', 'marketplace.json'), 'w') as f:
                json.dump({'plugins': [{'name': 'p', 'source': {'source': 'git-subdir', 'url': 'example/mods', 'path': ''}}]}, f)
            with mock.patch.object(cu.shutil, 'which', lambda t: '/bin/' + t), mock.patch.object(cu, 'run', fake):
                self.assertEqual(cu.advertised(market, 'p'), '1.0.0')
        self.assertEqual(asked, ['repos/example/mods/contents/.claude-plugin/plugin.json'])


class GhPaths(unittest.TestCase):
    """What reaches `gh api` is a repos/<owner>/<repo>/contents path and nothing else."""

    def test_hostile_lock_entries_never_reach_gh(self):
        entry = {'sourceType': 'github', 'skillFolderHash': '1' * 40}
        lock = {'skills': {
            'up-and-out': {**entry, 'source': 'x/y/../../user', 'skillPath': 'skills/a/SKILL.md'},
            'dot-repo': {**entry, 'source': 'x/..', 'skillPath': 'skills/b/SKILL.md'},
            'climbing-path': {**entry, 'source': 'example/skills', 'skillPath': '../../../user/c/SKILL.md'},
            'odd-ref': {**entry, 'source': 'example/skills', 'skillPath': 'skills/d/SKILL.md', 'ref': '..\\x y'},
            'fine': {**entry, 'source': 'example/skills', 'skillPath': '.agents/skills/fine/SKILL.md', 'ref': 'release/1.0'},
        }}
        asked = []

        def fake(cmd, timeout=120):
            asked.append(cmd[2])
            return json.dumps({'fine': '2' * 40})

        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, '.agents'))
            with open(os.path.join(home, '.agents', '.skill-lock.json'), 'w') as f:
                json.dump(lock, f)
            rep = cu.Report({})
            with mock.patch.object(cu, 'HOME', home), mock.patch.object(cu.shutil, 'which', lambda t: '/bin/' + t), \
                    mock.patch.object(cu, 'run', fake):
                cu.check_skills(rep)
        self.assertEqual(asked, ['repos/example/skills/contents/.agents/skills?ref=release%2F1.0'])
        self.assertEqual(rep.problems, [])
        refused = {i['id']: i for i in rep.items if i['kind'] == 'info'}
        self.assertEqual(sorted(refused), ['climbing-path', 'dot-repo', 'odd-ref', 'up-and-out'])
        self.assertTrue(all(i['note'].startswith('not compared') for i in refused.values()))
        self.assertEqual([i['id'] for i in rep.items if i['kind'] == 'skill'], ['fine'])

    def test_a_plugin_repo_with_dot_dot_is_not_asked(self):
        with tempfile.TemporaryDirectory() as market:
            os.makedirs(os.path.join(market, '.claude-plugin'))
            with open(os.path.join(market, '.claude-plugin', 'marketplace.json'), 'w') as f:
                json.dump({'plugins': [{'name': 'p', 'source': {'source': 'github', 'repo': 'x/..'}}]}, f)
            with mock.patch.object(cu.shutil, 'which', lambda t: '/bin/' + t), \
                    mock.patch.object(cu, 'run', side_effect=AssertionError('asked gh')):
                self.assertIsNone(cu.advertised(market, 'p'))


class Cache(unittest.TestCase):
    def test_default_cache_is_uptick_last_json(self):
        result = {'checkedAt': '2026-10-23T09:00:00+02:00', 'checked': [], 'skipped': [], 'problems': [], 'items': []}
        with tempfile.TemporaryDirectory() as home:
            for env, base in (({'XDG_CACHE_HOME': os.path.join(home, 'xdg')}, os.path.join(home, 'xdg')),
                              ({'XDG_CACHE_HOME': ''}, os.path.join(home, '.cache'))):
                with mock.patch.dict(os.environ, env), mock.patch.object(cu, 'HOME', home), \
                        mock.patch.object(cu, 'check', lambda *a: result), mock.patch('sys.stdout', new_callable=io.StringIO):
                    self.assertEqual(cu.main(['--cache']), 0)
                with open(os.path.join(base, 'uptick', 'last.json')) as f:
                    self.assertEqual(json.load(f)['checkedAt'], result['checkedAt'])

    def test_a_failed_write_keeps_the_last_cache_whole(self):
        result = {'checkedAt': '2026-01-02T09:00:00+00:00', 'checked': [], 'skipped': [], 'problems': [], 'items': []}

        def half(obj, f, **kw):
            f.write('{"checkedAt": "2026-01-0')
            raise OSError('disk full')

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'last.json')
            with open(path, 'w') as f:
                f.write('{"checkedAt": "2026-01-01T09:00:00+00:00", "items": []}')
            with mock.patch.object(cu, 'check', lambda *a: result), mock.patch.object(cu.json, 'dump', half), \
                    mock.patch('sys.stdout', new_callable=io.StringIO) as out:
                cu.main([f'--cache={path}'])
            with open(path) as f:
                self.assertEqual(json.load(f)['checkedAt'], '2026-01-01T09:00:00+00:00')
            self.assertEqual(os.listdir(tmp), ['last.json'])  # no temp file left behind
            self.assertIn('cache not written', out.getvalue())


class InSync(unittest.TestCase):
    """The checker, the mod and the menu bar plugin name one skills CLI and one id pattern per kind."""

    def read(self, *path):
        with open(os.path.join(ROOT, *path), encoding='utf-8') as f:
            return f.read()

    def test_one_pin_and_one_pattern_everywhere(self):
        logic, menu = self.read('plugin', 'hooks', 'logic.ts'), self.read('extras', 'macos', 'swiftbar', 'uptick.5m.py')
        pin = r"SKILLS_CLI = '([^']+)'"
        self.assertEqual({re.search(pin, logic).group(1), re.search(pin, menu).group(1)}, {cu.SKILLS_CLI})
        for name in ('PLUGIN_ID', 'SKILL_ID', 'BREW_ID', 'UV_ID'):
            ts = re.search(name + r' = /\^(.+)\$/', logic).group(1).replace('\\/', '/')  # a JS literal escapes /
            py = re.search(name + r" = re\.compile\(r'(.+)'\)", menu).group(1)
            self.assertEqual(ts, py, name)


if __name__ == '__main__':
    unittest.main()
