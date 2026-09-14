# bitwarden-to-keepass
Export (most of) your Bitwarden items into KeePass database.

## How it works?
It uses official [bitwarden-cli](https://bitwarden.com/help/article/cli/) client to export your items from Bitwarden vault and move them into your KeePass database - that includes logins (with TOTP seeds, URIs, custom fields, attachments, notes) and secure notes.

## Setup from scratch (Docker) - recommended

The whole export runs inside a Docker container, so you do **not** need Python
or the Bitwarden CLI installed on your machine.

### 1. What you need
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed
  **and running** - verify with `docker version`. Docker must be started before
  you run the commands below.
- `git` (Windows: Git for Windows; Linux/macOS: usually pre-installed)
- a Bitwarden account, your **master password** (and a 2FA code if enabled)
- (optional) a KeePass client such as [KeePassXC](https://keepassxc.org/) to
  open the resulting database afterwards

### 2. Clone the repository
Open a terminal (PowerShell / cmd / Bash) and run:

```
git clone https://github.com/reserve85/bitwarden-to-keepass
cd bitwarden-to-keepass
```

### 3. Create the `.env` file
The Docker setup reads its configuration from a file named `.env`. A fresh
clone does **not** contain it, so create it once:

```
cp .env.example .env
```

You can leave it as-is for the defaults, or adjust these keys:

- `DATABASE_PASSWORD=...` - sets the password for the KeePass database. If you
  leave it empty you will be asked for it interactively (recommended).
- `DATABASE_KEYFILE=...` - optional path to a key file used to protect the
  database.
- `BITWARDEN_URL=...` - set to `https://bitwarden.eu` if your account is in the
  EU region, or to your self-hosted Bitwarden server URL.
- `DATABASE_PATH=...` - where the database is written inside the container
  (default `/exports/bitwarden-export.kdbx`).

### 4. Run the export

```
docker compose run bitwarden-to-keepass
```

> Older setups use the standalone `docker-compose` binary (with a dash) instead
> of the `docker compose` plugin. If the command above is not found, try
> `docker-compose run bitwarden-to-keepass`.

The first run builds the Docker image automatically (this can take a few
minutes) and then starts the container.

### 5. What happens next
The container authenticates **non-interactively** and exports your vault:

1. It logs in and unlocks, or uses a pre-generated session:
   - **Interactive (recommended if you do not want secrets in `.env`):**
     double-click `create_backup.bat` (or run `create_backup.ps1`). The
     PowerShell script asks for your email, master password, 2FA code (if
     enabled) and the KeePass database password with **masked prompts in its
     own window** and passes them to a *non-interactive* container via
     temporary environment variables. It deliberately does **not** use
     `docker compose run -it`: on some Docker-for-Windows terminals (VS Code /
     Windows Terminal, ConPTY) that allocates a pseudo-terminal but never
     forwards the host keystrokes, so in-container prompts hang. Manual
     `docker compose run -it bitwarden-to-keepass` is still supported when the
     container has a real terminal - the CLI masks the master password and
     only prompts with a real terminal attached, never from Task Scheduler or
     with redirected output.
   - **Automated / API key:** it logs in with the **personal API key**
     (`BW_CLIENTID` / `BW_CLIENTSECRET`) and unlocks the vault with your
     **Bitwarden master password** (`BW_PASSWORD`, read via
     `bw unlock --passwordenv` - no interactive prompt, nothing is echoed), or
     uses a pre-generated `BW_SESSION` from your `.env`. For security it
     *never* runs the interactive `bw login` / `bw unlock` prompts unattended -
     without a terminal those prompts would echo your email and master password
     in clear text and dump them into redirected logs.
2. Your vault is synced, and your logins (with TOTP seeds, URIs, custom
   fields, attachments, notes) and secure notes are written into the KeePass
   database.
3. You are prompted for the **KeePass database password** only if you did not
   set `DATABASE_PASSWORD` in `.env` (leave the prompt empty to reuse it). Set
   `DATABASE_PASSWORD` in `.env` for non-interactive or scheduled runs where
   nobody can type (see "Automated backup").

### 6. Where is the result?
The database is written to `exports/bitwarden-export.kdbx` in the repository
folder (the `exports` directory is shared between the container and your
machine via a Docker volume). Open it with KeePassXC / KeePass 2 using the
password you chose in step 5.

### 7. Run it again / keep it up to date
- Pull the latest version before each run: `git pull`
- Run the export again: `docker compose run bitwarden-to-keepass`
- Repeated exports **update** the entries created by earlier runs (matched via
  the `Bitwarden ID` custom property) instead of piling up duplicates; entries
  for items that still exist are never created twice, and entries for failed
  items are rolled back instead of being saved half-written.
- For fully automated, scheduled backups with copies to OneDrive, Dropbox, etc.
  on Windows, see the [Automated backup (Windows)](#automated-backup-windows)
  section below.

## Usage without docker (venv)
- Clone this repository
- Run
```
make build
```
(On Windows, create the environment manually instead: `py -m venv .venv`,
then `pip install poetry && poetry install`, and activate with
`.venv\Scripts\activate`.)
- You can either **create new (empty) KeePass database** (tested with [KeePassXC](https://github.com/keepassxreboot/keepassxc) but it will probably work with others) right now, otherwise one will be created when the script is executed
- Go into the virtual environment
```
source .venv/bin/activate
```
- [Download](https://bitwarden.com/help/article/cli/#download-and-install) official bitwarden-cli and generate a session (`bw unlock --raw`).
- **SECURITY:** the session and the database password are *never* passed on the
  command line (argv is visible to other processes) - they come from
  environment variables or a hidden prompt. Run:
```
export BW_SESSION=$(bw unlock --raw)
export DATABASE_PASSWORD=your-database-password   # omit to be prompted
python run.py --database-path DATABASE_PATH [--database-keyfile DATABASE_KEYFILE] [--bw-path BW_PATH]
```
- Run the test suite (requires the virtual environment)
```
python -m unittest discover -s tests -v
```

## Automated backup (Windows)

The repository ships with `create_backup.ps1` (launched by the `create_backup.bat`
for double-click convenience). It wraps the Docker export and copies the
date-stamped database to several backup folders (OneDrive, Dropbox, local
disk, ...).

- Copy `.env.example` to `.env` and set the host-side keys (`REPO_DIR`,
  `BACKUP_DIRS`, ...) in addition to the Docker keys.
- Double-click `create_backup.bat`, or run the script directly:
  `powershell -NoProfile -ExecutionPolicy Bypass -File "create_backup.ps1"`
- **Interactive run (no secrets in `.env`):** leave the Bitwarden keys
  (`BW_CLIENTID` / `BW_CLIENTSECRET` / `BW_PASSWORD` / `BW_SESSION`) and
  `DATABASE_PASSWORD` out of `.env` and double-click `create_backup.bat` (or
  start the script from a terminal). It detects the missing credentials,
  switches to interactive mode and asks for your Bitwarden email, master
  password, 2FA code (if enabled) and the KeePass database password with
  masked prompts in the **PowerShell window itself** (nothing is stored in
  `.env`; the values reach the container as temporary environment variables
  that the entrypoint clears after the login, and the container runs without
  a TTY on purpose so this works from any terminal).
  `--interactive` forces this mode even if credentials are configured.
- For scheduled runs (e.g. Task Scheduler) append `--no-pause` so the script
  does not wait for a keypress:
  `powershell -NoProfile -ExecutionPolicy Bypass -File "create_backup.ps1" --no-pause`
- Set `PULL_LATEST=true` in `.env` for manual runs that pull the latest code
  and rebuild the Docker image. The default (`false`) runs the already-built
  image for fast, deterministic scheduled backups.
- Docker Desktop's UI opens before the engine (WSL2) is ready. The script
  therefore waits up to `DOCKER_WAIT_SECONDS` (default 30, configurable in
  `.env`) for the daemon before reporting "not reachable".
- **Security:** unattended runs (Task Scheduler, redirected output) never
  prompt for your Bitwarden email/password - without a terminal `bw login` /
  `bw unlock` would echo them in clear text (and redirected logs would store
  them on disk). For those runs configure a personal API key (`BW_CLIENTID` /
  `BW_CLIENTSECRET`, vault.bitwarden.com -> Settings -> Security -> Keys) plus
  `BW_PASSWORD` (your Bitwarden master password - current servers only create
  a locked session from the API key, so the container unlocks it with
  `bw unlock --passwordenv`), or a pre-generated `BW_SESSION`, in `.env`. From
  a real terminal the script instead runs interactively and asks you for the
  credentials - nothing needs to be stored in `.env`.

## Security notes

- **Secrets stay out of the command line.** `run.py` reads `BW_SESSION` and the
  database password exclusively from the environment (or a hidden interactive
  prompt). Command-line arguments are visible to other processes (`ps`, Task
  Manager, `/proc`) and are therefore not accepted.
- **`.env` is your secret store.** It is excluded from git and the Docker build
  context, but keep it out of synced folders (OneDrive, Dropbox, ...). The
  KeePass database is only as secure as `DATABASE_PASSWORD`: if both the
  database and the file containing its password live in the same cloud
  account, the encryption offers little protection.
- **Rotate leaked secrets.** Older commits of this repository contained a real
  `DATABASE_PASSWORD` in the tracked `.env` file. If you ever used that value,
  rotate it now and purge the git history (`git filter-repo` / BFG). The
  repository now ships a gitleaks pre-commit hook and a CI secret scan to keep
  secrets out of the tree.
- **Scheduled backups are deterministic.** `PULL_LATEST=false` is the default:
  unattended runs execute the already-built Docker image, so a compromised
  upstream repository or a freshly pushed bad commit cannot silently change
  what your nightly backup does. Pull and rebuild deliberately for manual runs
  (`PULL_LATEST=true` in `.env`, or `docker compose build`), ideally while you
  review the changes.
- **Verified build inputs.** The Docker image pins the Bitwarden CLI version
  and verifies its SHA-256 checksum at build time, pins `poetry`, and installs
  Python dependencies from the lock file.
- **Least privilege inside the container.** The image runs under an unprivileged
  user (`appuser`, uid 1000) and the source tree is mounted read-only, so `bw`
  or this script cannot modify the host checkout; only `exports/` (the database
  output) is writable. The Bitwarden CLI config lives in the `bw-config` volume
  under `/home/appuser/.config/Bitwarden CLI` (one re-login is needed when you
  upgrade from an image that used `/root`). On Linux hosts whose user id is not
  1000, run `chown -R 1000:1000 exports/` once so the container can write the
  database.
