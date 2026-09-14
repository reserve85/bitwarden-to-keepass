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
   - **Interactive (recommended if you do not want secrets in `.env`):** start
     the container with `docker compose run -it bitwarden-to-keepass` and type
     your email, master password and 2FA code into the terminal prompts. The
     master password is **masked** by the CLI (nothing is echoed or stored);
     note that the one-time 2FA code is shown as typed (official CLI behavior)
     but is single-use and expires within seconds. The CLI only prompts when a
     real terminal is attached - never when the script runs from Task Scheduler
     or with redirected output.
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
   set `DATABASE_PASSWORD` in `.env` *and* the container has a terminal. Set
   `DATABASE_PASSWORD` in `.env` for non-interactive or scheduled runs (see
   "Automated backup").

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
- For scheduled runs (e.g. Task Scheduler) append `--no-pause` so the script
  does not wait for a keypress:
  `powershell -NoProfile -ExecutionPolicy Bypass -File "create_backup.ps1" --no-pause`
- Set `PULL_LATEST=false` in `.env` to skip `git pull` and the Docker image
  rebuild for fast, deterministic scheduled backups.
- Docker Desktop's UI opens before the engine (WSL2) is ready. The script
  therefore waits up to `DOCKER_WAIT_SECONDS` (default 30, configurable in
  `.env`) for the daemon before reporting "not reachable".
- **Security:** the export never prompts for your Bitwarden email/password.
  In a non-TTY environment `bw login` / `bw unlock` would echo them in clear
  text (and redirected logs would store them on disk). Configure a personal
  API key (`BW_CLIENTID` / `BW_CLIENTSECRET`, vault.bitwarden.com ->
  Settings -> Security -> Keys) plus `BW_PASSWORD` (your Bitwarden master
  password - current servers only create a locked session from the API key, so
  the container unlocks it with `bw unlock --passwordenv`), or a pre-generated
  `BW_SESSION`, in `.env`; the script refuses to start without credentials and
  also requires `DATABASE_PASSWORD`.

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
- **Scheduled backups run the latest code.** `PULL_LATEST=true` (the default)
  pulls and executes a freshly fetched repository with your vault credentials.
  For deterministic, auditable automation set `PULL_LATEST=false` or pin the
  checkout/commit.
- **Verified build inputs.** The Docker image pins the Bitwarden CLI version
  and verifies its SHA-256 checksum at build time, pins `poetry`, and installs
  Python dependencies from the lock file.
