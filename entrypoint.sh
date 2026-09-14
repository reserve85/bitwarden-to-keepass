#!/bin/bash

set -uo pipefail

BW_PATH="${BW_PATH:-bw}"
BITWARDEN_URL="${BITWARDEN_URL:-https://bitwarden.com}"
# Match the 600 s cap the Python side applies to every `bw` call
# (COMMAND_TIMEOUT_SECONDS in src/bw_client.py): a hung `bw sync` (unreachable
# server, stuck daemon) must not block a scheduled export forever.
COMMAND_TIMEOUT_SECONDS=600

log_error() {
    echo "Error: $1" >&2
}

# ---------------------------------------------------------------------------
# The Bitwarden CLI stores its state in the named volume mounted at
# $HOME/.config/Bitwarden CLI. The container runs as the unprivileged
# 'appuser', but the volume can easily be owned by ROOT instead:
#   * volumes created before the non-root hardening (commit 81dee46), or
#   * fresh named volumes Docker creates at a mount path that does not exist
#     in the image (the volume root is then created as root).
# Either way the CLI (a Node.js binary) crashes with
#   EACCES: permission denied, open '.../data.json'
# which used to be masked behind the misleading "Failed to configure Bitwarden
# server URL." Detect the condition up front and tell the user how to repair it.
# ---------------------------------------------------------------------------
BW_CONFIG_DIR="${HOME}/.config/Bitwarden CLI"
if [[ ! -d "$BW_CONFIG_DIR" ]] || ! bw_config_probe=$(mktemp "$BW_CONFIG_DIR/.bw-write-test.XXXXXXXXXX" 2>/dev/null); then
    log_error "The Bitwarden CLI config directory '$BW_CONFIG_DIR' is not writable by the container's user '$(id -un)' (uid $(id -u)) - the 'bw-config' volume is still owned by root."
    log_error "Fix the ownership ONCE (keeps your logged-in session and 2FA state) - from your repository folder run:"
    log_error "    docker compose run --rm --user root --entrypoint chown ${COMPOSE_SERVICE:-bitwarden-to-keepass} -R appuser:appuser \"$BW_CONFIG_DIR\""
    log_error "(Replace '${COMPOSE_SERVICE:-bitwarden-to-keepass}' with your COMPOSE_SERVICE if you changed it.)"
    log_error "Or, if you do not need to keep the logged-in session, simply delete the volume (find it with 'docker volume ls', usually named '<project>_bw-config') and re-run:"
    log_error "    docker volume rm <project>_bw-config"
    exit 1
