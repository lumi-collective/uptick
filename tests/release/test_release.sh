#!/usr/bin/env bash
# Tests for scripts/release.sh, in a throwaway repo with a local bare origin, a stand-in gh and a stand-in gpg.
# Nothing here reaches GitHub or a real key.
#   bash tests/release/test_release.sh
set -uo pipefail

src="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
release="$src/scripts/release.sh"
tmp="$(mktemp -d)"
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL="$tmp/gitconfig"
mkdir -p "$tmp/bin"

# A stand-in gpg: git only needs a SIG_CREATED status line and something on stdout. Each signature differs (its
# pid), so a tag signed again is a new object, as with a real key.
cat > "$tmp/bin/gpg" <<'SH'
#!/bin/sh
cat > /dev/null
printf '\n[GNUPG:] SIG_CREATED D 1 8 00 0 0\n' >&2
printf -- '-----BEGIN PGP SIGNATURE-----\nfake %s\n-----END PGP SIGNATURE-----\n' "$$"
SH
cat > "$tmp/gitconfig" <<EOF
[user]
	name = Test
	email = test@example.invalid
[init]
	defaultBranch = main
[gpg]
	program = $tmp/bin/gpg
EOF

# A stand-in gh. It answers only `gh api … --jq <filter>` and `gh release view|create`; every other call fails.
# `api` runs the caller's real filter with jq over $FAKE_GH_BODY (an API-shaped file), or exits $FAKE_GH_EXIT.
# `release create` fails when the tag is not on origin (as --verify-tag does) or when $FAKE_GH_CREATE_FAIL is set.
cat > "$tmp/bin/gh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_GH_LOG"
case "${1-} ${2-}" in
  api\ *)
    [[ -z ${FAKE_GH_EXIT-} ]] || exit "$FAKE_GH_EXIT"
    filter=""
    while (($#)); do [[ $1 == --jq ]] && filter=$2; shift; done
    jq -r "$filter" "$FAKE_GH_BODY" ;;
  "release view") [[ -f $FAKE_GH_RELEASED ]] ;;
  "release create")
    [[ -z ${FAKE_GH_CREATE_FAIL-} ]] || exit 1
    git --git-dir="$FAKE_GH_ORIGIN" rev-parse -q --verify "refs/tags/$3" > /dev/null || exit 1
    while (($#)); do [[ $1 == --notes ]] && printf '%s' "$2" > "$FAKE_GH_RELEASED"; shift; done ;;
  *) exit 1 ;;
esac
SH
chmod +x "$tmp/bin/gpg" "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH"

fails=0
n=0
pass() { echo "ok   $1"; }
flunk() { echo "FAIL $1 (log: $box/log)"; fails=$((fails + 1)); }

# A fresh box: origin (bare) with main at the old release, and work on release/9.9.9, pushed, with the new release.
new_box() {
  box="$tmp/box$((++n))"
  origin="$box/origin.git"
  work="$box/work"
  git init -q --bare "$origin"
  git init -q "$work"
  mkdir -p "$work/plugin/.claude-plugin"
  printf '{"name": "uptick", "version": "9.9.8"}\n' > "$work/plugin/.claude-plugin/plugin.json"
  printf '# Changelog\n\n## 9.9.8\n\n- Old.\n' > "$work/CHANGELOG.md"
  w add -A && w commit -q -m old && w remote add origin "$origin" && w push -q origin main
  w switch -q -c release/9.9.9
  printf '{"name": "uptick", "version": "9.9.9"}\n' > "$work/plugin/.claude-plugin/plugin.json"
  printf '# Changelog\n\n## 9.9.9\n\n- New one.\n- New two.\n\n## 9.9.8\n\n- Old.\n' > "$work/CHANGELOG.md"
  w commit -q -am new && w push -q origin release/9.9.9
  old="$(git --git-dir="$origin" rev-parse main)"
  head="$(w rev-parse HEAD)"
  export FAKE_GH_LOG="$box/ghlog" FAKE_GH_RELEASED="$box/released" FAKE_GH_ORIGIN="$origin" FAKE_GH_BODY="$box/runs.json"
  unset FAKE_GH_EXIT FAKE_GH_CREATE_FAIL
  : > "$FAKE_GH_LOG"
  runs "$(run release/9.9.9 2026-01-01 success)"
}
w() { git -C "$work" "$@"; }
o() { git --git-dir="$origin" "$@"; }
# One run: branch, start day, conclusion (null: unfinished), and optionally the event (push) and workflow (test).
run() { printf '{"name":"%s","event":"%s","head_branch":"%s","run_started_at":"%sT00:00:00Z","conclusion":%s}' \
  "${5:-test}" "${4:-push}" "$1" "$2" "$([[ $3 == null ]] && echo null || echo "\"$3\"")"; }
runs() { local IFS=,; printf '{"workflow_runs":[%s]}' "$*" > "$FAKE_GH_BODY"; }
ship() { (cd "$work" && bash "$release") > "$box/log" 2>&1; }
# Nothing reached origin: main is where it was, and there is no tag and no release.
untouched() {
  [[ "$(o rev-parse main)" == "$old" ]] && ! o rev-parse -q --verify refs/tags/v9.9.9 > /dev/null && [[ ! -f $FAKE_GH_RELEASED ]]
}
refuses() { # name, expected message
  if ! ship && grep -q -- "$2" "$box/log" && untouched; then pass "$1"; else flunk "$1"; fi
}

new_box; runs "$(run release/9.9.9 2026-01-01 failure)" "$(run release/9.9.9 2026-01-02 success push lint)"
refuses "a red run sends nothing, and another workflow's green run does not count" "'failure', not success"
new_box; runs "$(run release/9.9.9 2026-01-01 success)" "$(run release/9.9.9 2026-01-02 failure)"
refuses "the newest run by start time decides, in any listed order" "'failure', not success"
new_box; runs "$(run release/9.9.9 2026-01-02 success pull_request)" "$(run release/9.9.9 2026-01-01 failure)"
refuses "a green pull_request run does not count" "'failure', not success"
new_box; runs "$(run release/9.9.9 2026-01-02 null)" "$(run release/9.9.9 2026-01-01 success)"
refuses "an unfinished newest run sends nothing" "'none', not success"
new_box; runs
refuses "no run sends nothing" "'none', not success"
new_box; runs "$(run main 2026-01-02 success)" "$(run v9.9.9 2026-01-02 success)" "$(run release/9.9.9 2026-01-01 failure)"
refuses "green runs of main and of the tag do not count" "'failure', not success"
new_box; export FAKE_GH_EXIT=1
refuses "a failing gh sends nothing" "could not read the CI runs"

new_box; printf '# Changelog\n\n## 9.9.8\n\n- Old.\n' > "$work/CHANGELOG.md"; w commit -q -am "no notes"; w push -q origin release/9.9.9
refuses "no changelog section is refused" "no '## 9.9.9' section"
new_box; w commit -q --allow-empty -m "not pushed"
refuses "a HEAD that is not on origin is refused" "is not HEAD"
new_box; w switch -q main
refuses "main is refused" "not main"
new_box; w tag -a -m elsewhere v9.9.9 main
refuses "a tag on another commit is refused" "points at"
new_box; w tag v9.9.9
refuses "a lightweight tag on HEAD is refused" "not a signed tag"
new_box; w tag -a -m unsigned v9.9.9
refuses "an unsigned tag on HEAD is refused" "not a signed tag"
new_box; git clone -q "$origin" "$box/other" && git -C "$box/other" commit -q --allow-empty -m foreign \
  && git -C "$box/other" push -q origin main && old="$(o rev-parse main)"
refuses "a main that is not a fast-forward sends nothing, not even the tag" "rejected"
new_box; w switch -q -c 'bad"name' && w push -q origin 'bad"name'
refuses "a branch name jq could misread is refused" "branch name"

new_box; runs "$(run main 2026-01-03 failure)" "$(run v9.9.9 2026-01-03 failure)" \
  "$(run release/9.9.9 2026-01-02 success)" "$(run release/9.9.9 2026-01-01 failure)"
if ship && [[ "$(o rev-parse main)" == "$head" ]] && [[ "$(o rev-parse 'refs/tags/v9.9.9^{commit}')" == "$head" ]] \
  && o cat-file tag v9.9.9 | grep -q 'BEGIN PGP SIGNATURE' && grep -q "head_sha=$head" "$FAKE_GH_LOG" \
  && [[ "$(cat "$FAKE_GH_RELEASED")" == $'- New one.\n- New two.' ]] && grep -q -- '--title Uptick 9.9.9' "$FAKE_GH_LOG"; then
  pass "a green run of the release branch ships main, a signed tag and the release with this version's notes"
else
  flunk "a green run ships"
fi
if ship && grep -q "release v9.9.9 exists" "$box/log" && [[ "$(grep -c 'release create' "$FAKE_GH_LOG")" == 1 ]]; then
  pass "a second run sends nothing new"
else
  flunk "a second run"
fi

new_box; export FAKE_GH_CREATE_FAIL=1
if ! ship && grep -q "release failed: run it again" "$box/log" && [[ "$(o rev-parse main)" == "$head" ]]; then
  unset FAKE_GH_CREATE_FAIL
  # From a checkout without the local tag: the script takes origin's, so the push stays a no-op.
  w tag -d v9.9.9 > /dev/null
  if ship && [[ -f $FAKE_GH_RELEASED ]]; then
    pass "a failed release is made by a rerun, also from a checkout without the tag"
  else
    flunk "rerun after a failed release"
  fi
else
  flunk "a failed release"
fi

echo "$fails failed"
[[ $fails -eq 0 ]]
