#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
cargo_bin=$(command -v cargo || true)
if [ -z "$cargo_bin" ]; then cargo_bin="$HOME/.cargo/bin/cargo"; fi
project="desktop/native"
target="${CARGO_TARGET_DIR:-$project/target}"
"$cargo_bin" build --locked --manifest-path "$project/Cargo.toml"
bundle=".build/GitDoc.app"
mkdir -p "$bundle/Contents/MacOS"
cp desktop/Info.plist "$bundle/Contents/Info.plist"
cp "$target/debug/gitdoc-desktop" "$bundle/Contents/MacOS/gitdoc-desktop"
swiftc -target "$(uname -m)-apple-macosx12.0" -parse-as-library -swift-version 5 -O -framework AppKit desktop/GitDoc.swift -o "$bundle/Contents/MacOS/GitDoc"
codesign --force --sign - "$bundle"
