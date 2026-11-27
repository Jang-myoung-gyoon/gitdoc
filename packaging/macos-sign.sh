#!/bin/sh
# Sign the assembled app and DMG using the release runner's temporary keychain.
set -eu
: "${RUNNER_TEMP:?}"
: "${MACOS_CERTIFICATE_P12_BASE64:?missing certificate secret}"
: "${MACOS_CERTIFICATE_PASSWORD:?missing certificate password secret}"
: "${APPLE_TEAM_ID:?missing Apple team ID secret}"

original_umask=$(umask)
umask 077
scratch=$(mktemp -d "$RUNNER_TEMP/gitdoc-sign.XXXXXX")
keychain="$scratch/release.keychain-db"
keychain_created=0
cleanup() {
    status=$?
    trap - 0
    if [ "$keychain_created" = 1 ]; then
        security delete-keychain "$keychain" >/dev/null 2>&1 || {
            echo 'Failed to delete the temporary signing keychain' >&2
            status=1
        }
    fi
    rm -rf "$scratch" || status=1
    exit "$status"
}
trap cleanup 0
trap 'exit 1' HUP INT TERM

certificate="$scratch/release.p12"
printf '%s' "$MACOS_CERTIFICATE_P12_BASE64" | base64 -D > "$certificate"
unset MACOS_CERTIFICATE_P12_BASE64
keychain_password=$(openssl rand -hex 24)
security create-keychain -p "$keychain_password" "$keychain"
keychain_created=1
security set-keychain-settings -lut 3600 "$keychain"
security unlock-keychain -p "$keychain_password" "$keychain"
if ! security import "$certificate" -f pkcs12 -k "$keychain" -P "$MACOS_CERTIFICATE_PASSWORD" \
    -T /usr/bin/codesign >/dev/null 2>&1; then
    echo 'Cannot import the release PKCS#12 certificate and private key' >&2
    exit 1
fi
rm "$certificate"
unset MACOS_CERTIFICATE_PASSWORD
security set-key-partition-list -S apple-tool:,apple: -s -k "$keychain_password" "$keychain" >/dev/null
unset keychain_password

identities=$(security find-identity -v -p codesigning "$keychain")
identity=$(printf '%s\n' "$identities" | awk -v team="($APPLE_TEAM_ID)\"" '
    /"Developer ID Application: / && index($0, team) && length($2) == 40 && $2 ~ /^[0-9A-F]+$/ {
        match_id = $2
        count++
    }
    END { if (count == 1) print match_id }
')
if [ -z "$identity" ]; then
    echo 'Expected one valid Developer ID Application identity for the configured Apple team' >&2
    exit 1
fi
unset APPLE_TEAM_ID
umask "$original_umask"
cd "$(dirname "$0")/.."
CODESIGN_IDENTITY="$identity" CODESIGN_KEYCHAIN="$keychain" sh packaging/macos.sh --package-only
