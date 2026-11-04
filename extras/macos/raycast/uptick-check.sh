#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Uptick check
# @raycast.mode fullOutput

# Optional parameters:
# @raycast.packageName Uptick
# @raycast.description What has an update: Claude Code, plugins, skills, Homebrew and uv tools. Read-only, no fetch.

# Linked into the Raycast script folder, this finds the clone through the link.
here=$(dirname "$(readlink -f "$0")")
exec "$here/../../../plugin/bin/uptick-check" --no-fetch --no-notify
