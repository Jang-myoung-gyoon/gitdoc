#!/bin/sh
# Return the path to the pinned upstream Sparkle distribution.
set -eu
cd "$(dirname "$0")/.."
version=2.10.1
hash=1a856d2a9edb8e628de88121d7be04cf0e2167f8641650b78fca00b14244cf27
archive="${GITDOC_SPARKLE_ARCHIVE:-dist/cache/Sparkle-$version.tar.xz}"
folder="dist/cache/sparkle-$version"
mkdir -p dist/cache
[ -f "$archive" ] || curl -q --fail --location --silent --show-error --proto '=https' \
    --retry 3 --output "$archive" "https://github.com/sparkle-project/Sparkle/releases/download/$version/Sparkle-$version.tar.xz"
printf '%s  %s\n' "$hash" "$archive" | shasum -a 256 --check --status
if [ ! -f "$folder/bin/generate_appcast" ]; then
    stage=$(mktemp -d dist/cache/.sparkle.XXXXXX)
    trap 'rm -rf "$stage"' 0
    tar -xJf "$archive" -C "$stage" ./Sparkle.framework ./bin ./LICENSE
    mv "$stage" "$folder"
    trap - 0
fi
printf '%s\n' "$PWD/$folder"
