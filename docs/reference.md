# Uptick reference

The details behind the [README](../README.md). Where it works is in the README's
[Platforms](../README.md#platforms).

## Files Uptick keeps

Uninstalling leaves these files of Uptick's own; remove them by hand if you want:

- the last result: `${XDG_CACHE_HOME:-~/.cache}/uptick/last.json` (on Windows `%USERPROFILE%\.cache\uptick\last.json`);
- with `vulns` in `kinds`, the scan results: `${XDG_CACHE_HOME:-~/.cache}/uptick/vulns.json`;
- the known-broken list, if you or Skip made one: `${XDG_CONFIG_HOME:-~/.config}/uptick/known-broken.json`;
- the list of repositories to scan, if you made one: `${XDG_CONFIG_HOME:-~/.config}/uptick/scan-repos.json`;
- the daily check's settings, if you made them: `${XDG_CONFIG_HOME:-~/.config}/uptick/check.env`.

## Vulnerability scan

With `vulns` in `kinds`, the checker also looks for known vulnerabilities, once a day per target (an hour after a
failed scan): your Homebrew formulae (`brew vulns`), and with [osv-scanner](https://google.github.io/osv-scanner/)
the global npm packages, the Appium install (`~/.appium/package-lock.json`, when it exists) and the lockfiles of
your installed plugins. Install osv-scanner with `brew install osv-scanner`, or on Windows with
`winget install --exact --id Google.OSVScanner`; without it, those targets are skipped and the list says so.

An update row whose installed version was found vulnerable in the last two days says how many and how bad
(`3 vulns (high)`): the update replaces that version. A finding with no update to offer is counted in the checked list
only, never a row of its own: a row always comes with a command. A target that fails to scan is a problem row that
says why and when its last good result was.

The details, with the advisory IDs and package names, stay in a local file,
`${XDG_CACHE_HOME:-~/.cache}/uptick/vulns.json`. The checker also scans the repositories you list in
`${XDG_CONFIG_HOME:-~/.config}/uptick/scan-repos.json` (`{"repos": ["/absolute/path", ...]}`; none by default) into
the same file, for other tools such as Signoff to show; their findings are not rows in Uptick. The checked list counts
them (`security: 2 repositories (for Signoff)`). A listed repository that fails to scan is a problem row that says
why, with a hint: `security: 1 of 2 repositories (for Signoff): scan failed: osv-scanner not found`. `--vulns-now`
rescans everything at once.

## Known-broken list

Hold back an upstream version you know is broken. It shows under Known issues, not as an update, until
upstream moves past it.

```json
{
  "example-skill": { "match": "0000000", "note": "invalid YAML front matter", "since": "2026-01-01" },
  "example-tool": { "match": "1.1", "kind": "brew", "note": "breaks shell completion" }
}
```

A skill's `match` is the start of its upstream tree hash. A version's `match` is the version, or its
start up to a non-digit: `1.1` holds back `1.1` and `1.1.3`, not `1.10.0`. With `"exact": true` a version's
`match` holds back that version only. `kind` (`plugin`, `skill`, `brew`, `uv`, `winget`) is optional and limits the
entry to that kind.

**Skip**, in the confirm step for one item, adds an entry for you: the latest version as `match`, the item's
`kind`, `"exact": true`, `"note": "skipped in the Uptick pane"` and today's date as `since`. So it holds back that
version only: a later patch release (`2.82.1.1`, a rebuild `2.82.1_1`) shows as an update again. It rewrites the
file as indented JSON and keeps the other entries; it replaces an entry it wrote itself for the same kind, so a
later version can be skipped too. A file it cannot read, one that is not a JSON object, or one with any other entry
for that id stays as it is, and a short message says why; so does a file it cannot write to. The list is keyed by id alone, so a formula and a cask with
one name share one entry. A relative `knownBroken` path is under your home folder, as the checker reads it. While
a check runs, Skip is not shown. The rewrite is not atomic: a full disk can leave the file cut short, and the
checker then reports it as not readable. Delete the entry to undo.

## Your own checks

`checkCommand` runs a command (split like a shell, run without one; on Windows passed on as one command line) that
prints JSON with any of `items`, `problems`, `checked` and `skipped`, in the schema below. Uptick merges it. If it
fails, the rest of the list still shows, with one problem row. Give your items the kind `info` (or `self`): Uptick
shows them and runs nothing for them.

For example, a small script for tools that `mise` manages:

```python
#!/usr/bin/env python3
import json, subprocess
out = json.loads(subprocess.run(['mise', 'outdated', '--json'], capture_output=True, text=True).stdout or '{}')
print(json.dumps({'checked': ['mise'], 'items': [
    {'id': name, 'kind': 'info', 'name': name, 'have': t['current'], 'latest': t['latest'], 'note': 'mise upgrade'}
    for name, t in out.items()]}))
```

Set `checkCommand` to `python3 -I /path/to/mise_check.py`. `npm outdated -g --json` has the same `current`
and `latest` per package, so the same script works for it with that command swapped in.

### The checker's JSON

```json
{
  "schema": 1,
  "checkedAt": "2026-01-01T09:12:00+00:00",
  "fetched": false,
  "checked": ["Claude Code 2.1.306", "14 plugins", "31 skills", "Homebrew"],
  "skipped": ["uv not found, skipped"],
  "items": [
    { "id": "example-plugin@example-market", "kind": "plugin", "name": "example-plugin@example-market", "have": "1.4.0", "latest": "1.5.0" },
    { "id": "example-tool", "kind": "brew", "name": "example-tool", "have": "2.0.0", "latest": "2.1.0" },
    { "id": "claude-code", "kind": "self", "name": "Claude Code", "have": "2.1.306", "latest": "2.1.310" },
    { "id": "my-script", "kind": "info", "name": "my-script", "have": "1", "latest": "2", "note": "run my-update.sh" },
    { "id": "example-skill", "kind": "broken", "name": "example-skill", "have": "a1b2c3d", "latest": "0000000",
      "note": "upstream known broken, keeping installed", "detail": "since 2026-01-01: invalid YAML front matter" }
  ],
  "problems": [{ "text": "2 skills not checked: gh got no answer from GitHub (signed out, or offline)", "hint": "run gh auth login" }]
}
```

`items` lists only what has a newer version, or a note. A cask carries `"cask": true`. With the
vulnerability scan on, an update whose installed version is vulnerable carries
`"vulns": {"count": 3, "level": "high"}` (`level`: `unknown`, `low`, `medium`, `high` or `critical`). New
fields may be added; existing ones keep their names.

`vulns.json` holds `interval` (how often a target is scanned, in seconds: `ok` after a good scan, `retry` after a
failed one), `targets` (`brew`, `npm-global`, `appium`, `claude-plugins`) and `repos` (keyed by the real path of
each entry in `scan-repos.json`, links resolved; a path that is not a folder gets an entry with an `error`). Each
entry has `at` and `tried` (Unix seconds of the last good scan and of the last try), `count`, `max`, `level`,
`gaps`, `error`, `hint`, `prev` (the count and max of the scan before) and `details`: one per vulnerable package,
worst `level` first, then by name, with `name`, `version`, `ecosystem`, `ids`, `advisories` (how many issues, an
alias counted once), `severity`, `level`, `fixed` (the lowest version that fixes them all, empty when there is
none) and `sources` (the lockfiles within a repository, or the plugin ids). The plugins' entry also has
`versions`, each plugin's version at the scan. A lockfile that is a link, or has a
colon in its path, is left out and counted in `gaps`.

### Running the checker yourself

```
python3 -I -X utf8 plugin/checker/check_updates.py [--json] [--fetch] [--cache [PATH]] [--brewfile PATH]
                                                   [--known-broken PATH] [--extra COMMAND] [--kinds LIST]
                                                   [--vulns-now]
```

On Windows, write `py -3` for `python3`. Exit code: 0 when everything is current, 1 when updates are
available, 2 when there are only problems. Known-broken notes, `not compared` items and Claude Code's own
update do not count. The whole check, `--fetch` included, stops after about seven minutes; what is
left out is named in one problem row, and the result and the cache still come out.

## Troubleshooting

- **`checker could not start (python3)`**: Claude Code found no Python. On macOS run
  `xcode-select --install` or `brew install python`; on Linux install your distribution's `python3`; on
  Windows install Python 3 from python.org with its `py` launcher, or `winget install -e --id Python.Python.3.13`.
  The pane says which, and with no list yet, so does the line above the prompt.
- **`claude not found, plugins not checked`**: the checker found no `claude` on its PATH. Add the folder Claude
  Code was installed to (with the native installer, `~/.local/bin`) to your PATH.
- **`Homebrew not found, skipped`** although you have it: Claude Code was started without Homebrew on its
  PATH (for example from the Dock). Start it from a terminal, or add `/opt/homebrew/bin` to the PATH it
  inherits.
- **`skills not checked: gh not found`**: install gh (`brew install gh`, on Windows `winget install --id GitHub.cli`),
  then run `gh auth login`.
- **`skills not checked: gh got no answer from GitHub`**: run `gh auth login`, or check the network.
- **`not scanned for vulnerabilities: osv-scanner not found`**: install it with `brew install osv-scanner`, or on
  Windows `winget install --exact --id Google.OSVScanner`. If Uptick still says it is not found after the winget
  install, start Claude Code again from a new terminal.
- **Garbled characters on Windows**: the checker runs in UTF-8 mode (`-X utf8`); if your console still
  shows `â†'` for `→`, run `chcp 65001` or use Windows Terminal.

## Limits and not supported

- Package managers other than Homebrew, uv tools and winget: scoop, choco, apt, pacman, nix and pip. Add them
  with `checkCommand`.
- winget is checked only on Windows; elsewhere `winget` in `kinds` is one skipped line, and the check never runs a
  `winget` found on the PATH. winget prints a table, not JSON, with headers in the Windows display language. The
  check reads this layout: a header line of one word per column (Name, Id, Version, Available and, with more
  than one source, Source), a dashed line under it, then rows padded to the columns, counting an East Asian wide
  character as two cells. A header it cannot split into four or five words (Korean has two-word headers), a
  first line that is not a row, a row after the footer, a line with a carriage return, or an id with a
  character outside letters, digits, `.`, `_`, `+` and `-` is a problem row, never a guess. A last row whose
  Available version does not start with a digit cannot be told from the footer and is left out.
- winget's explicit-targeting table holds the packages whose publisher asks winget to update them only by name
  (RequireExplicitUpgrade): each is shown under Info as `not compared: its publisher asks winget to update it
  only by name`, never offered and never in Update all. A table after the first, or one with text just above its
  header (the table alone, under its heading), counts as that table; a warning above the main table makes its
  rows Info too. Packages you pinned with `winget pin add` are left out, as winget leaves them out without
  `--include-pinned`. A package whose
  id winget cut short (it ends in `…`) is shown as `not compared` too. A package whose installed version winget
  cannot tell is left out (no `--include-unknown`).
- The winget update accepts the package's agreement (`--accept-package-agreements`), not a source's: a source
  whose agreement you have not accepted in winget fails the update with winget's message. It runs with
  winget's default installer mode, which shows the installer's progress; an installer that asks for
  administrator rights still shows the Windows prompt.
- Pinned Homebrew packages are left out.
- The Claude Code row reads its release channel only from `autoUpdatesChannel` in `~/.claude/settings.json`,
  and only `stable` switches it: any other value, or a channel set in managed settings, under
  `CLAUDE_CONFIG_DIR` or at install time, compares with latest.
- A Claude Code that `claude` on the `PATH` links to in a Homebrew Caskroom gets no Claude Code row: the cask
  can trail the npm registry, and an upgrade installs the cask's version, not npm's. With `brew` in `kinds`,
  `brew outdated` lists its update in the Homebrew group (with a Brewfile set, only when the Brewfile lists the
  cask). An install through another package manager, such as WinGet, apt, dnf or apk, is not told apart: its row
  still compares with npm and says it updates itself.
- A uv tool installed from git, a URL or a folder, or with an exact pin (`name==1.0`), is shown under Info
  as `not compared`, never offered: `uv tool upgrade` keeps its source or pin. Reinstall it with
  `uv tool install name@latest` to follow the latest again.
- A uv tool installed with a range (`name<6`, `<=`, `~=`, `!=`) is offered, and `uv tool upgrade` stays
  inside the range. uv lists it against the index's overall latest, so when nothing newer exists inside the
  range the update shows as done and the row comes back on the next check.
- A pin set with `uv tool install -c` (constraints) or `--overrides` does not show in `uv tool list`, so the
  tool is offered. With a constraint pin, `uv tool upgrade` prints its pin hint and the pane shows a failure;
  with an override pin it only says "Nothing to upgrade", the pane shows it as done, and the row comes back on
  the next check.
- The vulnerability scan does not cover casks, and `brew vulns` skips formulae it cannot map to a source
  (the checked list says how many). A finding in a plugin's dependencies marks the plugin's update, which
  may or may not carry the fix. A finding in a global npm package or Appium is never a row: Uptick does not
  update those. A scan waits for its lockfile search, which stops at 20000 folders (`node_modules` and
  `.git` aside) and then fails that target; the update list waits for the scans, within the check's time
  limit. A row narrower than 40 columns shows no count.
- An entry in `kinds` that is not `plugin`, `skill`, `brew`, `uv`, `winget` or `vulns` (a typo such as `skill;`)
  is ignored, so the group you meant is not checked. The result shows a problem row that names the entry.
- Plugins are listed as Claude Code sees them in the folder the check runs from (from the pane: your home
  folder), so a plugin installed only in another project's scope is not listed unless your user settings turn
  it on. When a plugin has several installs, the one compared is the one `claude plugin update` updates from
  your home folder: an install made in your home folder itself, else the user-scope one, else the first local
  one, else the first project one. An older install in a project is not shown; run `claude plugin update` in
  that project.
- A skill at the root of its repository is not compared; it shows under Info with the reason. A skill installed at a
  pinned `ref` is compared at that ref, as the skills CLI does.
- A cask that needs an administrator password fails in the pane; run `brew upgrade --cask <id>` in a
  terminal.
- An update shows a timer, not its live output; the full output is under `o` after a failure.
- `s` stops after the current item: stopping Homebrew halfway can leave a broken install.

## Customize it with Claude

The code is the mod's hooks (`plugin/hooks/`) and one Python file, both in `plugin/`. Fork it, then:

```
git clone https://github.com/<you>/uptick && cd uptick
claude --plugin-dir plugin
```

The session loads your copy; run `/reload-plugins` after a change. Claude Code's `plugin-authoring` skill
knows the mod API; `AGENTS.md` lists the rules this code keeps. A prompt to start from:

> Read AGENTS.md. Add a check for `<tool>` to plugin/checker/check_updates.py: its installed version from
> `<command>`, its latest from `<source>`, shown under Info. Write the failing test first, then run
> both test suites.

## Companions

`extras/` holds optional pieces that read or refresh the same cache file outside Claude Code: a daily
check for launchd (macOS), systemd (Linux) and Task Scheduler (Windows), and a Raycast command. See
[extras/README.md](../extras/README.md).

The daily check itself is `plugin/bin/uptick-check`. Claude Code puts a plugin's `bin/` on its Bash tool's
PATH, so a session can run it as `uptick-check`. A running session re-reads the cache every 15 minutes, so
a scheduled check shows up in the line above the prompt without a restart.

## Uptick vs topgrade

[topgrade](https://github.com/topgrade-rs/topgrade) upgrades everything on your system in one go; Uptick
shows what is new around Claude Code and updates one item at a time, after you confirm it.

## Development and tests

```
claude plugin validate plugin
claude plugin validate .claude-plugin/marketplace.json
claude plugin test plugin
python3 -I -B -m unittest discover -s tests/checker -p "test_*.py"
shellcheck plugin/bin/uptick-check extras/macos/raycast/uptick-check.sh
```

`plugin/` is the plugin: what an install gets. The checker's tests and fixtures, the media and `extras/` stay
outside it.

The tests fake the tools Uptick checks and updates (the extra-check tests run small Python scripts of their own);
none runs an update. CI runs 8 jobs: the checker on Linux, macOS and Windows with
Python 3.9 and 3.13, and the mod's validate and tests on the pinned Claude Code (2.1.319) on Linux and Windows.

