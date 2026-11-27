#!/bin/sh
# Publish only a Developer ID disk image with an accepted, stapled Apple ticket.
set -eu
: "${RUNNER_TEMP:?}"
: "${APPLE_ID:?missing Apple ID secret}"
: "${APPLE_TEAM_ID:?missing Apple team ID secret}"
: "${APPLE_APP_SPECIFIC_PASSWORD:?missing Apple app-specific password secret}"

image=$1
codesign --verify --strict -R='anchor apple generic and certificate leaf[field.1.2.840.113635.100.6.1.13] exists' "$image"
umask 077
scratch=$(mktemp -d "$RUNNER_TEMP/gitdoc-notary.XXXXXX")
trap 'rm -rf "$scratch"' 0
trap 'exit 1' HUP INT TERM
notary() {
    xcrun notarytool "$@" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
        --password "$APPLE_APP_SPECIFIC_PASSWORD"
}
submit_result=0
notary submit "$image" --wait --timeout 45m --output-format json > "$scratch/result.json" \
    2> "$scratch/errors" || submit_result=$?
submission=$(jq -er '.id | strings | select(test("^[A-Fa-f0-9-]{36}$"))' "$scratch/result.json" 2>/dev/null) || submission=
status=$(jq -er '.status | strings' "$scratch/result.json" 2>/dev/null) || status=
if [ "$submit_result" -ne 0 ] || [ "$status" != Accepted ] || [ -z "$submission" ]; then
    case "$status" in
        Invalid|Rejected|'In Progress') echo "Apple notarization status: $status" >&2 ;;
        *) echo "Apple notarization did not return Accepted (notarytool exit $submit_result)" >&2 ;;
    esac
    # Print HTTP status codes without echoing credential diagnostics.
    LC_ALL=C grep -Eo 'HTTP( status code:?)? [0-9]{3}' "$scratch/errors" >&2 || true
    if [ -n "$submission" ]; then
        echo "Apple notarization submission: $submission" >&2
        if notary log "$submission" "$scratch/log.json" >/dev/null 2>&1; then
            jq -r '(.issues // [])[] | [.severity, .path, .message] | @tsv' "$scratch/log.json" >&2
        fi
    fi
    exit 1
fi
unset APPLE_ID APPLE_TEAM_ID APPLE_APP_SPECIFIC_PASSWORD
echo "Apple notarization Accepted: $submission"
xcrun stapler staple "$image"
xcrun stapler validate "$image"
codesign --verify --strict "$image"
spctl --assess --type open --context context:primary-signature --verbose=2 "$image"
