#!/bin/sh
# Generate the signed feed only after the Developer ID DMG has been notarized and stapled.
set -eu
: "${GITDOC_SPARKLE_PRIVATE_SEED:?missing GitDoc Sparkle signing seed}"
seed=$GITDOC_SPARKLE_PRIVATE_SEED
unset GITDOC_SPARKLE_PRIVATE_SEED
cd "$(dirname "$0")/.."
version=$(sed -n 's/^version = "\(.*\)"$/\1/p' desktop/native/Cargo.toml | head -n 1)
case "$version" in *[!0-9.]*|'') exit 1 ;; esac
image="GitDoc-$version-macos-arm64.dmg"
codesign --verify --strict -R='anchor apple generic and certificate leaf[field.1.2.840.113635.100.6.1.13] exists' "dist/$image"
xcrun stapler validate "dist/$image"
public=$(/usr/libexec/PlistBuddy -c 'Print :SUPublicEDKey' dist/macos/GitDoc.app/Contents/Info.plist)
printf '%s\n' "$seed" | sh packaging/sparkle-key-check.sh "$public"
sparkle=$(sh packaging/sparkle.sh)
stage=$(mktemp -d "$PWD/dist/.sparkle-appcast.XXXXXX")
trap 'rm -rf "$stage"' 0
ln "dist/$image" "$stage/$image"
repository=https://github.com/Jang-myoung-gyoon/gitdoc
notes="$stage/GitDoc-$version-macos-arm64.html"
previous=$(git describe --tags --abbrev=0 HEAD^ 2>/dev/null || true)
if [ -n "$previous" ]; then
    git log --format=%s "$previous..HEAD"
else
    git log -1 --format=%s
fi | /usr/bin/python3 -c 'import html,sys
print("<ul>" + "".join("<li>" + html.escape(line.strip()) + "</li>" for line in sys.stdin if line.strip()) + "</ul>")' > "$notes"
printf '%s\n' "$seed" | "$sparkle/bin/generate_appcast" --ed-key-file - --maximum-deltas 0 \
    --download-url-prefix "$repository/releases/download/v$version/" \
    --full-release-notes-url "$repository/releases/tag/v$version" \
    --embed-release-notes -o "$stage/gitdoc-macos-arm64-appcast.xml" "$stage"
printf '%s\n' "$seed" | "$sparkle/bin/sign_update" --ed-key-file - --verify "$stage/gitdoc-macos-arm64-appcast.xml"
unset seed
mv "$stage/gitdoc-macos-arm64-appcast.xml" dist/
