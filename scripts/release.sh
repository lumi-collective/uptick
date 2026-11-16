#!/usr/bin/env bash
# Ship a release from the pushed tip of its release branch. main and the signed tag go to origin only when the
# newest `test` run of that branch on this very commit succeeded; then the tag gets its GitHub release, with this
# version's CHANGELOG.md section as the notes. Anything else sends nothing. A rerun is safe: what is already on
# origin is a no-op, and a release that failed is made then.
#   scripts/release.sh
set -euo pipefail

die() { echo "✗ $*" >&2; exit 1; }
cd "$(git rev-parse --show-toplevel)"

version="$(jq -r .version plugin/.claude-plugin/plugin.json)"
tag="v$version"
branch="$(git symbolic-ref --short HEAD)" || die "HEAD is detached: check out the release branch. Nothing sent."
[[ $branch != main ]] || die "run it on the release branch, not main. Nothing sent."
# The name goes into the jq filter below, so it may hold no quote or backslash.
[[ $branch =~ ^[A-Za-z0-9._/-]+$ ]] || die "the branch name '$branch' has characters the CI filter cannot take. Nothing sent."
head="$(git rev-parse HEAD)"
[[ "$(git ls-remote origin "refs/heads/$branch" | cut -f1)" == "$head" ]] ||
  die "origin/$branch is not HEAD ${head:0:7}: push the branch and wait for its test run. Nothing sent."

notes="$(awk -v h="## $version" '$0 == h { f = 1; next } f && /^## / { exit } f' CHANGELOG.md | sed -e '/./,$!d')" ||
  die "could not read CHANGELOG.md. Nothing sent."
[[ -n $notes ]] || die "CHANGELOG.md has no '## $version' section: write the release notes first. Nothing sent."

# A rerun from a checkout that lacks the tag uses origin's, so the push stays a no-op.
if ! git rev-parse -q --verify "refs/tags/$tag" > /dev/null && git ls-remote --exit-code -q origin "refs/tags/$tag" > /dev/null; then
  git fetch -q origin "refs/tags/$tag:refs/tags/$tag"
fi
if git rev-parse -q --verify "refs/tags/$tag" > /dev/null; then
  [[ "$(git rev-parse "$tag^{commit}")" == "$head" ]] ||
    die "$tag points at another commit than HEAD ${head:0:7}. If it is not on origin, remove it (git tag -d $tag). Nothing sent."
  # cat-file tag also fails on a lightweight tag, which is no tag object.
  if ! git cat-file tag "refs/tags/$tag" | grep -qE -- '-----BEGIN (PGP|SSH) SIGNATURE-----'; then
    die "$tag is not a signed tag. If it is not on origin, remove it (git tag -d $tag). Nothing sent."
  fi
fi

slug="$(git remote get-url origin | sed -nE 's#\.git$##; s#^.*[:/]([^/:]+/[^/:]+)$#\1#p')"
command -v gh > /dev/null || die "gh reads the CI result, and it is not installed. Nothing sent."
# The newest push run of this branch only. The runs that the push of main and of the tag start on the same commit
# do not count, so they cannot hold back a rerun; nor do pull_request runs, which test a merge ref, not HEAD, and
# which a fork can start.
ci="$(gh api "repos/$slug/actions/runs?head_sha=$head&per_page=100" \
  --jq "[.workflow_runs[] | select(.name == \"test\" and .event == \"push\" and .head_branch == \"$branch\")] | sort_by(.run_started_at) | last | .conclusion // \"none\"")" ||
  die "could not read the CI runs of $slug with gh. Nothing sent."
[[ $ci == success ]] || die "the test run of $branch on ${head:0:7} is '$ci', not success. Nothing sent."
echo "→ the test run of $branch on ${head:0:7} is green"

if ! git rev-parse -q --verify "refs/tags/$tag" > /dev/null; then
  TZ=UTC git tag -s -m "Uptick $version" "$tag" "$head" || die "could not sign $tag. Nothing sent."
fi
# The commit the gate checked, not a ref read again; fast-forward only; main and the tag both or neither.
git push --atomic origin "$head:refs/heads/main" "refs/tags/$tag:refs/tags/$tag"

if gh release view "$tag" -R "$slug" > /dev/null 2>&1; then
  echo "→ the release $tag exists"
else
  gh release create "$tag" -R "$slug" --verify-tag --title "Uptick $version" --notes "$notes" ||
    die "main and $tag are pushed, but the GitHub release failed: run it again to make it."
  echo "→ released $tag"
fi
