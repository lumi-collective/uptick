# Uptick

Uptick shows what is out of date around Claude Code and updates it only after you confirm the exact
command. Type /uptick to open a pane that lists newer versions of Claude Code itself, your plugins, the
skills you installed with the skills CLI, your Homebrew packages, and, if you turn them on, the tools you
installed with uv tool install. Turned on, an opt-in vulnerability scan marks the updates that replace a
vulnerable version. Pick one item, or all of them, read
the command it will run, and confirm. A line above the prompt tells you when updates are waiting.

## Where it works

Claude Code on your own computer only. In the terminal everything works. In the local session of the
desktop app the pane and the line above the prompt draw, but /uptick does not run there; the line's Show
button opens the pane. It needs Python 3.9 or newer on that computer. It does not work on claude.ai on
the web or on mobile, or in Cowork.

## What it runs, and when

- When the pane opens, when you press Check now, again after each run of confirmed updates, and for a
  claude -p "/uptick" report: the bundled checker, with python3 (on Windows, the py launcher). The
  checker asks claude, npm, gh, brew and, when uv is in the kinds setting, uv (uv tool list --outdated) for
  installed and latest versions. When vulns is in the kinds setting, at most once a day per target, it
  also runs brew vulns, npm ls -g and osv-scanner on the lockfiles it found, with an empty osv-scanner
  config of its own, so that a scanned folder's config cannot hide a finding. It reads only; with Check now
  (or on every open, if you turn on fetchOnOpen) it first refreshes marketplace and Homebrew metadata.
- Only after you press Update in the confirm step: one fixed command per item, built from a template and
  the item's name, which must match a strict pattern. A plugin updates through claude, a skill through
  npx running the skills CLI (pinned to version 1.7.4), a Homebrew package through brew, a uv tool
  through uv tool upgrade. Each runs from
  your home folder, as an argument list without a shell; on Windows, npx and claude start through cmd.exe.
- Reload plugins never runs anything: it only types /reload-plugins into your prompt, and you press Enter.
- Your own extra check command, if you set one in the settings.

The plugin also adds uptick-check, the same check as a command line for a daily scheduler. When it finds
updates or problems, or the check fails, it sends a desktop notification (osascript on macOS, notify-send on
Linux); --no-notify turns that off. If a settings file in your config folder exists, it runs that file as
shell code to set its options (UPTICK_BREWFILE, UPTICK_KNOWN_BROKEN, UPTICK_CHECK_COMMAND, UPTICK_KINDS,
UPTICK_PATH), and it puts UPTICK_PATH, or by default your ~/.local/bin and the usual Homebrew folders, before
PATH.

## What it reads

Its own cache file (the last result, in your cache folder), the metadata of your installed plugins and
skills, the list of your uv tools with the version specifier or source each was installed with (through
uv), a Brewfile if you set one, and a known-broken list if you set one or the default file exists in
your config folder. When vulns is in the kinds setting, it also reads its scan file (vulns.json in your
cache folder), the list of your installed plugins and the lockfiles under each plugin install, the Appium
lockfile in ~/.appium, and the list of repositories to scan (scan-repos.json in your config folder, none
by default) with the lockfiles of each repository it lists. It reads the environment variables HOME, USERPROFILE, OS, XDG_CACHE_HOME and
XDG_CONFIG_HOME to find those files. To tell whether Claude Code updates itself, it reads DISABLE_UPDATES,
DISABLE_AUTOUPDATER and CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC from the environment and from the env
block of your Claude Code settings.json; nothing else in that file.

What it passes on: the item names to the update commands (a uv tool's name to uv tool upgrade); your marketplace names to claude plugin
marketplace update; the GitHub repositories, folders and refs named in your plugin and skill metadata to gh
api, which asks GitHub for them; and the Brewfile path, the known-broken path and your extra check command
to the checker.

## What it sends

Uptick itself sends nothing anywhere and has no telemetry. The programs it starts fetch what they need:
the npm registry for the latest Claude Code version, the GitHub API through gh for skill folders and
plugin manifests, your marketplaces and Homebrew when you check now, and whatever an update you confirm
downloads.

uv tools are off by default. Add uv to the kinds setting (in /config, UPTICK_KINDS for the daily check, or
--kinds) to turn them on; uv tool list --outdated then sends the name of every uv tool to PyPI (or the
package index you set for uv) on every check, also of one installed from git, a URL or a folder, which is
shown as not compared.

The vulnerability scan is off by default. Add vulns to the kinds setting to turn it on; at most once a
day per target, brew vulns and osv-scanner then send the names and versions of your Homebrew formulae,
global npm packages, Appium and plugin dependencies, and of the packages in the lockfiles of the
repositories you listed, to api.osv.dev, and brew vulns downloads Homebrew's public advisory list from
formulae.brew.sh and raw.githubusercontent.com. The advisory IDs
and package names stay in the local scan file; an update row shows only a count and a level.

## What its hooks do

On session start it registers /uptick and reads the cache. It answers the /uptick command and passes
every other command on untouched. It notes when its own pane is closed, and it draws its pane and its
line above the prompt, which stays while Claude works.

## Install

At the prompt of a Claude Code session in a terminal:

```
/plugin install uptick --marketplace lumi-collective/uptick
```

Full documentation, settings and source: https://github.com/lumi-collective/uptick
