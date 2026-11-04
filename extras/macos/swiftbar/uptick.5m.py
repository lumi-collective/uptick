#!/usr/bin/env -S python3 -I
# <swiftbar.title>Uptick</swiftbar.title>
# <swiftbar.desc>Tool updates for Claude Code, plugins, skills, Homebrew and uv tools, from Uptick's cache.</swiftbar.desc>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
"""SwiftBar plugin: Uptick in the menu bar.

Reads ${XDG_CACHE_HOME:-~/.cache}/uptick/last.json, the file the mod and plugin/bin/uptick-check write.
Link it into the SwiftBar plugin folder (do not copy it): it finds the clone through the link.

Without arguments it prints the menu. `update <kind> <id> [cask]` and `update-all` run the same per-kind
commands as the mod (plugin/hooks/logic.ts argvFor) in a terminal, then check again. `--self-test` runs its checks.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SELF = Path(__file__).resolve()
CHECK = SELF.parent.parent.parent.parent / 'plugin' / 'bin' / 'uptick-check'
CACHE = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'uptick' / 'last.json'
# SwiftBar starts plugins with a bare PATH: add where claude, brew, npx and uv usually live.
PATH = os.pathsep.join([str(Path.home() / '.local/bin'), '/opt/homebrew/bin', '/usr/local/bin', os.environ.get('PATH', '')])

# The same templates and id patterns as plugin/hooks/logic.ts: the id reaches a command line.
SKILLS_CLI = 'skills@1.7.4'
PLUGIN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}@[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
SKILL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
BREW_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9@._+-]{0,127}(/[A-Za-z0-9][A-Za-z0-9@._+-]{0,127}){0,2}')
UV_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
UPDATE_ENV = {'skill': {'DISABLE_TELEMETRY': '1', 'DO_NOT_TRACK': '1'}}
GROUPS = [('plugin', 'Plugins'), ('skill', 'Skills'), ('brew', 'Homebrew'), ('uv', 'uv tools')]
NOTE_GROUPS = [(('broken',), 'Known issues', 'exclamationmark.triangle'), (('self', 'info'), 'Info', 'info.circle')]


def argv_for(kind, id_, cask=False):
    if kind == 'plugin' and PLUGIN_ID.fullmatch(id_):
        return ['claude', 'plugin', 'update', id_]
    if kind == 'skill' and SKILL_ID.fullmatch(id_):
        return ['npx', '--yes', SKILLS_CLI, 'update', '-g', id_]
    if kind == 'brew' and BREW_ID.fullmatch(id_):
        return ['brew', 'upgrade', *(['--cask'] if cask else []), id_]
    if kind == 'uv' and UV_ID.fullmatch(id_):
        return ['uv', 'tool', 'upgrade', id_]
    return None


def clean(v, limit=120):
    """One safe menu line: SwiftBar reads `|` as its parameter separator and a newline as a new item."""
    return re.sub(r'[\x00-\x1f\x7f-\x9f|]', ' ', str(v))[:limit]


def load():
    try:
        data = json.loads(CACHE.read_text())
        return data if isinstance(data, dict) and isinstance(data.get('items'), list) else None
    except (OSError, ValueError):
        return None


def checked_at(iso, now=None):
    """`09:12` when the check ran today, else `10-19 09:12`, in the check's own time zone."""
    try:
        at = datetime.fromisoformat(iso)
    except ValueError:
        return iso or 'unknown'
    today = (now or datetime.now().astimezone()).astimezone(at.tzinfo).date()
    return at.strftime('%H:%M' if at.date() == today else '%m-%d %H:%M')


def menu(data, now=None):
    check_now = f'Check now | bash={CHECK} param1=--no-notify terminal=false refresh=true'
    if data is None:
        return ['⚠ | sfimage=exclamationmark.triangle', '---', f'No readable {CACHE.name} yet | color=gray', check_now]
    items = [i for i in data['items'] if isinstance(i, dict)]
    updatable = [i for i in items if i.get('kind') in dict(GROUPS) and argv_for(i.get('kind'), str(i.get('id', '')), i.get('cask') is True)]
    out = [f'{len(updatable)} | sfimage=arrow.up.circle.fill' if updatable else '| sfimage=checkmark.circle', '---']
    out.append(f'Uptick · checked {clean(checked_at(str(data.get("checkedAt", "")), now))} | color=gray size=11')
    for p in data.get('problems') or []:
        if isinstance(p, dict):
            hint = f' ({p["hint"]})' if p.get('hint') else ''
            out.append(f'{clean(str(p.get("text", "")) + hint)} | sfimage=wifi.exclamationmark color=orange')
    for kind, title in GROUPS:
        group = [i for i in updatable if i['kind'] == kind]
        if group:
            out += ['---', f'{title} ({len(group)}) | color=gray size=11']
            for i in group:
                cask = ['param4=cask'] if i.get('cask') is True else []
                out.append(' '.join([f'{clean(i.get("name"))}  {clean(i.get("have"), 30)} → {clean(i.get("latest"), 30)} |',
                                     f'bash={SELF} param1=update param2={kind} param3={clean(i["id"])}', *cask,
                                     'terminal=true refresh=true']))
    for kinds, title, icon in NOTE_GROUPS:
        group = [i for i in items if i.get('kind') in kinds]
        if group:
            out += ['---', f'{title} ({len(group)}) | color=gray size=11']
            for i in group:
                note = i.get('note') or ('updates itself' if i['kind'] == 'self' else '')
                out.append(f'{clean(i.get("name"))}  {clean(i.get("have"), 30)} → {clean(i.get("latest"), 30)}  {clean(note)}'
                           f' | sfimage={icon} color=gray')
                if i.get('detail'):
                    out.append(f'--{clean(i["detail"], 200)}')
    out.append('---')
    if updatable:
        out.append(f'Update all | bash={SELF} param1=update-all terminal=true refresh=true')
    out.append(check_now)
    return out


