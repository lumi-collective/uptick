# Changelog

## Unreleased

- On Windows, a `winget` kind, off by default: add `winget` to `kinds`. The check runs
  `winget upgrade --disable-interactivity` and reads its table by its layout, one header word per column; a
  header it cannot split (Korean, for one) is a problem row. A confirmed update runs
  `winget upgrade --id <id> --exact --accept-package-agreements --disable-interactivity`, without `cmd.exe`. A
  package whose publisher asks winget to update it only by name, or one whose id winget cut short, is shown as
  `not compared`, never offered. On other systems
  `winget` in `kinds` is one skipped line.
- The Claude Code row follows your release channel: with `autoUpdatesChannel` set to `stable` it compares with
  the npm registry's stable version, not latest. A Homebrew install no longer gets a row that says "updates
  itself": with `brew` in `kinds`, its update shows in the Homebrew group, from `brew outdated`.

## 0.4.0

- The confirm step for one item has **Skip**, with no hotkey: it adds that version to your known-broken list, so
  the item waits under Known issues until a newer version comes. It holds back that version only, so a later
  patch release shows again. A later Skip of the same item replaces its own entry; a list it cannot read and an
  entry you wrote for the item are left alone. The known-broken list learns `"exact": true` for this.
- On macOS, `uptick-check` sends no notification. It used `osascript`, which macOS shows as Script Editor's,
  and a click on it opened an empty Script Editor window. Linux keeps `notify-send`.
- `vulns.json` says how often it scans: `"interval": {"ok": 86400, "retry": 3600}`, in seconds, so a tool that
  reads the file, such as Signoff, can tell a fresh result from a stale one.
- The SwiftBar menu bar plugin is gone from `extras/`. Since 0.3.1 a new session checks on its own, and the line
  above the prompt shows the count. A link to `uptick.5m.py` in a SwiftBar plugin folder now points at nothing;
  remove it.

## 0.3.1

- A new session checks in the background, fetching metadata first, when no check has fetched it for a day. The
  line above the prompt then shows updates without `/uptick`, which the desktop app cannot run. Nothing goes into
  the conversation.
- The interactive `/uptick` reply is one line; the list is in the pane. `claude -p "/uptick"` still prints the
  whole report.
- With no list yet, a check that failed shows in the line above the prompt, with what to do.
- On Windows, the hint for a missing Python names `winget install -e --id Python.Python.3.13`.
- No `claude` on the PATH is a skipped line, not silence. A missing gh names the command that installs it; a gh
  that gets no answer says signed out or offline. On Windows, no `Homebrew not found` line.
- A plugin that runs from its folder (a directory marketplace) is compared at the folder's version, not at the
  version it was installed at, so it no longer shows a false update.
- A refused environment read at session start no longer drops the rest of the start.

## 0.3.0

- The checked list names Signoff, which shows the scanned repositories, instead of "the inbox".
- On Windows, the confirm step and the text report show the command as it runs: `cmd.exe /d /c` before
  `npx` and `claude`.
- The `/uptick` reply says when a fresh check is running; the list above it is the last check's.
- A listed repository that could not be scanned is a problem row that says why, also when updates are listed.
- On Windows, the hint for a missing osv-scanner is `winget install --exact --id Google.OSVScanner`.
- The checker's tests skip the symlink cases on a Windows account that may not make symlinks.

## 0.2.0

- A vulnerability scan, off by default; on when `vulns` is in `kinds`. Once a day per target (an hour after a failed scan), `brew vulns`
  and `osv-scanner` (api.osv.dev) scan Homebrew, global npm, Appium and the plugins' lockfiles. An update row
  whose installed version is vulnerable shows a count and a level (`"vulns"` in the JSON); a finding without
  an update is only counted. The details stay in `~/.cache/uptick/vulns.json`, which also holds the scans of
  the repositories listed in `~/.config/uptick/scan-repos.json`, for other tools. `--vulns-now` rescans.
- A new kind, `uv`: tools installed with `uv tool install`, read from `uv tool list --outdated` (uv 0.10.10+,
  asks PyPI) and updated with `uv tool upgrade <name>`. Off by default; on when `uv` is in `kinds`. A tool
  installed from git, a folder, or an exact pin shows under Info as `not compared`. One with a range, or
  pinned through `-c`/`--overrides` (which uv's list does not show), is offered.
- A plugin with several installs (user scope and a project's) is compared at the one the pane's update
  reaches, not at the first one listed, so its row no longer stays after the update or goes missing.
- An unknown entry in `kinds` (such as `skill;`) now shows a problem row instead of being dropped silently.
- The Claude Code row no longer says "updates itself" when its auto-update is off (`DISABLE_UPDATES`,
  `DISABLE_AUTOUPDATER` or `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, in the environment or in
  `~/.claude/settings.json`): it says so, and with `DISABLE_AUTOUPDATER` alone it says to run `claude update`.
- A `scan-repos.json` saved with a byte order mark (as Windows PowerShell 5.1 writes UTF-8) is read.
- A shorter README with a new demo; the details moved to `docs/reference.md`.
- Tested with Claude Code 2.1.319.

## 0.1.0

First release.

- `/uptick` pane: updates for Claude Code, plugins, skills-CLI skills and Homebrew, grouped, with a
  confirm step that shows the exact command; Update all; Stop after the current item; output of a failed
  update. **Reload plugins** types `/reload-plugins` into the prompt for you to run.
- A line above the prompt when updates are waiting, also while Claude works, or a status line entry, or
  nothing (`hint`). A running session re-reads the cache every 15 minutes, so a result from the daily check reaches it without a
  restart.
- `claude -p "/uptick"`: a read-only text report.
- Settings: `kinds`, `brewfile`, `knownBroken`, `checkCommand`, `hint`, `fetchOnOpen`.
- A known-broken list that holds back an upstream version, optionally per kind.
- The checker, `plugin/checker/check_updates.py`: Python 3.9+, standard library only; JSON and text output,
  a cache file, a time limit that keeps a slow fetch from costing the result.
- The daily check, `plugin/bin/uptick-check`. The plugin's `bin/` folder keeps it to Claude Code on your own
  computer.
- `plugin/` is the whole plugin; tests, media and companions stay outside it.
- Companions in `extras/`: schedulers for launchd, systemd and Task Scheduler, a SwiftBar menu bar plugin
  and a Raycast command.
