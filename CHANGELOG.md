# Changelog

## Unreleased

- An unknown entry in `kinds` (such as `skill;`) now shows a problem row instead of being dropped silently.
- The Claude Code row no longer says "updates itself" when its auto-update is off (`DISABLE_UPDATES`,
  `DISABLE_AUTOUPDATER` or `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, in the environment or in
  `~/.claude/settings.json`): it says so, and with `DISABLE_AUTOUPDATER` alone it says to run `claude update`.

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