fi
rm -f "$bw_config_probe"

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
        # INTERACTIVE MODE (manual runs only, via `docker compose run -it`);
        # create_backup.ps1 uses the host-collected-secrets branch below.
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
        # terminal. The session is fetched from the LOGIN process itself
        # (`bw login --raw`): login derives the master key from the password,
        # so the returned session stays unlocked for the later `bw` processes.
        # A separate `bw unlock --raw` trips a known Bitwarden CLI bug
        # (bitwarden/clients#20720): it returns a session key but the vault
        # stays LOCKED, so `bw list ...` prompts for the master password again
        # and fails with "The decryption operation failed". stderr stays open
        # here so the interactive 2FA challenge still reaches the terminal; the
        # session key is the only thing written to stdout (`--raw`).
        session=$(BW_PASSWORD="$bw_master" "$BW_PATH" login --passwordenv BW_PASSWORD --raw "$bw_email")
        if [[ -z "$session" ]]; then
            # The login itself failed (wrong password / 2FA code) or this CLI
            # returned only a locked session; fall back to the classic unlock
            # path. The status check below rejects a still-locked vault.
            session=$(BW_PASSWORD="$bw_master" "$BW_PATH" unlock --passwordenv BW_PASSWORD --raw </dev/null 2>/dev/null)
        fi
        unset bw_master
        if [[ -z "$session" ]]; then
            log_error "Interactive login failed. Check your email address, master password and 2FA code (or approve a pending 'Login with device' request on your phone). If the login itself said it succeeded, run 'bw unlock --raw' manually and set the output as BW_SESSION in '.env'."
            return 1
        fi
        if ! BW_SESSION="$session" "$BW_PATH" status 2>/dev/null | grep -q '"status":"unlocked"'; then
            log_error "The session is not unlocked (known Bitwarden CLI bug bitwarden/clients#20720). Clear the Docker volume 'bitwarden-to-keepass_bw-config' (docker volume rm) and re-run, or set BW_SESSION in '.env' from a manual 'bw unlock --raw'."
            return 1
        fi
    elif [[ -n "${BW_EMAIL:-}" && -n "${BW_MASTER_PASSWORD:-}" ]]; then
        # INTERACTIVE LOGIN WITH HOST-COLLECTED SECRETS (create_backup.ps1
        # --interactive / auto-detected). The PowerShell script asks for the
        # email, master password, 2FA code and KeePass database password with
        # masked prompts in its OWN window and hands them to this container as
        # temporary environment variables. This deliberately requires NO
        # container TTY: on some Docker-for-Windows terminals (VS Code /
        # Windows Terminal, ConPTY) `docker compose run -it` allocates a
        # pseudo-terminal but never forwards the host keystrokes, so
        # in-container prompts hang forever while the host console stays
        # silent. The secrets are cleared from the environment right after the
        # login (run.py only needs DATABASE_PASSWORD).
        "$BW_PATH" logout >/dev/null 2>&1 || true
        echo "Logging in to Bitwarden as $BW_EMAIL (secrets collected by create_backup.ps1)..." >&2
        # Fetch the unlocked session DIRECTLY from the login process (`bw login
        # --raw`): login derives the master key from the password, so the
        # returned session can decrypt the vault in the later export processes.
        # A separate `bw unlock --raw` trips a known Bitwarden CLI bug
        # (bitwarden/clients#20720): it returns a session key but the vault
        # stays LOCKED, so `bw list ...` prompts for the master password again
        # and fails with "The decryption operation failed".
        # SECURITY NOTE: the one-time 2FA code is the only secret handed to
        # the CLI as a command-line argument (--code) - the only place the CLI
        # accepts it. It is single-use, expires within seconds and is never
        # stored anywhere by this script.
        if [[ -n "${BW_TOTP:-}" ]]; then
            session=$(BW_PASSWORD="$BW_MASTER_PASSWORD" "$BW_PATH" login --passwordenv BW_PASSWORD --code "$BW_TOTP" --raw "$BW_EMAIL" 2>/dev/null)
        else
            session=$(BW_PASSWORD="$BW_MASTER_PASSWORD" "$BW_PATH" login --passwordenv BW_PASSWORD --raw "$BW_EMAIL" 2>/dev/null)
        fi
        if [[ -z "$session" ]]; then
            # Fallback: a few server/CLI combinations only hand out a LOCKED
            # session from `bw login --raw`; the classic unlock path still runs
            # everything (the status check below rejects a still-locked vault).
            session=$(BW_PASSWORD="$BW_MASTER_PASSWORD" "$BW_PATH" unlock --passwordenv BW_PASSWORD --raw </dev/null 2>/dev/null)
        fi
        unset BW_EMAIL BW_MASTER_PASSWORD BW_TOTP
        if [[ -z "$session" ]]; then
            log_error "Login failed. Check the email address and master password; if 2FA is enabled, provide the one-time code (or approve a pending 'Login with device' request on your phone and re-run the script)."
            return 1
        fi
        # Guard against bitwarden/clients#20720: only accept a session whose
        # vault is actually UNLOCKED, so the user gets this clear message
        # instead of a cryptic crypto error from run.py later.
        if ! BW_SESSION="$session" "$BW_PATH" status 2>/dev/null | grep -q '"status":"unlocked"'; then
            log_error "The session is not unlocked (known Bitwarden CLI bug bitwarden/clients#20720). Clear the Docker volume 'bitwarden-to-keepass_bw-config' (docker volume rm) and re-run, or set BW_SESSION in '.env' from a manual 'bw unlock --raw'."
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
    if ! config_output=$("$BW_PATH" config server "$BITWARDEN_URL" 2>&1); then
        log_error "Failed to configure Bitwarden server URL ('$BITWARDEN_URL')."
        [[ -n "${config_output:-}" ]] && log_error "$config_output"
        exit 1
    fi
fi

export BW_SESSION
if ! BW_SESSION=$(get_bw_session); then
    exit 1
fi

# Always lock the vault, even if a later step fails.
trap '"$BW_PATH" lock >/dev/null 2>&1 || true' EXIT

timeout "$COMMAND_TIMEOUT_SECONDS" "$BW_PATH" sync || {
    log_error "Failed to sync Bitwarden vault (timed out after ${COMMAND_TIMEOUT_SECONDS}s)."
    exit 1
}

poetry run python run.py || {
    log_error "Failed to run main export script."
    exit 1
}

exit 0
