#!/bin/sh
# Validate the public key against a challenge signed by the pinned Sparkle tool.
set -eu
cd "$(dirname "$0")/.."
sparkle=$(sh packaging/sparkle.sh)
challenge=$(mktemp "${RUNNER_TEMP:-/tmp}/gitdoc-sparkle-key.XXXXXX")
trap 'rm -f "$challenge"' 0
printf '%s\n' 'GitDoc Sparkle release key validation' > "$challenge"
signature=$("$sparkle/bin/sign_update" --ed-key-file - -p "$challenge" 2>/dev/null) || {
    echo 'Sparkle could not read the configured private key in its exported format.' >&2
    exit 1
}
swift packaging/sparkle-key.swift "$1" "$challenge" "$signature"
