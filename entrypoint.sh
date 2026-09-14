#!/bin/bash

set -uo pipefail

BW_PATH="${BW_PATH:-bw}"
BITWARDEN_URL="${BITWARDEN_URL:-https://bitwarden.com}"

log_error() {
    echo "Error: $1" >&2
}

get_bw_session() {
    local session
    session=$("$BW_PATH" login --raw || "$BW_PATH" unlock --raw)
    if [[ -z "$session" ]]; then
        log_error "Failed to obtain a Bitwarden session."
        exit 1
    fi
    echo "$session"
}

# Strip trailing slashes so "https://bitwarden.com/" and
# "https://bitwarden.com" compare equal.
normalize_url() {
    local value=$1
    while [[ "$value" == */ ]]; do
        value=${value%/}
    done
    printf '%s' "$value"
}

# The Bitwarden CLI refuses to change the server while a session exists
# ("Logout required before server config update."). The persistent bw-config
# volume keeps the login state between runs, so only reconfigure when the
# configured server differs from the requested one, and log out first.
current_server=$(normalize_url "$("$BW_PATH" config server 2>/dev/null)")
target_server=$(normalize_url "$BITWARDEN_URL")
if [[ "$current_server" != "$target_server" ]]; then
    "$BW_PATH" logout >/dev/null 2>&1 || true
    "$BW_PATH" config server "$BITWARDEN_URL" >/dev/null 2>&1 || {
        log_error "Failed to configure Bitwarden server URL."
        exit 1
    }
fi

export BW_SESSION
BW_SESSION=$(get_bw_session)

# Always lock the vault, even if a later step fails.
trap '"$BW_PATH" lock >/dev/null 2>&1 || true' EXIT

"$BW_PATH" sync || {
    log_error "Failed to sync Bitwarden vault."
    exit 1
}

poetry run python run.py || {
    log_error "Failed to run main export script."
    exit 1
}

exit 0
