#!/usr/bin/env python3
"""uptick checker: which AI dev tools have a newer version. Read-only: installs nothing.

Checks Claude Code (vs the npm registry's latest), installed Claude Code plugins (vs the version
their marketplace advertises), skills-CLI skills (~/.agents/.skill-lock.json tree hash vs GitHub,
through gh), Homebrew and uv tools (uv tool list --outdated, which asks the package index).
A check whose tool or file is absent is skipped.

Usage: check_updates.py [--json] [--fetch] [--cache [PATH]] [--brewfile PATH]
                        [--known-broken PATH] [--extra COMMAND] [--kinds LIST]
  --json               print the result as JSON (schema: README.md) instead of text
  --fetch              refresh marketplace and Homebrew metadata first (slow)
  --cache [PATH]       also write the JSON to PATH (default: ~/.cache/uptick/last.json)
  --brewfile PATH      report only outdated Homebrew packages listed in this Brewfile
  --known-broken PATH  JSON {"<id>": {"match": "<upstream version>", "kind": "...", "note": "...", "since": "..."}}:
                       that upstream version is a note, not an update; `kind` (optional) narrows the entry
                       (default: ~/.config/uptick/known-broken.json, when it exists)
  --extra COMMAND      run COMMAND (split like a shell, run without one) and merge the JSON it prints
  --kinds LIST         comma list of plugin, skill, brew, uv: check only these (default: plugin, skill, brew)
Exit code: 0 = all current, 1 = updates available, 2 = only problems. Known-broken notes,
`not compared` items and self-updating tools are not updates.
"""
import argparse
import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from urllib.parse import quote

HOME = os.path.expanduser('~')
KINDS = ('plugin', 'skill', 'brew', 'uv')
# uv is opt-in: its check sends the name of every uv tool to the package index.
DEFAULT_KINDS = ('plugin', 'skill', 'brew')
# The skills CLI the mod runs, pinned (hooks/logic.ts SKILLS_CLI).
SKILLS_CLI = 'skills@1.7.4'
# The mod builds the real argv from its own templates; these are only hints in the text output.
HOW = {
    'plugin': 'claude plugin update {id}',
    'skill': 'npx --yes ' + SKILLS_CLI + ' update -g {id}',
    'brew': 'brew upgrade {id}',
    'uv': 'uv tool upgrade {id}',
}
EXTRA_HINT = 'check uptick.checkCommand in /config'
# The note of a plugin that could not be compared: shown, never counted as an update.
NOT_COMPARED = 'not compared'
SELF_NOTE = 'updates itself; new sessions get it'
SELF_OFF_NOTE = 'auto-update is off: run claude update'
SELF_BLOCKED_NOTE = 'updates are turned off (DISABLE_UPDATES)'
# This flag also stops claude update from looking up a version, so the note names no command.
SELF_QUIET_NOTE = 'auto-update is off (CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC)'
# The whole check, fetch included, ends well inside the mod's 10-minute limit on the checker; what is
# left when time runs out becomes a problem row, and the JSON and the cache still come out.
TOTAL_SECONDS = 420
FETCH_MARKETPLACES_SECONDS = 120
FETCH_BREW_SECONDS = 150
DEADLINE = None  # time.monotonic() value, set by check()
# C0 and C1 control characters but the newline: none of an outside string's escapes reach a terminal.
CONTROL = re.compile('[\x00-\x09\x0b-\x1f\x7f-\x9f]')


def is_repo(name):
    """owner/repo as GitHub names them; never `.` or `..`, which would climb out of repos/ in an API path."""
    return bool(re.fullmatch(r'[\w-][\w.-]*/[\w.-]+', name)) and name.split('/')[1] not in ('.', '..')


def is_folder(path):
    """A relative folder path ('' for the top) with no `.` or `..` segment."""
    return path == '' or all(re.fullmatch(r'[\w.-]+', p) and p not in ('.', '..') for p in path.split('/'))


def time_left(cap):
    """cap, cut to what is left of the check's time."""
    return cap if DEADLINE is None else min(cap, DEADLINE - time.monotonic())


def out_of_time():
    """The check's time is used up: a missing answer then says nothing about the network or a login."""
    return time_left(1) < 1


