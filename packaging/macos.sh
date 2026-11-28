#!/bin/sh
# Build the self-contained GitDoc.app and its disk image for Apple Silicon.
#   GITDOC_NATIVE_DIR      folder with prebuilt gitdoc-desktop and gitdoc-pdf (skips Cargo)
#   GITDOC_PYTHON_ARCHIVE  local copy of the pinned Python archive (skips the download)
# Use --assemble-only and --package-only to separate dependency installation from signing.
set -eu
cd "$(dirname "$0")/.."

python_archive="cpython-3.12.16+20261124-aarch64-apple-darwin-install_only.tar.gz"
python_url="https://github.com/astral-sh/python-build-standalone/releases/download/20261124/cpython-3.12.16%2B20261124-aarch64-apple-darwin-install_only.tar.gz"
python_sha256="431b99a9c2cdd37ad9b06e425be0e520d2dbce0552abe454257abef592fe3634"

project="desktop/native"
version=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$project/Cargo.toml" | head -n 1)
[ -n "$version" ] || { echo "version not found in $project/Cargo.toml" >&2; exit 1; }
out="dist/macos"
bundle="$out/GitDoc.app"
image="dist/GitDoc-$version-macos-arm64.dmg"

assemble() {
    native="${GITDOC_NATIVE_DIR:-}"
    if [ -z "$native" ]; then
        cargo_bin=$(command -v cargo || true)
        if [ -z "$cargo_bin" ]; then cargo_bin="$HOME/.cargo/bin/cargo"; fi
        "$cargo_bin" build --release --locked --target aarch64-apple-darwin --manifest-path "$project/Cargo.toml"
        native="${CARGO_TARGET_DIR:-$project/target}/aarch64-apple-darwin/release"
    fi

    rm -rf "$out" "$image"
    mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources/app/desktop" dist/cache

    cp desktop/Info.plist "$bundle/Contents/Info.plist"
    cp desktop/icons/GitDoc.icns "$bundle/Contents/Resources/"
    plist() { /usr/libexec/PlistBuddy -c "$1" "$bundle/Contents/Info.plist"; }
    plist "Set :CFBundleShortVersionString $version"
    plist "Set :CFBundleVersion $version"
    # The bundled PDFium wheel needs macOS 13.
    plist "Set :LSMinimumSystemVersion 13.0"

    sparkle=$(sh packaging/sparkle.sh)
    mkdir -p "$bundle/Contents/Frameworks"
    ditto "$sparkle/Sparkle.framework" "$bundle/Contents/Frameworks/Sparkle.framework"
    cp "$sparkle/LICENSE" "$bundle/Contents/Resources/LICENSE-Sparkle"
    if [ "${GITDOC_DISTRIBUTION:-false}" = true ]; then
        : "${GITDOC_SPARKLE_PUBLIC_KEY:?configure the GitDoc Sparkle public key}"
        sparkle_public=$(printf '%s' "$GITDOC_SPARKLE_PUBLIC_KEY" | /usr/bin/python3 -c '
import base64, sys
key = base64.b64decode(sys.stdin.read().strip(), validate=True)
assert len(key) == 32
print(base64.b64encode(key).decode("ascii"))
')
        plist "Add :SUPublicEDKey string $sparkle_public"
        plist 'Add :SUFeedURL string https://github.com/Jang-myoung-gyoon/gitdoc/releases/latest/download/gitdoc-macos-arm64-appcast.xml'
        plist 'Add :SUEnableAutomaticChecks bool false'
        plist 'Add :SUAllowsAutomaticUpdates bool false'
        plist 'Add :SUAutomaticallyUpdate bool false'
        plist 'Add :SUVerifyUpdateBeforeExtraction bool true'
        plist 'Add :SURequireSignedFeed bool true'
        plist 'Add :SUSignedFeedFailureExpirationInterval integer 0'
    fi

    cp "$native/gitdoc-desktop" "$native/gitdoc-pdf" "$bundle/Contents/MacOS/"
    swiftc -target arm64-apple-macosx13.0 -parse-as-library -swift-version 5 -O -framework AppKit \
        -F "$sparkle" -framework Sparkle -Xlinker -rpath -Xlinker @executable_path/../Frameworks \
        desktop/GitDoc.swift -o "$bundle/Contents/MacOS/GitDoc"
    swiftc -target arm64-apple-macosx12.0 -parse-as-library -swift-version 5 -O -framework Vision desktop/OCR.swift -o "$bundle/Contents/MacOS/gitdoc-ocr"

    app="$bundle/Contents/Resources/app"
    cp ./*.py LICENSE "$app/"
    cp desktop/*.py "$app/desktop/"
    cp -R templates static "$app/"
    echo "$version" > "$app/PACKAGED"

    archive="${GITDOC_PYTHON_ARCHIVE:-dist/cache/$python_archive}"
    [ -f "$archive" ] || curl --fail --location --silent --show-error --output "$archive" "$python_url"
    echo "$python_sha256  $archive" | shasum -a 256 --check --status || { echo "checksum mismatch: $archive" >&2; exit 1; }
    tar -xzf "$archive" -C "$bundle/Contents/Resources"
    runtime="$bundle/Contents/Resources/python"
    python="$runtime/bin/python3"
    "$python" -m pip install --disable-pip-version-check --no-cache-dir --no-warn-script-location \
        -r requirements.txt -r requirements-desktop.txt -r requirements-mcp.txt

    # Drop what the engine never loads; installed entry scripts point at the build folder.
    library="$runtime/lib/python3.12"
    rm -rf "$library/test" "$library/idlelib" "$library/tkinter" "$library/turtledemo" "$library/ensurepip" \
        "$library"/site-packages/pip "$library"/site-packages/pip-*.dist-info "$library"/lib-dynload/_tkinter* "$runtime"/lib/libtcl* "$runtime"/lib/tcl* "$runtime"/lib/tk* \
        "$runtime/lib/pkgconfig" "$runtime/include" "$runtime/share"
    find "$runtime/bin" ! -type d ! -name python ! -name python3 ! -name python3.12 -delete
    find "$runtime" "$app" -name __pycache__ -type d -prune -exec rm -rf {} +
    # Hash-based bytecode stays valid after copying, so nothing is written into the installed app.
    "$python" -m compileall -q --invalidation-mode unchecked-hash "$app" "$library" >/dev/null || true
}

sign() {
    target=$1
    shift
    if [ -n "${CODESIGN_KEYCHAIN:-}" ]; then
        if ! codesign --force --sign "$CODESIGN_IDENTITY" --keychain "$CODESIGN_KEYCHAIN" --timestamp "$@" "$target"; then
            printf 'Developer ID signing failed for %s\n' "$target" >&2
            exit 1
        fi
    else
        codesign --force --sign - "$@" "$target"
    fi
}

sign_runtime_code() {
    if [ -n "${CODESIGN_KEYCHAIN:-}" ]; then
        sign "$1" --options runtime
    else
        sign "$1"
    fi
}

sign_bundle() {
    # Sign leaves before their containing bundles, including code shipped as runtime resources.
    find "$bundle/Contents/Resources/python" "$bundle/Contents/MacOS" "$bundle/Contents/Frameworks" -depth -print > "$stage/code-paths"
    while IFS= read -r code; do
        [ ! -L "$code" ] || continue
        if [ -f "$code" ]; then
            # Signing GitDoc.app below also signs its main executable.
            [ "$code" != "$bundle/Contents/MacOS/GitDoc" ] || continue
            kind=$(file -b "$code")
            case "$kind" in
                Mach-O*) sign_runtime_code "$code" ;;
            esac
        elif [ -d "$code" ]; then
            case "$code" in
                *.framework)
                    if [ -d "$code/Versions" ]; then
                        for framework_version in "$code"/Versions/*; do
                            [ -d "$framework_version" ] && [ ! -L "$framework_version" ] || continue
                            sign "$code" --bundle-version "${framework_version##*/}"
                        done
                    else
                        sign "$code"
                    fi ;;
                *.app|*.xpc|*.appex) sign_runtime_code "$code" ;;
                *.bundle)
                    if [ -d "$code/Contents/MacOS" ]; then sign "$code"; fi ;;
            esac
        fi
    done < "$stage/code-paths"
    sign_runtime_code "$bundle"
    codesign --verify --deep --strict "$bundle"
}

