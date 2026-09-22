#!/bin/sh
# Publish a sanitized copy of this repository to the public GitHub remote.
#
# The internal history contains deployment hostnames, so it cannot be pushed to a
# public remote as-is. This produces a filtered copy — same commits, internal
# identifiers replaced — and force-pushes it. The internal history is never touched.
#
# Requires git-filter-repo (single file, no deps):
#   curl -sfLO https://raw.githubusercontent.com/newren/git-filter-repo/main/git-filter-repo
#
# Usage: sh tools/publish-public.sh /path/to/git-filter-repo
set -eu
FR="${1:?path to git-filter-repo required}"
SRC=$(git rev-parse --show-toplevel)
BR=$(git rev-parse --abbrev-ref HEAD)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

git clone -q --no-local --single-branch --branch "$BR" "$SRC" "$TMP/clean"
cd "$TMP/clean"
python3 "$FR" \
  --replace-text "$SRC/tools/public-sanitize-replacements.txt" \
  --path ansible-webui-prompt.md --path foreman.md --invert-paths \
  --force

# Gate: refuse to publish if anything internal survived the filter.
if git grep -qI "example" -- . 2>/dev/null || [ "$(git log --all --oneline -S'example' | wc -l)" -ne 0 ]; then
  echo "ABORT: internal references still present after filtering" >&2
  exit 1
fi

git remote add github https://github.com/imoes/CentralStation.git

# Gate 2: the filter only rewrites the branch we push. Anything else that exists on
# the public remote was pushed some other way and was never filtered — which is
# exactly how internal hostnames and a service password ended up public once. The
# first gate cannot see this: it only inspects our own clean clone.
OTHER=$(git ls-remote --heads github 2>/dev/null \
        | awk '{print $2}' | sed 's|refs/heads/||' | grep -v '^main$' || true)
if [ -n "$OTHER" ]; then
  echo "ABORT: the public remote carries branches this script never filtered:" >&2
  echo "$OTHER" | sed 's/^/  /' >&2
  echo "Check them for internal references, mirror anything worth keeping to the" >&2
  echo "internal remote, then delete them there before publishing again." >&2
  exit 1
fi

git push --force github "$BR:main"
echo "published $(git rev-parse --short HEAD) -> github main"

# Not a gate, a reminder: pull-request refs (refs/pull/N/head) keep their commits
# reachable even after the branch is gone, and they cannot be deleted with git.
# Anything that was ever public through a PR stays public until GitHub support
# removes it or the repository is recreated — a leaked credential must be rotated,
# not merely deleted.
PRS=$(git ls-remote github 'refs/pull/*' 2>/dev/null | wc -l)
[ "$PRS" -gt 0 ] && echo "note: $PRS pull-request refs remain reachable on the public remote" >&2
exit 0
