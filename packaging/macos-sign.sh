#!/bin/sh
# Sign the assembled app and DMG using the release runner's temporary keychain.
set -eu
: "${RUNNER_TEMP:?}"
: "${MACOS_CERTIFICATE_P12_BASE64:?missing certificate secret}"
: "${MACOS_CERTIFICATE_PASSWORD:?missing certificate password secret}"
: "${APPLE_TEAM_ID:?missing Apple team ID secret}"

team_id=$APPLE_TEAM_ID
unset APPLE_TEAM_ID
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
# Preserve the runner's other keychains. Deleting the temporary keychain also removes it from this list.
runner_keychains=$(security list-keychains -d user)
set -- "$keychain"
while IFS= read -r entry; do
    case "$entry" in
        *\"*\") entry=${entry#*\"}; set -- "$@" "${entry%\"}" ;;
        '') ;;
        *) echo 'Cannot read the runner keychain search list' >&2; exit 1 ;;
    esac
done <<EOF
$runner_keychains
EOF

keychain_password=$(openssl rand -hex 24)
security create-keychain -p "$keychain_password" "$keychain"
keychain_created=1
security set-keychain-settings -lut 3600 "$keychain"
security unlock-keychain -p "$keychain_password" "$keychain"
# codesign uses the user's search list for the certificate chain even with --keychain.
security list-keychains -d user -s "$@"
if ! security import "$certificate" -f pkcs12 -k "$keychain" -P "$MACOS_CERTIFICATE_PASSWORD" \
    -T /usr/bin/codesign >/dev/null 2>&1; then
    echo 'Cannot import the release PKCS#12 certificate and private key' >&2
    exit 1
fi
rm "$certificate"
unset MACOS_CERTIFICATE_PASSWORD
# Use Apple's Developer ID intermediates when the exported PKCS#12 omits them.
security find-certificate -a -Z "$keychain" > "$scratch/imported-certificates"
add_intermediate() {
    name=$1
    sha256=$2
    grep -qiF "$sha256" "$scratch/imported-certificates" && return 0
    path="$scratch/$name.cer"
    curl -q --fail --silent --show-error --location --proto '=https' \
        "https://www.apple.com/certificateauthority/$name.cer" -o "$path"
    printf '%s  %s\n' "$sha256" "$path" | shasum -a 256 --check --status || {
        echo 'Apple Developer ID intermediate checksum mismatch' >&2
        exit 1
    }
    security add-certificates -k "$keychain" "$path"
}
add_intermediate DeveloperIDCA 7afc9d01a62f03a2de9637936d4afe68090d2de18d03f29c88cfb0b1ba63587f
add_intermediate DeveloperIDG2CA f16cd3c54c7f83cea4bf1a3e6a0819c8aaa8e4a1528fd144715f350643d2df3a

identities=$(security find-identity -v -p codesigning "$keychain")
identity=$(printf '%s\n' "$identities" | awk -v team="($team_id)\"" '
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
if ! security set-key-partition-list -S apple-tool:,apple: -s -k "$keychain_password" "$keychain" >/dev/null; then
    echo 'Cannot grant codesign access to the imported private key' >&2
    exit 1
fi
unset keychain_password
echo 'Developer ID signing identity and certificate chain are ready'
umask "$original_umask"
cd "$(dirname "$0")/.."
CODESIGN_IDENTITY="$identity" CODESIGN_KEYCHAIN="$keychain" sh packaging/macos.sh --package-only