package() {
    if [ -n "${CODESIGN_KEYCHAIN:-}${CODESIGN_IDENTITY:-}" ]; then
        : "${CODESIGN_KEYCHAIN:?a distribution identity requires a keychain}"
        : "${CODESIGN_IDENTITY:?a distribution keychain requires an identity}"
        [ "$CODESIGN_IDENTITY" != "-" ] || { echo 'Ad-hoc identity is invalid for distribution' >&2; exit 1; }
    fi

    stage=$(mktemp -d dist/.dmg.XXXXXX)
    trap 'rm -rf "$stage"' 0
    sign_bundle
    mkdir "$stage/image"
    ditto "$bundle" "$stage/image/GitDoc.app"
    ln -s /Applications "$stage/image/Applications"
    # Hosted runners sometimes report the staging folder as busy.
    attempt=1
    until hdiutil create -quiet -volname GitDoc -srcfolder "$stage/image" -format UDZO -ov "$image"; do
        [ "$attempt" -lt 3 ] || exit 1
        attempt=$((attempt + 1))
        sleep 5
    done
    if [ -n "${CODESIGN_KEYCHAIN:-}" ]; then
        sign "$image" --identifier local.gitdoc.desktop.dmg
        codesign --verify --strict "$image"
    fi
    echo "$image"
}

case "${1:-}" in
    --assemble-only) assemble ;;
    --package-only) package ;;
    '') assemble; package ;;
    *) echo "usage: $0 [--assemble-only|--package-only]" >&2; exit 2 ;;
esac
