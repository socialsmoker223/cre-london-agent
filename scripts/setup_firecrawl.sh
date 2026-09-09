#!/usr/bin/env sh
set -eu

version='v2.11.162'
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
target="$root/.runtime/firecrawl"

commit='7666c1f9ae8720a6bba271e0f60b6a217f8a5210'
if [ -d "$target" ]; then
  test "$(git -C "$target" rev-parse HEAD)" = "$commit" || {
    printf '%s\n' "Existing Firecrawl checkout differs from the required commit; inspect $target."
    exit 1
  }
else
  mkdir -p "$root/.runtime"
  git clone --depth 1 --branch "$version" https://github.com/mendableai/firecrawl.git "$target"
  test "$(git -C "$target" rev-parse HEAD)" = "$commit"
fi
printf '%s\n' "Firecrawl $version ($commit) at $target"