def run_updates(targets):
    """targets: (kind, argv) pairs, run one by one."""
    failed = 0
    for kind, argv in targets:
        print('$', ' '.join(argv), flush=True)
        failed += subprocess.run(argv, env={**os.environ, 'PATH': PATH, **UPDATE_ENV.get(kind, {})}).returncode != 0
    print('Checking again…', flush=True)
    subprocess.run([str(CHECK), '--no-fetch', '--no-notify'], env={**os.environ, 'PATH': PATH}, stdout=subprocess.DEVNULL)
    return 1 if failed else 0


def self_test():
    assert argv_for('brew', 'example-tool') == ['brew', 'upgrade', 'example-tool']
    assert argv_for('brew', 'example-app', True) == ['brew', 'upgrade', '--cask', 'example-app']
    assert argv_for('brew', 'example/tap/widget')
    assert argv_for('plugin', 'example-plugin@example-market') == ['claude', 'plugin', 'update', 'example-plugin@example-market']
    assert argv_for('skill', 'example-skill') == ['npx', '--yes', SKILLS_CLI, 'update', '-g', 'example-skill']
    assert argv_for('uv', 'example-tool') == ['uv', 'tool', 'upgrade', 'example-tool']
    for kind, bad in [('brew', '-x'), ('brew', 'a;b'), ('brew', '../x'), ('skill', 'a b'), ('plugin', 'noat'),
                      ('skill', 'x' * 200), ('broken', 'x'), ('info', 'x'), ('uv', '--x'), ('uv', 'a/b'), ('uv', 'a b')]:
        assert argv_for(kind, bad) is None, (kind, bad)
    assert clean('a|b\nc\x1b[2J') == 'a b c [2J'
    now = datetime.fromisoformat('2026-01-01T18:00:00+00:00')
    m = menu({'checkedAt': '2026-01-01T13:24:15+00:00', 'problems': [{'text': 'p', 'hint': 'run gh auth login'}], 'items': [
        {'id': 'claude-code', 'kind': 'self', 'name': 'Claude Code', 'have': '1', 'latest': '2'},
        {'id': 'notes@example', 'kind': 'info', 'name': 'notes@example', 'have': '0000000', 'latest': '?', 'note': 'not compared'},
        {'id': 'example-tool', 'kind': 'brew', 'name': 'example-tool', 'have': '1', 'latest': '2'},
        {'id': 'bad;id', 'kind': 'brew', 'name': 'bad', 'have': '1', 'latest': '2'},
        {'id': 'r', 'kind': 'broken', 'name': 'r', 'note': 'n', 'detail': 'd'},
        {'id': 'lint@example', 'kind': 'plugin', 'name': 'lint@example', 'have': '1', 'latest': '2'},
        {'id': 'example-cli', 'kind': 'uv', 'name': 'example-cli', 'have': '1', 'latest': '2'}]}, now)
    assert m[0].startswith('3 '), m[0]  # info and known issues are not counted
    assert m[2].startswith('Uptick · checked 13:24 |'), m[2]
    assert m[3].startswith('p (run gh auth login) |'), m[3]
    heads = [line.split(' |')[0] for line in m if line.endswith('color=gray size=11') and '(' in line]
    assert heads == ['Plugins (1)', 'Homebrew (1)', 'uv tools (1)', 'Known issues (1)', 'Info (2)'], heads
    assert any(line.startswith('Claude Code  1 → 2  updates itself |') for line in m), m
    assert any(line.startswith('notes@example  0000000 → ?  not compared |') for line in m), m
    assert checked_at('2025-12-30T08:30:00+00:00', now) == '12-30 08:30'
    assert menu(None)[0].startswith('⚠')
    print('self-test ok')


def main(args):
    if args[:1] == ['--self-test']:
        return self_test()
    if args[:1] == ['update'] and len(args) >= 3:
        argv = argv_for(args[1], args[2], args[3:4] == ['cask'])
        if not argv:
            sys.exit(f'refused: not a valid {args[1]} id: {args[2]!r}')
        return run_updates([(args[1], argv)])
    if args[:1] == ['update-all']:
        items = [i for i in (load() or {'items': []})['items'] if isinstance(i, dict)]
        targets = [(kind, a) for kind, _ in GROUPS for i in items if i.get('kind') == kind  # in the menu's order
                   if (a := argv_for(kind, str(i.get('id', '')), i.get('cask') is True))]
        return run_updates(targets)
    print('\n'.join(menu(load())))


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
