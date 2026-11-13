# Companions

Optional pieces that work outside Claude Code. They read or refresh the same cache file the mod reads,
`${XDG_CACHE_HOME:-~/.cache}/uptick/last.json`, so the pane opens on a fresh result. None of them installs
anything.

They run the checker in the clone's plugin folder, `plugin/`, from a clone of this repository:

```
git clone https://github.com/lumi-collective/uptick ~/src/uptick
```

`git pull` in the clone updates them. The paths below assume `~/src/uptick`.

## The daily check: `plugin/bin/uptick-check`

The launcher lives in `plugin/bin/uptick-check`, no longer in `extras/bin/`: a launchd or systemd schedule set
up with the old path must be set up again from the commands below.

Runs the checker with `--fetch --cache` and prints the report. On Linux it sends a `notify-send`
notification when something needs a look. On macOS it sends none; the line above the prompt shows the result.
`--no-fetch` makes it quick, `--no-notify` keeps it quiet.

It reads the `knownBroken` you set in `/config` itself. Give it the other options you set there as environment
variables, in `${XDG_CONFIG_HOME:-~/.config}/uptick/check.env`; `UPTICK_KNOWN_BROKEN` there wins over
`knownBroken`. The file is read as shell, so quote a value that holds a space:

```
UPTICK_BREWFILE=~/Brewfile
UPTICK_CHECK_COMMAND="python3 -I /path/to/my_check.py"
UPTICK_KINDS=plugin,skill,brew
```

Add `uv` to `UPTICK_KINDS` to check uv tools too; the name of every uv tool then goes to PyPI (or your uv
index) on every check. Add `vulns` to turn on the vulnerability scan; package names and versions then go to
api.osv.dev once a day per target (see [the vulnerability scan](../docs/reference.md#vulnerability-scan)).

A scheduler starts it with a bare PATH; it adds `~/.local/bin` and the usual Homebrew folders. If your
`claude`, `npm`, `brew` or `uv` live elsewhere, set `UPTICK_PATH` in the same file.

### macOS: launchd

```
sed -e "s|__UPTICK__|$HOME/src/uptick|" -e "s|__LOG__|$HOME/Library/Logs/uptick-check.log|" \
  ~/src/uptick/extras/macos/launchd/uptick.check.plist > ~/Library/LaunchAgents/uptick.check.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/uptick.check.plist
launchctl kickstart gui/$(id -u)/uptick.check
```

It runs every day at 09:00 (edit `StartCalendarInterval` to change it). To stop it:
`launchctl bootout gui/$(id -u)/uptick.check`, then remove the plist.

### Linux: systemd

```
mkdir -p ~/.config/systemd/user
sed "s|__UPTICK__|$HOME/src/uptick|" ~/src/uptick/extras/linux/systemd/uptick-check.service \
  > ~/.config/systemd/user/uptick-check.service
cp ~/src/uptick/extras/linux/systemd/uptick-check.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now uptick-check.timer
```

The report goes to the journal: `journalctl --user -u uptick-check`. To stop it:
`systemctl --user disable --now uptick-check.timer`.

### Windows: Task Scheduler

See [windows/README.md](windows/README.md).

## Raycast (macOS)

`macos/raycast/uptick-check.sh` is a script command that prints a quick report (no fetch). Link it into
your Raycast script folder:

```
ln -s ~/src/uptick/extras/macos/raycast/uptick-check.sh "<your Raycast script folder>/"
```