def run(cmd, timeout=120, err=None):
    """stdout of a command that exited 0, else None (missing tool, failure, timeout, or out of time).
    err: a list that gets the command's stderr, when it ran."""
    timeout = time_left(timeout)
    if timeout < 1:
        return None
    try:
        # which() also finds Windows' .cmd shims (npm, npx), which subprocess does not look for.
        r = subprocess.run([shutil.which(cmd[0]) or cmd[0], *cmd[1:]], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if err is not None:
        err.append(r.stderr)
    return r.stdout.strip() if r.returncode == 0 else None


VERSION = re.compile(r'v?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?')


def version_key(v):
    """A sortable key for a version like 1.2.0-rc.1, or None. 1.2 and 1.2.0 have one key; a release sorts
    after its pre-releases, whose dot parts compare as semver says (numbers below words)."""
    m = VERSION.fullmatch(str(v))
    if not m:
        return None
    nums = [int(x) for x in m.group(1).split('.')]
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    pre = m.group(2)
    tail = [(0, int(p), '') if p.isdigit() else (1, 0, p) for p in pre.split('.')] if pre else None
    return nums, (0, tail) if pre else (1, [])


def newer(latest, current):
    """True when latest is strictly newer than current, False when not, None when either is no version.
    A pre-release is never newer than a stable version."""
    a, b = version_key(latest), version_key(current)
    if a is None or b is None:
        return None
    if a[1][0] == 0 and b[1][0] == 1:
        return False
    return a > b


def parse_brewfile(path):
    """Names of the brew and cask entries of a Brewfile, each also by its last path segment (tap/name)."""
    names = set()
    with open(os.path.expanduser(path)) as f:
        for line in f:
            m = re.match(r'\s*(?:brew|cask)\s+["\']([^"\']+)["\']', line)
            if m:
                names.update({m.group(1), m.group(1).split('/')[-1]})
    return names


def load_known_broken(path):
    """The known-broken list, or {} when no file is named and the default one is absent."""
    explicit = path is not None
    if path is None:
        base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(HOME, '.config')
        path = os.path.join(base, 'uptick', 'known-broken.json')
    try:
        with open(os.path.expanduser(path)) as f:
            data = json.load(f)
    except FileNotFoundError:
        if explicit:
            raise
        return {}
    if not isinstance(data, dict):
        raise ValueError('not a JSON object')
    return data


class Report:
    def __init__(self, known_broken):
        self.known_broken = known_broken
        self.items, self.problems, self.checked, self.skipped = [], [], [], []

    def problem(self, text, hint=None, offline=False):
        self.problems.append({'text': text, **({'hint': hint} if hint else {}), **({'offline': True} if offline else {})})

    def held_back(self, item, upstream):
        """The known-broken entry that holds back this upstream version, or None. A skill's upstream is a
        tree hash, matched by prefix; a version matches exactly, or as a prefix that a non-digit follows
        (1.1 holds back 1.1 and 1.1.3, not 1.10.0)."""
        kb = self.known_broken.get(item['id'])
        if not isinstance(kb, dict) or not kb.get('match') or kb.get('kind') not in (None, item.get('kind')):
            return None
        up, match = str(upstream), str(kb['match'])
        if item.get('kind') == 'skill':
            return kb if up.startswith(match) else None
        return kb if up == match or (up.startswith(match) and not up[len(match)].isdigit()) else None

    def offer(self, item, upstream):
        """Adds an update, or a known-broken note when the upstream version is on the list."""
        kb = self.held_back(item, upstream)
        if kb:
            note = str(kb.get('note') or '')
            since = str(kb.get('since') or '')
            item = {**item, 'kind': 'broken', 'note': 'upstream known broken, keeping installed',
                    'detail': f'since {since}: {note}' if since else note}
        self.items.append(item)


def self_update_note():
    """The Claude Code row's note when Claude Code skips its own updates, else None. Its flags come from its
    environment, which the env block of ~/.claude/settings.json extends and overrides; a scheduled check
    inherits only the first, so it reads the file too."""
    env = dict(os.environ)
    # shortcut: managed settings, CLAUDE_CONFIG_DIR and a lower-case key on Windows are missed; add one when a user needs it.
    try:
        with open(os.path.join(HOME, '.claude', 'settings.json'), encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get('env'), dict):
            env.update(data['env'])
    except (OSError, ValueError, RecursionError):
        pass

    def is_set(name):  # Claude Code's reading: 1, true, yes or on, in any case
        return str(env.get(name, '')).strip().lower() in ('1', 'true', 'yes', 'on')
    if is_set('DISABLE_UPDATES'):
        return SELF_BLOCKED_NOTE
    if env.get('CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC') not in (None, ''):  # any value counts
        return SELF_QUIET_NOTE
    if is_set('DISABLE_AUTOUPDATER'):
        return SELF_OFF_NOTE
    return None


def check_claude(rep):
    if not shutil.which('claude'):
        return
    current = (run(['claude', '--version']) or '').split(' ')[0]
    if not current:
        rep.problem('Claude Code: version not readable')
        return
    rep.checked.append(f'Claude Code {current}')
    if not shutil.which('npm'):
        rep.skipped.append('npm not found, Claude Code version not compared')
        return
    latest = run(['npm', 'view', '@anthropic-ai/claude-code', 'version'], timeout=60)
    if latest is None:
        if not out_of_time():
            rep.problem('could not reach the npm registry', offline=True)
        return
    is_newer = newer(latest, current)
    item = {'id': 'claude-code', 'name': 'Claude Code', 'have': current, 'latest': latest}
    if is_newer is None:
        rep.items.append({**item, 'kind': 'info', 'note': NOT_COMPARED})
    elif is_newer:
        # Native installs update themselves unless a flag stops them; running sessions stay on the old binary.
        note = self_update_note()
        rep.items.append({**item, 'kind': 'self', **({'note': note} if note else {})})


def advertised(mpath, name):
    """The version a marketplace advertises for a plugin: its entry's, else its plugin.json's."""
    try:
        with open(os.path.join(mpath, '.claude-plugin', 'marketplace.json')) as f:
            entry = next(e for e in json.load(f).get('plugins', []) if e.get('name') == name)
        if entry.get('version') is not None:
            return str(entry['version'])
        src = entry.get('source')
        if isinstance(src, str):
            with open(os.path.join(mpath, src, '.claude-plugin', 'plugin.json')) as f:
                return json.load(f).get('version')
        # A plugin in a GitHub repo, whole or a folder of it (git-subdir): its plugin.json there,
        # at the pinned ref if any, through gh.
        kind = src.get('source') if isinstance(src, dict) else None
        repo, sub = (src.get('repo'), '') if kind == 'github' else (None, '')
        if kind == 'git-subdir':
            m = re.fullmatch(r'(?:https://github\.com/)?([\w.-]+/[\w-]+(?:\.[\w-]+)*?)(?:\.git)?/?', str(src.get('url', '')))
            path = str(src.get('path') or '').strip('/')
            repo, sub = (m.group(1) if m else None), path + '/' if path else ''  # no path: the repository root
        if (isinstance(repo, str) and is_repo(repo) and re.fullmatch(r'(?:[\w-][\w.-]*/)*', sub)
                and shutil.which('gh')):
            ref = src.get('sha') or src.get('ref')
            path = f'repos/{repo}/contents/{sub}.claude-plugin/plugin.json' + (f'?ref={quote(str(ref), safe="")}' if ref else '')
            out = run(['gh', 'api', path, '--jq', '.content'])
            return json.loads(base64.b64decode(out)).get('version') if out else None
    except (OSError, StopIteration, ValueError, AttributeError):
        pass
    return None


def check_plugins(rep, fetch):
    if not shutil.which('claude'):
        return
    root = os.path.join(HOME, '.claude', 'plugins', 'marketplaces')
    if fetch and os.path.isdir(root):
        until = time.monotonic() + FETCH_MARKETPLACES_SECONDS
        for m in sorted(os.listdir(root)):
            run(['claude', 'plugin', 'marketplace', 'update', m], timeout=min(60, until - time.monotonic()))
    try:
        listed = json.loads(run(['claude', 'plugin', 'list', '--json'], timeout=60) or 'null')
    except ValueError:
        listed = None
    listed = listed.get('installed', listed) if isinstance(listed, dict) else listed
    if not isinstance(listed, list):
        rep.problem('plugins not checked: claude plugin list failed')
        return
    # A directory marketplace lives where it was added, not under marketplaces/.
    try:
        with open(os.path.join(HOME, '.claude', 'plugins', 'known_marketplaces.json')) as f:
            known = json.load(f)
    except (OSError, ValueError):
        known = {}
    seen = set()
    for p in listed:
        if not isinstance(p, dict):
            continue
        pid, ver = str(p.get('id', '')), p.get('version')
        if '@' not in pid or pid in seen or p.get('enabled') is False:
            continue
        seen.add(pid)
        name, mp = pid.split('@', 1)
        entry = known.get(mp) if isinstance(known, dict) else None
        location = entry.get('installLocation') if isinstance(entry, dict) else None
        latest = advertised(location if isinstance(location, str) else os.path.join(root, mp), name)
        is_newer = newer(latest, ver)
        if is_newer is None:
            # A git-hash version, or no advertised one: listed under Info so it is not silently missing.
            rep.items.append({'id': pid, 'kind': 'info', 'name': pid, 'have': str(ver or '?'),
                              'latest': str(latest or '?'), 'note': NOT_COMPARED})
        elif is_newer:
            rep.offer({'id': pid, 'kind': 'plugin', 'name': pid, 'have': str(ver), 'latest': str(latest)}, latest)
    rep.checked.append(f'{len(seen)} plugins')


def check_skills(rep):
    try:
        with open(os.path.join(HOME, '.agents', '.skill-lock.json')) as f:
            lock = json.load(f)['skills']
    except FileNotFoundError:
        return
    except (OSError, KeyError, ValueError, TypeError):
        lock = None
    if not isinstance(lock, dict):
        rep.problem('skills not checked: ~/.agents/.skill-lock.json not readable')
        return
    github = {k: e for k, e in lock.items() if isinstance(e, dict) and e.get('sourceType') == 'github'}
    if not github:
        return
    rep.checked.append(f'{len(github)} skills')
    if not shutil.which('gh'):
        rep.problem(f'{len(github)} skills not checked: gh not found', 'install the GitHub CLI')
        return
    trees, missed = {}, 0
    for skill, e in sorted(github.items()):
        # The skill folder's git tree hash vs the same folder upstream: at the lock's `ref` (a pinned commit
        # or a branch, as the skills CLI itself checks it), else on the default branch.
        parent, base = os.path.split(os.path.dirname(str(e.get('skillPath', ''))))
        if not base:
            rep.items.append({'id': skill, 'kind': 'info', 'name': skill, 'have': str(e.get('skillFolderHash', ''))[:7] or '?',
                              'latest': '?', 'note': f'{NOT_COMPARED}: the skill sits at its repository root'})
            continue
        ref = str(e.get('ref') or '')
        key = (str(e.get('source', '')), parent, ref)
        # The lock is outside data: only a plain owner/repo, folder and ref reach the GitHub API.
        if not (is_repo(key[0]) and is_folder(parent) and re.fullmatch(r'[\w./-]{0,200}', ref) and '..' not in ref):
            rep.items.append({'id': skill, 'kind': 'info', 'name': skill, 'have': str(e.get('skillFolderHash', ''))[:7] or '?',
                              'latest': '?', 'note': f'{NOT_COMPARED}: its lock entry names no plain GitHub path'})
            continue
        if key not in trees:
            path = f'repos/{key[0]}/contents/{parent}' + (f'?ref={quote(ref, safe="")}' if ref else '')
            out = run(['gh', 'api', path, '--jq', '[.[] | {(.name): .sha}] | add'])
            try:
                trees[key] = json.loads(out) if out else None
            except ValueError:
                trees[key] = None
        if not isinstance(trees[key], dict):
            missed += 1
            continue
        upstream, have = trees[key].get(base), str(e.get('skillFolderHash', ''))
        if upstream and upstream != have:
            rep.offer({'id': skill, 'kind': 'skill', 'name': skill, 'have': have[:7], 'latest': upstream[:7]}, upstream)
    if missed and not out_of_time():
        rep.problem(f'{missed} skills not checked: GitHub not reachable', 'run gh auth login')


def check_brew(rep, fetch, brewfile):
    if not shutil.which('brew'):
        rep.skipped.append('Homebrew not found, skipped')
        return
    try:
        wanted = parse_brewfile(brewfile) if brewfile else None
    except OSError:
        rep.problem(f'Homebrew not checked: Brewfile not readable: {brewfile}', 'check uptick.brewfile in /config')
        return
    if fetch:
        run(['brew', 'update', '--quiet'], timeout=FETCH_BREW_SECONDS)
    try:
        data = json.loads(run(['brew', 'outdated', '--json=v2'], timeout=300) or 'null')
    except ValueError:
        data = None
    if not isinstance(data, dict):
        rep.problem('Homebrew not checked: brew outdated failed')
        return
    for group, is_cask in (('formulae', False), ('casks', True)):
        entries = data.get(group)
        for p in entries if isinstance(entries, list) else []:
            if not isinstance(p, dict):
                continue
            name = str(p.get('name') or '')
            if not name or p.get('pinned'):
                continue
            if wanted is not None and name not in wanted and name.split('/')[-1] not in wanted:
                continue
            have = str((p.get('installed_versions') or ['?'])[-1])
            latest = str(p.get('current_version') or '?')
            rep.offer({'id': name, 'kind': 'brew', 'name': name, 'have': have, 'latest': latest,
                       **({'cask': True} if is_cask else {})}, latest)
    rep.checked.append('Homebrew')


# A tool line of uv tool list (uv 0.10.10+): `name v1.2.0 [required: <spec>] [latest: 1.3.0]`, the
# required part only for an install with a specifier or a source; its `- ` lines name executables.
UV_TOOL = re.compile(r'(\S+) v(\S+)(?: \[required: ([^\]]*)\])? \[latest: ([^\]\s]+)\]')


def check_uv(rep):
    if not shutil.which('uv'):
        rep.skipped.append('uv not found, skipped')
        return
    err = []
    out = run(['uv', 'tool', 'list', '--outdated', '--show-version-specifiers', '--color', 'never'], timeout=120, err=err)
    if out is None:
        stderr = ''.join(err)
        if 'unexpected argument' in stderr:  # a uv from before --outdated
            rep.skipped.append('uv < 0.10.10: update it, or drop uv from kinds')
        elif not out_of_time():
            first = re.sub(r'^error: ', '', (stderr.strip().splitlines() or ['uv tool list failed'])[0])
            rep.problem(f'uv tools not checked: {first}', offline='Failed to fetch' in stderr)
        return
    for line in out.splitlines():
        m = UV_TOOL.fullmatch(line.strip())
        if not m:
            continue
        name, have, spec, latest = m.group(1), m.group(2), (m.group(3) or '').strip(), m.group(4)
        # uv compares by name with the index's latest, but uv tool upgrade keeps an install's source and an
        # exact pin: such a row would never clear. A range is offered: its upgrade moves within the range.
        if '://' in spec:  # git, a URL, a wheel or a folder: uv prints each as a URL
            rep.items.append({'id': name, 'kind': 'info', 'name': name, 'have': have, 'latest': '?',
                              'note': f'{NOT_COMPARED}: installed from {spec}'})
        elif '==' in spec and '*' not in spec:
            rep.items.append({'id': name, 'kind': 'info', 'name': name, 'have': have, 'latest': latest,
                              'note': f'{NOT_COMPARED}: installed as {spec}, which uv tool upgrade keeps'})
        else:
            rep.offer({'id': name, 'kind': 'uv', 'name': name, 'have': have, 'latest': latest}, latest)
    rep.checked.append('uv tools')


def merge_extra(rep, command):
    """Runs the extra command and merges its JSON (same schema); a failure is one problem row."""
    try:
        # Windows takes one command line as it is; shlex's POSIX rules would eat its backslashes.
        argv = command if os.name == 'nt' else shlex.split(command)
    except ValueError as err:
        rep.problem(f'extra check not run: {err}', EXTRA_HINT)
        return
    if not argv:
        return
    timeout = time_left(300)
    if timeout < 1:
        rep.problem('extra check not run: out of time', EXTRA_HINT)
        return
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as err:
        rep.problem(f'extra check could not run: {err}', EXTRA_HINT)
        return
    try:
        data = json.loads(r.stdout)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        last = (r.stderr.strip().splitlines() or ['no JSON output'])[-1]
        rep.problem(f'extra check exited {r.returncode}: {last}', EXTRA_HINT)
        return
    for item in data.get('items') or []:
        if isinstance(item, dict) and isinstance(item.get('id'), str) and item['id'] and isinstance(item.get('kind'), str):
            rep.offer(item, item.get('latest', ''))  # the known-broken list holds back extra items too
    rep.problems += [p if isinstance(p, dict) else {'text': str(p)} for p in data.get('problems') or []]
    rep.checked += [str(c) for c in data.get('checked') or []]
    rep.skipped += [str(s) for s in data.get('skipped') or []]


def check(fetch=False, brewfile=None, known_broken=None, extra=None, kinds=None):
    """kinds: the set of KINDS to check, None for all; Claude Code and the extra check always run."""
    global DEADLINE
    try:
        rep = Report(load_known_broken(known_broken))
    except (OSError, ValueError):
        rep = Report({})
        rep.problem(f'known-broken list not readable: {known_broken}', 'check uptick.knownBroken in /config')
    checks = [
        (None, 'Claude Code', lambda: check_claude(rep)),
        ('plugin', 'plugins', lambda: check_plugins(rep, fetch)),
        ('skill', 'skills', lambda: check_skills(rep)),
        ('brew', 'Homebrew', lambda: check_brew(rep, fetch, brewfile)),
        ('uv', 'uv tools', lambda: check_uv(rep)),
        (None, 'extra check', lambda: merge_extra(rep, extra) if extra else None),
    ]
    DEADLINE = time.monotonic() + TOTAL_SECONDS
    try:
        for kind, name, run_check in checks:
            if kind and kinds is not None and kind not in kinds:
                continue
            try:
                run_check()
            except Exception as err:  # one broken check must not cost the others, the JSON or the cache
                rep.problem(f'{name} not checked: {type(err).__name__}: {err}')
        if out_of_time():
            rep.problem(f'check stopped after {TOTAL_SECONDS // 60} minutes: some results are missing',
                        'try again later, or narrow kinds in /config')
    finally:
        DEADLINE = None
    return {
        'schema': 1,
        'checkedAt': datetime.now().astimezone().isoformat(timespec='seconds'),
        'fetched': fetch,
        'checked': rep.checked,
        'skipped': rep.skipped,
        'items': rep.items,
        'problems': rep.problems,
    }


def is_not_compared(item):
    return item.get('kind') == 'info' and str(item.get('note', '')).startswith(NOT_COMPARED)


def is_update(item):
    """What the exit code counts: not a note, not a self-updating tool, not an uncompared item."""
    return item.get('kind') not in ('broken', 'self') and not is_not_compared(item)


def text(result):
    lines = [f"Uptick · checked {result['checkedAt'][:16].replace('T', ' ')}"]
    for it in result['items']:
        name = it.get('name') or it['id']
        if it['kind'] == 'broken':
            lines.append(f"  ! {name}: {it.get('note', '')}" + (f" ({it['detail']})" if it.get('detail') else ''))
        elif is_not_compared(it):
            lines.append(f"  · {name}: {it.get('have', '?')} ({it['note']})")
        elif it['kind'] == 'self':
            lines.append(f"  · {name}: {it.get('have', '?')} → {it.get('latest', '?')} ({it.get('note') or SELF_NOTE})")
        else:
            how = it.get('note') or HOW.get(it['kind'], it['kind']).format(id=it['id'])
            lines.append(f"  ↑ {name}: {it.get('have', '?')} → {it.get('latest', '?')}   [{how}]")
    lines += [f"  ? {p.get('text', '')}" + (f" ({p['hint']})" if p.get('hint') else '') for p in result['problems']]
    lines += [f'  · {s}' for s in result['skipped']]
    if not result['items'] and not result['problems']:
        lines.append('  everything is current')
    return CONTROL.sub(' ', '\n'.join(lines))


def main(argv=None):
    ap = argparse.ArgumentParser(description='Read-only update check for AI dev tools.')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--fetch', action='store_true')
    ap.add_argument('--cache', nargs='?', const='', default=None, metavar='PATH')
    ap.add_argument('--brewfile')
    ap.add_argument('--known-broken')
    ap.add_argument('--extra')
    ap.add_argument('--kinds')
    args = ap.parse_args(argv)
    listed = None if args.kinds is None else {k.strip() for k in args.kinds.split(',')} - {''}
    kinds = set(DEFAULT_KINDS) if listed is None else listed & set(KINDS)
    result = check(args.fetch, args.brewfile, args.known_broken, args.extra, kinds)
    unknown = ', '.join(f'"{k}"' for k in sorted(listed - set(KINDS))) if listed else ''
    if unknown:
        # A typo must not silently turn a group off.
        result['problems'].append({
            'text': f'kinds: ignored unknown {unknown} (valid: {", ".join(KINDS)})',
            'hint': 'fix kinds in /config, UPTICK_KINDS or --kinds'})
    if args.cache is not None:
        path = os.path.expanduser(args.cache) if args.cache else os.path.join(
            os.environ.get('XDG_CACHE_HOME') or os.path.join(HOME, '.cache'), 'uptick', 'last.json')
        try:
            write_atomically(path, result)
        except OSError as err:
            result['problems'].append({'text': f'cache not written: {err}'})
    print(json.dumps(result, indent=1) if args.json else text(result))
    updates = [i for i in result['items'] if is_update(i)]
    return 2 if result['problems'] and not updates else 1 if updates else 0


def write_atomically(path, data):
    """A reader sees the old file or the new one, never half of one."""
    folder = os.path.dirname(path) or '.'
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix='.last.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


if __name__ == '__main__':
    sys.exit(main())
