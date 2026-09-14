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
    if [[ -n "${BW_SESSION:-}" ]]; then
        # Pre-generated session, e.g. `bw unlock --raw` run manually once.
        session="$BW_SESSION"
    elif [[ -t 0 ]]; then
        # INTERACTIVE MODE - only reachable through `docker compose run -it`.
        # stdin must be a real terminal so the prompts can be answered and the
        # master password masked. stdout is deliberately NOT required: on some
        # Docker-for-Windows hosts the container's stdout is not a terminal
        # even with `-t`, so every prompt is printed to stderr instead. The
        # old `session=$(bw login --raw)` also captured the CLI's stdout, and
        # in that exact combination the CLI silently swallows its login
        # prompts and waits forever instead of asking for the email address.
        # So the email is read by bash and the master password by Python's
        # getpass - the same masked prompt run.py uses for the database
        # password - and then handed to the CLI through an environment
        # variable; the only prompt the CLI still shows is the one-time 2FA
        # code, which appears on the terminal (official CLI behavior). When
        # the script runs unattended (create_backup.ps1 / Task Scheduler)
        # stdin is not a TTY, so this branch is never taken and the secrets
        # cannot leak into redirected logs.
        "$BW_PATH" logout >/dev/null 2>&1 || true
        echo "Interactive Bitwarden login:" >&2
        echo "  Email is shown as typed; the master password is masked." >&2
        echo "  The one-time 2FA code (if enabled) is typed visibly but is" >&2
        echo "  single-use and expires within seconds - or skip it by using the" >&2
        echo "  personal-API-key flow (bw login --apikey; client secret is masked)." >&2
        local bw_email bw_master
        while :; do
            echo -n "Bitwarden email: " >&2
            read -r bw_email
            bw_master=$(python3 -c 'import getpass; print(getpass.getpass("Master password: "))' </dev/tty) || {
                echo "Could not read the master password - is the terminal still attached?" >&2
                return 1
            }
            if [[ -n "$bw_email" && -n "$bw_master" ]]; then
                break
            fi
            echo "Email and master password must not be empty - try again." >&2
        done
        # `bw` reads the password from the environment, so it neither echoes it
        # nor stores it; accounts with 2FA get the one-time code prompt on the
        # terminal. The login status output stays on the console.
        if ! BW_PASSWORD="$bw_master" "$BW_PATH" login --passwordenv BW_PASSWORD "$bw_email"; then
            unset bw_master
            log_error "Interactive login failed. Check your email address, master password and 2FA code (or approve a pending 'Login with device' request on your phone)."
            return 1
        fi
        # Fetch the raw session for run.py without prompting again: the password
        # comes from the environment and stdin is /dev/null, so nothing echoes
        # and nothing can be swallowed by the command substitution. `bw unlock
        # --raw` returns a session whether the login left the vault unlocked or
        # only authenticated.
        session=$(BW_PASSWORD="$bw_master" "$BW_PATH" unlock --passwordenv BW_PASSWORD --raw </dev/null 2>/dev/null)
        unset bw_master
        if [[ -z "$session" ]]; then
            log_error "Interactive login succeeded, but obtaining a session failed. Run 'bw unlock --raw' in a terminal and set the output as BW_SESSION in '.env'."
            return 1
        fi
    elif [[ -n "${BW_CLIENTID:-}" && -n "${BW_CLIENTSECRET:-}" ]]; then
        # Personal API key (non-interactive; no email/password prompt, no 2FA).
        # Log out first: `bw login` refuses while a previous session is stored
        # in the persistent bw-config volume, and the fallback `bw unlock`
        # would prompt for the password.
        "$BW_PATH" logout >/dev/null 2>&1 || true

        # Some servers still hand out the session key directly at login time.
        session=$("$BW_PATH" login --apikey --raw 2>/dev/null)

        # Current Bitwarden servers and recent Vaultwarden releases only create
        # a *locked* session from the API key (the login no longer returns the
        # session key). Unlock it non-interactively with the master password
        # from the environment: `bw unlock --passwordenv` reads the variable
        # without a prompt, so nothing is echoed or written to disk.
        if [[ -z "$session" ]]; then
            if [[ -z "${BW_PASSWORD:-}" ]]; then
                log_error "The API-key login only produces a locked session on current Bitwarden/Vaultwarden servers, and BW_PASSWORD (your Bitwarden MASTER password - NOT the KeePass DATABASE_PASSWORD) is not set. Add BW_PASSWORD to '.env'; it is read via 'bw unlock --passwordenv', never shown or stored by the CLI. Alternative: set BW_SESSION (from a manual 'bw unlock --raw')."
                return 1
            fi
            # `bw login --apikey --raw` above already created the session on
            # new CLI versions; a plain login is a harmless no-op otherwise.
            "$BW_PATH" login --apikey >/dev/null 2>&1 || true
            session=$("$BW_PATH" unlock --passwordenv BW_PASSWORD --raw </dev/null 2>/dev/null)
        fi
    else
        log_error "No Bitwarden credentials configured. Set BW_CLIENTID and BW_CLIENTSECRET (personal API key: https://vault.bitwarden.com/#/settings/security/keys) plus BW_PASSWORD, or set BW_SESSION in '.env'. Refusing to prompt interactively because the prompt would display the email and password in clear text."
        return 1
    fi
    if [[ -z "$session" ]]; then
        log_error "Failed to obtain a Bitwarden session. Check BW_CLIENTID, BW_CLIENTSECRET and BW_PASSWORD (Bitwarden master password) in '.env'."
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
