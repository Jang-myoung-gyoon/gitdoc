#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
cargo_bin=$(command -v cargo || true)
if [ -z "$cargo_bin" ]; then cargo_bin="$HOME/.cargo/bin/cargo"; fi
project="desktop/native"
target="${CARGO_TARGET_DIR:-$project/target}"
"$cargo_bin" build --locked --manifest-path "$project/Cargo.toml"
bundle=".build/GitDoc.app"
mkdir -p .build
stage=$(mktemp -d .build/.gitdoc.XXXXXX)
staged="$stage/GitDoc.app"
previous="$stage/previous.app"
cleanup() {
    if [ -d "$previous" ] && [ ! -d "$bundle" ]; then
        mv "$previous" "$bundle" || return
    fi
    rm -rf "$stage"
}
trap cleanup 0
trap 'exit 1' HUP INT TERM

mkdir -p "$staged/Contents/MacOS"
cp desktop/Info.plist "$staged/Contents/Info.plist"
cp "$target/debug/gitdoc-desktop" "$staged/Contents/MacOS/gitdoc-desktop"
cp "$target/debug/gitdoc-pdf" "$staged/Contents/MacOS/gitdoc-pdf"
swiftc -target "$(uname -m)-apple-macosx12.0" -parse-as-library -swift-version 5 -O -framework AppKit desktop/GitDoc.swift -o "$staged/Contents/MacOS/GitDoc"
swiftc -target "$(uname -m)-apple-macosx12.0" -parse-as-library -swift-version 5 -O -framework Vision desktop/OCR.swift -o "$staged/Contents/MacOS/gitdoc-ocr"
codesign --force --sign - "$staged/Contents/MacOS/gitdoc-ocr"
codesign --force --sign - "$staged/Contents/MacOS/gitdoc-pdf"
codesign --force --sign - "$staged"
codesign --verify --deep "$staged"

# Replace the bundle without overwriting executables a running app may still map.
if [ -d "$bundle" ]; then
    mv "$bundle" "$previous"
fi
mv "$staged" "$bundle"
