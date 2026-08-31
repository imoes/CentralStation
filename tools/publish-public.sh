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
git push --force github "$BR:main"
echo "published $(git rev-parse --short HEAD) -> github main"
