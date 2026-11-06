#!/usr/bin/env python3
"""uptick checker: which AI dev tools have a newer version. Read-only: installs nothing.

Checks Claude Code (vs the npm registry's latest), installed Claude Code plugins (vs the version
their marketplace advertises), skills-CLI skills (~/.agents/.skill-lock.json tree hash vs GitHub,
through gh), Homebrew and uv tools (uv tool list --outdated, which asks the package index).
A check whose tool or file is absent is skipped. With vulns in --kinds it also scans Homebrew, global npm,
Appium and the plugins (brew vulns, osv-scanner; online, at most once a day each), and the repositories
listed in ~/.config/uptick/scan-repos.json, into ~/.cache/uptick/vulns.json.

Usage: check_updates.py [--json] [--fetch] [--cache [PATH]] [--brewfile PATH]
                        [--known-broken PATH] [--extra COMMAND] [--kinds LIST] [--vulns-now]
  --json               print the result as JSON (schema: README.md) instead of text
  --fetch              refresh marketplace and Homebrew metadata first (slow)
  --cache [PATH]       also write the JSON to PATH (default: ~/.cache/uptick/last.json)
  --brewfile PATH      report only outdated Homebrew packages listed in this Brewfile
  --known-broken PATH  JSON {"<id>": {"match": "<upstream version>", "kind": "...", "note": "...", "since": "..."}}:
                       that upstream version is a note, not an update; `kind` (optional) narrows the entry
                       (default: ~/.config/uptick/known-broken.json, when it exists)
  --extra COMMAND      run COMMAND (split like a shell, run without one) and merge the JSON it prints
  --kinds LIST         comma list of plugin, skill, brew, uv, vulns: check only these (default: plugin, skill, brew)
  --vulns-now          with vulns: rescan every target now, whatever the age of its last scan
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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote

HOME = os.path.expanduser('~')
KINDS = ('plugin', 'skill', 'brew', 'uv', 'vulns')
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


# ---- vulnerability scans: opt-in, `vulns` in kinds ----
# brew vulns and osv-scanner send package names and versions to api.osv.dev. Each target is scanned at most
# once a day; the result, IDs and package names included, stays in the local cache (vulns.json), which the
# inbox app also reads. A row gets only a count and a level.
SCAN_SECONDS = 120
DAY = 86400
RETRY_AFTER = 3600  # a failed scan is not retried on every check
LOCKFILES = ('package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'bun.lock', 'uv.lock', 'poetry.lock', 'Cargo.lock')
LEVELS = ('unknown', 'low', 'medium', 'high', 'critical')
MAX_FOLDERS = 20000  # a listed home folder must not take the whole check's time
INSTALL_HINT = 'brew install osv-scanner'


class ScanError(Exception):
    """A scan that gave no result; its text goes into the cache and a problem row, so it holds no outside text."""


def level_of(severity):
    """A CVSS score ("7.5") or a label ("MODERATE") as one of LEVELS."""
    try:
        score = float(severity)
    except (TypeError, ValueError):
        label = str(severity).lower()
        label = 'medium' if label == 'moderate' else label
        return label if label in LEVELS else 'unknown'
    return 'critical' if score >= 9 else 'high' if score >= 7 else 'medium' if score >= 4 else 'low' if score > 0 else 'unknown'


def top_level(levels):
    """The worst of some levels, '' for none."""
    return max(levels, key=LEVELS.index, default='')


def fixed_version(pkg, vulns):
    """The lowest version above the installed one that fixes every vulnerability of a package, '' when one has
    no such version (a commit fix, last_affected only, or a version that does not parse)."""
    need = None
    for v in vulns:
        found = [e['fixed'] for a in (v.get('affected') if isinstance(v, dict) else None) or [] if isinstance(a, dict)
                 and isinstance(a.get('package'), dict) and a['package'].get('name') == pkg.get('name')
                 and a['package'].get('ecosystem') == pkg.get('ecosystem')
                 for r in a.get('ranges') or [] if isinstance(r, dict) for e in r.get('events') or []
                 if isinstance(e, dict) and isinstance(e.get('fixed'), str) and newer(e['fixed'], pkg.get('version'))]
        if not found:
            return ''
        best = min(found, key=version_key)
        need = best if need is None or newer(best, need) else need
    return need or ''


def parse_osv(data, source_of=lambda path: ''):
    """osv-scanner's JSON as an entry: one detail per vulnerable package, with the lockfiles it is in."""
    pkgs = {}
    for res in data.get('results') or [] if isinstance(data.get('results'), list) else []:
        if not isinstance(res, dict):
            continue
        source = res.get('source')
        path = source.get('path') if isinstance(source, dict) else None
        for p in res.get('packages') or [] if isinstance(res.get('packages'), list) else []:
            vulns = p.get('vulnerabilities') if isinstance(p, dict) else None
            pkg = p.get('package') if isinstance(p, dict) else None
            if not isinstance(vulns, list) or not vulns or not isinstance(pkg, dict):
                continue
            pkg = {k: str(pkg.get(k) or '') for k in ('name', 'version', 'ecosystem')}
            d = pkgs.setdefault((pkg['name'], pkg['version'], pkg['ecosystem']),
                                {**pkg, 'ids': set(), 'severity': '', 'fixed': fixed_version(pkg, vulns), 'sources': set(),
                                 'groups': set()})
            for g in p.get('groups') or [] if isinstance(p.get('groups'), list) else []:
                if isinstance(g, dict):
                    ids = frozenset(i for i in g.get('ids') or [] if isinstance(i, str))
                    d['ids'].update(ids)
                    d['groups'].add(ids)  # one group is one issue, whatever its aliases
                    d['severity'] = max(d['severity'], str(g.get('max_severity') or ''), key=score)
            d['ids'].update(v['id'] for v in vulns if isinstance(v, dict) and isinstance(v.get('id'), str))
            if isinstance(path, str) and source_of(path):
                d['sources'].add(source_of(path))
    details = [{**{k: v for k, v in d.items() if k != 'groups'}, 'ids': sorted(d['ids']), 'level': level_of(d['severity']),
                'sources': sorted(d['sources']), 'advisories': len(d['groups']) or len(d['ids']) or 1}
               for _, d in sorted(pkgs.items())]
    return {'count': len(details), 'max': max((d['severity'] for d in details), key=score, default=''),
            'level': top_level(d['level'] for d in details), 'details': details, 'gaps': []}


