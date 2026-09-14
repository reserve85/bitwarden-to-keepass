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

### 5. What happens next (interactively)
The container logs you in and exports your vault. You will be asked to:

1. **Log in to your Bitwarden vault** with the official
   [bitwarden-cli](https://bitwarden.com/help/article/cli/):
   - the first time: your Bitwarden **email**, **master password** and, if
     enabled, your **2FA code**
   - on later runs you stay logged in (the login state is kept in a persistent
     Docker volume), so only your **master password** is requested again
2. **Choose the KeePass database password**: a new, strong password that will
   protect your `bitwarden-export.kdbx` file. It is not displayed while typing,
   and you will need it every time you open the database. (This prompt is
   skipped if you set `DATABASE_PASSWORD` in `.env`.)

The vault is synced and your logins (with TOTP seeds, URIs, custom fields,
attachments, notes) and secure notes are written into the KeePass database.

### 6. Where is the result?
The database is written to `exports/bitwarden-export.kdbx` in the repository
folder (the `exports` directory is shared between the container and your
machine via a Docker volume). Open it with KeePassXC / KeePass 2 using the
password you chose in step 5.

### 7. Run it again / keep it up to date
- Pull the latest version before each run: `git pull`
- Run the export again: `docker compose run bitwarden-to-keepass`
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
- [Download](https://bitwarden.com/help/article/cli/#download-and-install) official bitwarden-cli and do `bw login` (you need `BW_SESSION` for export to work).
- Run
```
python run.py --bw-session BW_SESSION --database-path DATABASE_PATH [--database-password DATABASE_PASSWORD] [--database-keyfile DATABASE_KEYFILE] [--bw-path BW_PATH]
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
  Settings -> Security -> Keys) or `BW_SESSION` in `.env`; the script refuses
  to start without credentials and also requires `DATABASE_PASSWORD`.
