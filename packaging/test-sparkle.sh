#!/bin/sh
# Test Sparkle EdDSA signatures without the login keychain or release credentials.
set -eu
cd "$(dirname "$0")/.."
sparkle=$(sh packaging/sparkle.sh)
key_source="$PWD/packaging/sparkle-key.swift"
umask 077
stage=$(mktemp -d "${RUNNER_TEMP:-/tmp}/gitdoc-sparkle-test.XXXXXX")
trap 'rm -rf "$stage"' 0
cat > "$stage/key.swift" <<'SWIFT'
import CryptoKit
import Foundation
let key = Curve25519.Signing.PrivateKey()
try key.rawRepresentation.base64EncodedString().write(toFile: CommandLine.arguments[1], atomically: true, encoding: .utf8)
print(key.publicKey.rawRepresentation.base64EncodedString())
SWIFT
public=$(swift "$stage/key.swift" "$stage/seed")
other=$(swift "$stage/key.swift" "$stage/wrong-seed")
swift "$key_source" "$public" < "$stage/seed"
if swift "$key_source" "$other" < "$stage/seed"; then
    echo 'accepted a mismatched Sparkle key' >&2
    exit 1
fi

bundle="$stage/GitDoc.app"
mkdir -p "$bundle/Contents/MacOS" "$stage/archives"
cp desktop/Info.plist "$bundle/Contents/Info.plist"
# Real Mach-O lets Sparkle infer CPU compatibility in the fixture.
cp /usr/bin/true "$bundle/Contents/MacOS/GitDoc"
plist() { /usr/libexec/PlistBuddy -c "$1" "$bundle/Contents/Info.plist"; }
plist 'Set :CFBundleShortVersionString 2.0.0'
plist 'Set :CFBundleVersion 2.0.0'
plist "Add :SUPublicEDKey string $public"
plist 'Add :SUVerifyUpdateBeforeExtraction bool true'
plist 'Add :SURequireSignedFeed bool true'
plist 'Add :SUSignedFeedFailureExpirationInterval integer 0'
codesign --force --sign - "$bundle"
archive="$stage/archives/GitDoc-2.0.0.zip"
ditto -c -k --keepParent "$bundle" "$archive"
feed="$stage/archives/gitdoc-macos-arm64-appcast.xml"
"$sparkle/bin/generate_appcast" --ed-key-file "$stage/seed" --maximum-deltas 0 \
    --download-url-prefix https://example.com/updates/ -o "$feed" "$stage/archives"
"$sparkle/bin/sign_update" --ed-key-file "$stage/seed" --verify "$feed"
signature=$(python3 - "$feed" <<'PY'
import sys
from xml.etree import ElementTree
root = ElementTree.parse(sys.argv[1])
print(root.find(".//enclosure").get("{http://www.andymatuschak.org/xml-namespaces/sparkle}edSignature"))
PY
)
"$sparkle/bin/sign_update" --ed-key-file "$stage/seed" --verify "$archive" "$signature"
printf 'tampered' >> "$archive"
if "$sparkle/bin/sign_update" --ed-key-file "$stage/seed" --verify "$archive" "$signature"; then
    echo 'accepted a tampered Sparkle archive' >&2
    exit 1
fi
python3 - "$feed" <<'PYTHON'
import sys
from pathlib import Path
feed = Path(sys.argv[1])
original = feed.read_bytes()
changed = original.replace(b"https://example.com/updates/", b"https://example.org/updates/", 1)
assert changed != original
feed.write_bytes(changed)
PYTHON
if "$sparkle/bin/sign_update" --ed-key-file "$stage/seed" --verify "$feed"; then
    echo 'accepted a tampered Sparkle feed' >&2
    exit 1
fi
