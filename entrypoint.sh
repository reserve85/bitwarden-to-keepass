#!/bin/bash

set -uo pipefail

BW_PATH="${BW_PATH:-bw}"
BITWARDEN_URL="${BITWARDEN_URL:-https://bitwarden.com}"

log_error() {
    echo "Error: $1" >&2
}

get_bw_session() {
    local session
    # SECURITY: never run the interactive `bw login` / `bw unlock` prompts.
    # This container usually has no TTY, so the CLI would ECHO the typed email
    # and master password in clear text - and redirected output (Task Scheduler
    # logs, `*> file`) would write them to disk. Credentials must come from
    # environment variables instead.
    if [[ -n "${BW_CLIENTID:-}" && -n "${BW_CLIENTSECRET:-}" ]]; then
        # Personal API key (non-interactive; no master password / 2FA needed).
        # Log out first: `bw login` refuses while a previous session is stored
        # in the persistent bw-config volume, and the fallback `bw unlock`
        # would prompt for the password.
        "$BW_PATH" logout >/dev/null 2>&1 || true
        session=$("$BW_PATH" login --apikey --raw 2>/dev/null)
    elif [[ -n "${BW_SESSION:-}" ]]; then
        # Pre-generated session, e.g. `bw unlock --raw` run manually once.
        session="$BW_SESSION"
    else
        log_error "No Bitwarden credentials configured. Set BW_CLIENTID and BW_CLIENTSECRET (personal API key: https://vault.bitwarden.com/#/settings/security/keys) or BW_SESSION in '.env'. Refusing to prompt interactively because the prompt would display the email and password in clear text."
        return 1
    fi
    if [[ -z "$session" ]]; then
        log_error "Failed to obtain a Bitwarden session. Check BW_CLIENTID / BW_CLIENTSECRET in '.env'."
        return 1
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
if ! BW_SESSION=$(get_bw_session); then
    exit 1
fi

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