def score(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return -1.0


def parse_brew(data):
    """brew vulns --json: formulae with an unpatched vulnerability, and what it did not check. Its fixed
    versions are mostly commits, so `fixed` stays empty."""
    details = []
    # For these brew scanned the current formula, not what is installed: its findings say nothing about the latter.
    current_only = {n for n in data.get('outdated_without_sbom') or [] if isinstance(n, str)}
    for f in data.get('findings') or [] if isinstance(data.get('findings'), list) else []:
        vulns = [v for v in f.get('vulnerabilities') or [] if isinstance(v, dict)] if isinstance(f, dict) else []
        if not vulns or f.get('formula') in current_only:
            continue
        level = top_level(level_of(v.get('severity')) for v in vulns)
        ids = sorted({v['id'] for v in vulns if isinstance(v.get('id'), str)})
        details.append({'name': str(f.get('formula') or ''), 'version': str(f.get('version') or ''), 'ecosystem': 'Homebrew',
                        'ids': ids, 'severity': level.upper(), 'level': level, 'fixed': '', 'sources': [],
                        'advisories': len(ids) or 1})
    skipped = sum(len(data.get(k) or []) for k in ('skipped_formulae', 'unscanned_formulae', 'outdated_without_sbom'))
    gaps = [f'{skipped} formulae not checked by brew vulns'] if skipped or not data.get('complete') else []
    level = top_level(d['level'] for d in details)
    return {'count': len(details), 'max': level.upper(), 'level': level, 'details': details,
            'gaps': gaps + ['casks not covered']}


def scan_json(cmd, label, clean=()):
    """Runs a scanner that exits 1 when it finds something; `clean` exit codes mean "nothing to scan"."""
    timeout = time_left(SCAN_SECONDS)
    if timeout < 1:
        raise ScanError(f'{label} not run: out of time')
    try:
        r = subprocess.run([shutil.which(cmd[0]) or cmd[0], *cmd[1:]], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise ScanError(f'{label} did not run: {type(err).__name__}') from err
    if r.returncode in clean:
        return {}
    try:
        data = json.loads(r.stdout) if r.returncode in (0, 1) else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise ScanError(f'{label} failed (exit {r.returncode}); offline?')
    return data


def osv_lockfiles(paths, label, source_of=lambda path: ''):
    """osv-scanner exits 128 when it finds no packages. The empty --config keeps a scanned project's own
    osv-scanner.toml from ignoring its findings. Every path is absolute (from the user's list or a walk
    under it), so none reads as a flag."""
    if not paths:
        return {'count': 0, 'max': '', 'level': '', 'details': [], 'gaps': []}
    with tempfile.TemporaryDirectory(prefix='uptick-osv-') as tmp:
        config = os.path.join(tmp, 'osv-scanner.toml')
        os.close(os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        cmd = ['osv-scanner', 'scan', 'source', '--config', config, *[a for p in paths for a in ('-L', p)], '--format', 'json']
        return parse_osv(scan_json(cmd, label, clean=(128,)), source_of)


def lockfiles_under(root):
    """The lockfiles under root, and how many were left out: a link (it may point outside root) or a path with
    a colon (osv-scanner reads `x:path` as a parser name)."""
    found, left_out = [], 0
    for n, (here, dirs, files) in enumerate(os.walk(root)):
        if out_of_time():
            raise ScanError('lockfile search stopped: out of time')
        if n >= MAX_FOLDERS:
            raise ScanError(f'more than {MAX_FOLDERS} folders: not scanned')
        dirs[:] = sorted(d for d in dirs if d not in ('node_modules', '.git'))
        for path in (os.path.join(here, f) for f in files if f in LOCKFILES):
            ok = ':' not in os.path.splitdrive(path)[1] and not os.path.islink(path)  # C: on Windows is fine
            found += [path] if ok else []
            left_out += not ok
    return sorted(found), left_out


def sbom(tree):
    """CycloneDX SBOM of an `npm ls -g --all --json` tree: every node with a version, once."""
    comps = {}

    def walk(node):
        deps = node.get('dependencies') if isinstance(node, dict) else None
        for name, dep in deps.items() if isinstance(deps, dict) else ():
            if isinstance(dep, dict) and isinstance(dep.get('version'), str):
                purl = 'pkg:npm/' + quote(name, safe='/') + '@' + quote(dep['version'], safe='')
                comps[purl] = {'type': 'library', 'name': name, 'version': dep['version'], 'purl': purl}
            walk(dep)

    walk(tree)
    return {'bomFormat': 'CycloneDX', 'specVersion': '1.5', 'components': list(comps.values())}


def scan_brew():
    return parse_brew(scan_json(['brew', 'vulns', '--json'], 'brew vulns'))


def appium_lock():
    return os.path.join(HOME, '.appium', 'package-lock.json')


def scan_appium():
    return osv_lockfiles([appium_lock()], 'osv-scanner (Appium)')


def scan_npm_global():
    # npm ls exits 1 on any invalid or missing dependency and still prints the whole tree.
    timeout = time_left(60)
    try:
        r = subprocess.run([shutil.which('npm') or 'npm', 'ls', '-g', '--all', '--json'], capture_output=True, text=True,
                           timeout=timeout) if timeout >= 1 else None
        tree = json.loads(r.stdout) if r is not None and r.returncode in (0, 1) else None
    except (OSError, subprocess.TimeoutExpired, ValueError, RecursionError):
        r, tree = None, None
    if not isinstance(tree, dict) or (r.returncode == 1 and not isinstance(tree.get('dependencies'), dict)):
        raise ScanError('npm ls -g failed')
    with tempfile.TemporaryDirectory(prefix='uptick-sbom-') as tmp:
        path = os.path.join(tmp, 'global-npm.cdx.json')
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
            json.dump(sbom(tree), f)
        return osv_lockfiles([path], 'osv-scanner (global npm)')


def plugin_installs():
    """{install path: (plugin id, version)} from installed_plugins.json, so stale cached versions do not count."""
    with open(os.path.join(HOME, '.claude', 'plugins', 'installed_plugins.json'), encoding='utf-8') as f:
        plugins = json.load(f).get('plugins')
    return {i['installPath']: (pid, str(i.get('version') or '')) for pid, group in plugins.items() if isinstance(group, list)
            for i in group if isinstance(i, dict) and isinstance(i.get('installPath'), str) and os.path.isabs(i['installPath'])}


def left_out_gap(n):
    return [f'{n} lockfiles left out (a link, or a colon in the path)'] if n else []


def scan_plugins():
    try:
        installs = plugin_installs()
    except (OSError, ValueError, AttributeError, RecursionError) as err:
        raise ScanError('installed_plugins.json not readable') from err
    paths, bare, left_out = [], 0, 0
    for root in sorted(installs):
        found, n = lockfiles_under(root)
        paths, bare, left_out = paths + found, bare + (not found), left_out + n

    def source_of(path):
        return next((pid for root, (pid, _) in installs.items() if path.startswith(root.rstrip(os.sep) + os.sep)), '')
    result = osv_lockfiles(paths, 'osv-scanner (Claude Code plugins)', source_of)
    result['gaps'] = ([f'{bare} of {len(installs)} plugin installs without a lockfile'] if bare else []) + left_out_gap(left_out)
    # The version each finding was scanned at: a plugin updated since then is not marked with them.
    result['versions'] = {pid: version for pid, version in installs.values()}
    return result


def scan_repo(root):
    if not os.path.isdir(root):
        raise ScanError('not a folder')
    found, left_out = lockfiles_under(root)
    result = osv_lockfiles(found, 'osv-scanner', lambda path: os.path.relpath(path, root))
    result['gaps'] = left_out_gap(left_out)
    return result


def machine_scanners():
    """target -> (tools it needs, scan); a target whose tool or file is absent is left out."""
    return {
        'brew': (('brew',), scan_brew),
        'npm-global': (('npm', 'osv-scanner'), scan_npm_global),
        'claude-plugins': (('osv-scanner',), scan_plugins),
        **({'appium': (('osv-scanner',), scan_appium)} if os.path.isfile(appium_lock()) else {}),
    }


def config_path(name):
    return os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.join(HOME, '.config'), 'uptick', name)


def vulns_cache_path():
    return os.path.join(os.environ.get('XDG_CACHE_HOME') or os.path.join(HOME, '.cache'), 'uptick', 'vulns.json')


def load_repo_list():
    """The repositories opted in to scanning (scan-repos.json, {"repos": ["/abs/path", ...]}), and problems."""
    path = config_path('scan-repos.json')
    try:
        with open(path, encoding='utf-8') as f:
            listed = json.load(f).get('repos')
    except FileNotFoundError:
        return [], []
    except (OSError, ValueError, AttributeError, RecursionError):
        listed = None
    if not isinstance(listed, list):  # None, not []: a half-saved list must not wipe the repositories' results
        return None, [{'text': 'scan-repos.json not readable: no repository scanned', 'hint': f'fix {path}'}]
    repos = [p for p in listed if isinstance(p, str) and os.path.isabs(p) and '\0' not in p]
    bad = len(listed) - len(repos)
    return sorted(set(repos)), [{'text': f'scan-repos.json: ignored {bad} entries that are not absolute paths',
                                 'hint': f'fix {path}'}] if bad else []


def clean_entry(e):
    """A cache entry with every field of the right type, or None when it holds nothing usable."""
    if not isinstance(e, dict):
        return None

    def num(d, k):
        v = d.get(k)
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 1e13 else 0

    def text(d, k):
        return d[k] if isinstance(d.get(k), str) else ''

    def texts(d, k):
        return sorted({s for s in d[k] if isinstance(s, str)}) if isinstance(d.get(k), list) else []
    details = [{'name': d['name'], 'version': text(d, 'version'), 'ecosystem': text(d, 'ecosystem'), 'ids': texts(d, 'ids'),
                'severity': text(d, 'severity'), 'level': level_of(text(d, 'severity')), 'fixed': text(d, 'fixed'),
                'sources': texts(d, 'sources'), 'advisories': int(num(d, 'advisories')) or len(texts(d, 'ids')) or 1}
               for d in e.get('details') or [] if isinstance(d, dict) and isinstance(d.get('name'), str)] \
        if isinstance(e.get('details'), list) else []
    prev, versions = e.get('prev'), e.get('versions')
    out = {'at': num(e, 'at'), 'tried': num(e, 'tried'), 'count': int(num(e, 'count')), 'max': text(e, 'max'),
           **({'versions': {k: v for k, v in versions.items() if isinstance(v, str)}} if isinstance(versions, dict) else {}),
           'level': top_level(d['level'] for d in details), 'details': details, 'gaps': texts(e, 'gaps'),
           'error': text(e, 'error'), 'hint': text(e, 'hint'),
           'prev': {'count': int(num(prev, 'count')), 'max': text(prev, 'max')} if isinstance(prev, dict) else None}
    return out if out['at'] or out['error'] else None


def entries(raw, keep):
    return {k: e for k, x in (raw.items() if isinstance(raw, dict) else ()) if k in keep and (e := clean_entry(x))}


def refresh(found, scanners, now, force=False):
    """Rescans the due targets in parallel and stores a result or an error for each; returns the missing
    tools as {target: tool}."""
    missing, due = {}, {}
    for target, (tools, fn) in scanners.items():
        absent = next((t for t in tools if not shutil.which(t)), None)
        e = found.get(target) or {'at': 0, 'tried': 0}
        age, tried = now - e['at'], now - e['tried']  # below 0: a clock set back, or a time in ms; scan again
        if absent:
            missing[target] = absent
        elif force or ((age >= DAY or age < 0) and (tried >= RETRY_AFTER or tried < 0)):
            due[target] = fn
    with ThreadPoolExecutor(min(4, max(1, len(due)))) as pool:
        futures = {t: pool.submit(fn) for t, fn in due.items()}
    for target, fut in futures.items():
        old = found.get(target)
        try:
            prev = {'count': old['count'], 'max': old['max']} if old and old['at'] else None  # what "worse" is measured against
            # Stored as it reads back, so a check with nothing due does not rewrite the file.
            found[target] = clean_entry({'error': '', 'hint': '', **fut.result(), 'at': now, 'tried': now, 'prev': prev})
        except Exception as err:  # the last good result stays; the others still count
            last = old or {'at': 0, 'count': 0, 'max': '', 'level': '', 'details': [], 'gaps': [], 'prev': None}
            text = str(err) if isinstance(err, ScanError) else f'scan crashed: {type(err).__name__}'
            found[target] = clean_entry({**last, 'tried': now, 'error': text[:200], 'hint': ''})
    return missing


def scan_vulns(force=False):
    """Scans what is due (the machine, the listed repositories), saves the cache when it changed, and
    returns it with the problems and skips for the report."""
    path = vulns_cache_path()
    try:
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, ValueError, RecursionError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    now = time.time()
    machine = machine_scanners()
    listed, problems = load_repo_list()
    repos = listed or []
    kept = repos if listed is not None else (raw.get('repos') if isinstance(raw.get('repos'), dict) else {})
    cache = {'schema': 1, 'targets': entries(raw.get('targets'), machine), 'repos': entries(raw.get('repos'), kept)}
    missing = refresh(cache['targets'], machine, now, force)
    for root, tool in refresh(cache['repos'], {r: (('osv-scanner',), lambda r=r: scan_repo(r)) for r in repos}, now, force).items():
        old = cache['repos'].get(root) or {'at': 0, 'tried': now, 'count': 0, 'max': '', 'details': [], 'gaps': [], 'prev': None}
        if old.get('error') != f'{tool} not found':  # set once: every check would rewrite the file otherwise
            cache['repos'][root] = clean_entry({**old, 'tried': now, 'error': f'{tool} not found', 'hint': INSTALL_HINT})
    if json.dumps(cache, sort_keys=True) != json.dumps(raw, sort_keys=True):  # also drops what left the list
        try:
            write_atomically(path, cache)
        except OSError as err:
            problems.append({'text': f'vulnerability cache not written: {err.strerror}'})
    skipped = [f'{t} not scanned for vulnerabilities: {tool} not found' + (f' ({INSTALL_HINT})' if tool == 'osv-scanner' else '')
               for t, tool in missing.items() if tool != 'brew']
    return {'cache': cache, 'now': now, 'problems': problems, 'skipped': skipped}


def ago(seconds):
    s = int(seconds)
    return f'{s // DAY} d ago' if s >= DAY else f'{s // 3600} h ago' if s >= 3600 else f'{s // 60} min ago'


def add_vulns(rep, scan):
    """The Uptick view: an outdated formula or plugin with a known vulnerability gets a count and a level on
    its row. A finding without an update row stays in the cache and the checked list: no row without a fix."""
    targets, now = scan['cache']['targets'], scan['now']
    rep.problems += scan['problems']
    rep.skipped += scan['skipped']
    def fresh(target):  # a target that keeps failing must not mark rows with old findings forever
        e = targets.get(target) or {'at': 0}
        return e if 0 <= now - e['at'] < 2 * DAY else {'details': [], 'versions': {}}
    brew = {d['name']: d for d in fresh('brew')['details']}
    plugins, scanned_at = {}, fresh('claude-plugins').get('versions') or {}
    for d in fresh('claude-plugins')['details']:
        for pid in d['sources']:
            plugins.setdefault(pid, []).append(d)
    for item in rep.items:
        have = str(item.get('have', ''))
        if item.get('kind') == 'brew' and not item.get('cask'):
            d = brew.get(item['id']) or brew.get(item['id'].split('/')[-1])
            # Only the findings of the installed version; brew vulns names it without the _1 revision.
            found = [d] if d and d['version'] == re.sub(r'_\d+$', '', have) else []
        elif item.get('kind') == 'plugin':
            found = plugins.get(item['id']) if scanned_at.get(item['id']) == have else []
        else:
            continue
        if found:
            item['vulns'] = {'count': sum(d['advisories'] for d in found), 'level': top_level(d['level'] for d in found)}
    for target, e in targets.items():
        if e['at']:
            stale = [f"scanned {ago(now - e['at'])}"] if now - e['at'] >= DAY else []
            note = '; '.join(e['gaps'] + stale)
            rep.checked.append(f"security: {target}: {e['count']} vulnerable" + (f" (max {e['level']})" if e['level'] else '')
                               + (f' ({note})' if note else ''))
        if e['error']:
            last = f"last good result {ago(now - e['at'])}" if e['at'] else 'never scanned'
            rep.problem(f"security: {target}: scan failed: {e['error']}; {last}", e['hint'] or 'tried again after an hour')
    repos = scan['cache']['repos']
    if repos:
        rep.checked.append(f'security: {len(repos)} repositories (for the inbox)')


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


def check(fetch=False, brewfile=None, known_broken=None, extra=None, kinds=None, vulns_now=False):
    """kinds: the set of KINDS to check, None for all but vulns, which is only ever named; Claude Code and
    the extra check always run. vulns_now: rescan every target, whatever the cache's age."""
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
        with ThreadPoolExecutor(1) as pool:
            # The scans run beside the other checks; their own cache keeps them to once a day per target.
            scans = pool.submit(scan_vulns, vulns_now) if kinds is not None and 'vulns' in kinds else None
            for kind, name, run_check in checks:
                if kind and kinds is not None and kind not in kinds:
                    continue
                try:
                    run_check()
                except Exception as err:  # one broken check must not cost the others, the JSON or the cache
                    rep.problem(f'{name} not checked: {type(err).__name__}: {err}')
            if scans is not None:
                try:
                    add_vulns(rep, scans.result())
                except Exception as err:
                    rep.problem(f'vulnerability scan not done: {type(err).__name__}: {err}')
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
            v = it.get('vulns')
            # An extra check's item comes through whole: only the checker's own shape is shown.
            flag = f"   ! {v['count']} known vulnerabilities (max {v['level']})" if isinstance(v, dict) and isinstance(
                v.get('count'), int) and v.get('level') in LEVELS else ''
            lines.append(f"  ↑ {name}: {it.get('have', '?')} → {it.get('latest', '?')}   [{how}]{flag}")
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
    ap.add_argument('--vulns-now', action='store_true')
    args = ap.parse_args(argv)
    listed = None if args.kinds is None else {k.strip() for k in args.kinds.split(',')} - {''}
    kinds = set(DEFAULT_KINDS) if listed is None else listed & set(KINDS)
    result = check(args.fetch, args.brewfile, args.known_broken, args.extra, kinds, args.vulns_now)
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
