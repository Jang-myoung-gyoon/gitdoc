#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
bundle=".build/GitDoc.app"
mkdir -p "$bundle/Contents/MacOS"
cp desktop/Info.plist "$bundle/Contents/Info.plist"
swiftc -target "$(uname -m)-apple-macosx12.0" -parse-as-library -swift-version 5 -O -framework AppKit -framework WebKit desktop/GitDoc.swift -o "$bundle/Contents/MacOS/GitDoc"
codesign --force --sign - "$bundle"
