#!/usr/bin/env bash
# Source this before recording: it builds a throwaway world in /tmp/uptick-demo with synthetic data and fake
# brew, claude, npx, uv and python3 commands that only print. Nothing real is checked or updated, and Claude Code
# runs with an empty config folder and home, so no account, path or plugin of the recording machine shows.
# Usage, from the repository root: source media/demo/setup.sh
demo=/tmp/uptick-demo
real_claude=$(command -v claude)
mkdir -p "$demo"/bin "$demo"/home "$demo"/config "$demo"/cache/uptick "$demo"/data
rsync -a plugin/ "$demo"/uptick/

now=$(date '+%Y-%m-%dT%H:%M:%S')
self='{"id": "claude-code", "kind": "self", "name": "Claude Code", "have": "2.1.319", "latest": "2.1.320"}'
cat > "$demo"/data/before.json <<JSON
{"schema": 1, "checkedAt": "$now", "fetched": false, "skipped": [], "problems": [],
 "checked": ["Claude Code 2.1.319", "3 plugins", "6 skills", "Homebrew", "uv tools"],
 "items": [
  {"id": "example-plugin@example-market", "kind": "plugin", "name": "example-plugin@example-market", "have": "1.4.0", "latest": "1.5.0"},
  {"id": "example-skill", "kind": "skill", "name": "example-skill", "have": "a1b2c3d", "latest": "e4f5a6b"},
  {"id": "example-tool", "kind": "brew", "name": "example-tool", "have": "2.0.0", "latest": "2.1.0",
   "vulns": {"count": 2, "level": "high"}},
  {"id": "example-app", "kind": "brew", "name": "example-app", "have": "3.2", "latest": "3.3", "cask": true},
  {"id": "example-cli", "kind": "uv", "name": "example-cli", "have": "0.4.1", "latest": "0.5.0"},
  {"id": "example-helper", "kind": "broken", "name": "example-helper", "have": "0f0f0f0", "latest": "0000000",
   "note": "upstream known broken, keeping installed", "detail": "since 2026-01-01: invalid YAML front matter"},
  $self]}
JSON
cat > "$demo"/data/after.json <<JSON
{"schema": 1, "checkedAt": "$now", "fetched": false, "skipped": [], "problems": [],
 "checked": ["Claude Code 2.1.319", "3 plugins", "6 skills", "Homebrew", "uv tools"], "items": [$self]}
JSON
cp "$demo"/data/before.json "$demo"/cache/uptick/last.json
# The uv group and the scan are opt-in; --plugin-dir names the plugin uptick@inline.
echo '{"pluginConfigs": {"uptick@inline": {"options": {"kinds": "plugin,skill,brew,uv,vulns"}}}}' > "$demo"/config/settings.json
# Past the first-run screens: a theme, and this folder trusted.
cat > "$demo"/config/.claude.json <<JSON
{"hasCompletedOnboarding": true, "theme": "dark",
 "projects": {"$demo": {"hasTrustDialogAccepted": true}, "/private$demo": {"hasTrustDialogAccepted": true}}}
JSON
echo before > "$demo"/data/state

# The checker answers with the synthetic result, the shorter one once an update ran.
cat > "$demo"/bin/python3 <<'SH'
#!/bin/sh
sleep 1
cat "/tmp/uptick-demo/data/$(cat /tmp/uptick-demo/data/state).json"
SH
cat > "$demo"/bin/claude <<'SH'
#!/bin/sh
sleep 1; echo after > /tmp/uptick-demo/data/state
echo "Updating plugin \"$4\"..."; echo "Plugin \"$4\" updated from 1.4.0 to 1.5.0."
SH
cat > "$demo"/bin/npx <<'SH'
#!/bin/sh
sleep 1; echo after > /tmp/uptick-demo/data/state
echo "Updated 1 skill: $6"
SH
cat > "$demo"/bin/brew <<'SH'
#!/bin/sh
sleep 2; echo after > /tmp/uptick-demo/data/state
name=$3; [ "$3" = --cask ] && name=$4
echo "==> Upgrading $name"; echo "==> $name was upgraded"
SH
cat > "$demo"/bin/uv <<'SH'
#!/bin/sh
sleep 1; echo after > /tmp/uptick-demo/data/state
echo "Updated $3 v0.4.1 -> v0.5.0"
SH
chmod +x "$demo"/bin/*

# Recorded from inside a Claude Code session, its variables would load its plugins and add its notices.
for v in $(compgen -e | grep -E '^(CLAUDE|AI_AGENT)'); do unset "$v"; done
export HOME="$demo"/home CLAUDE_CONFIG_DIR="$demo"/config XDG_CACHE_HOME="$demo"/cache XDG_CONFIG_HOME="$demo"/home/.config
export PATH="$demo/bin:$PATH" DISABLE_TELEMETRY=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export PS1='$ '
# Only this shell reaches the real Claude Code; the mod's update commands find the fake ones on PATH.
eval "claude() { '$real_claude' \"\$@\"; }"
cd "$demo" || return
