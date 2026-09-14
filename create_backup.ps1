<#
    create_backup.ps1
    ============================================================
    Full automation of the Bitwarden -> KeePass export:
      1) (optional) pull the latest version of the repository
      2) (optional) build the Docker image
      3) run the export inside Docker
      4) stamp the KeePass database with today's date and copy it
         to every configured backup folder

    All configuration lives in the ".env" file in this script's
    folder (template: ".env.example") - nothing is hardcoded here.

    Usage:
      .\create_backup.ps1
      .\create_backup.ps1 --no-pause
      .\create_backup.ps1 --interactive

    "--no-pause" skips the "press Enter" prompt at the end. Use it
    when the script is started from the Task Scheduler or similar
    (input redirection disables the pause automatically as well).

    "--interactive" (or "-i") forces an interactive export: the script asks
    for your Bitwarden email, master password, 2FA code (if enabled) and the
    KeePass database password with masked prompts in your OWN console window
    and hands them to the container through temporary environment variables -
    no secret needs to be stored in ".env" and no container TTY is required
    (Docker-for-Windows terminals like VS Code / Windows Terminal can hang
    with "docker compose run -it"). When ".env" contains no Bitwarden
    credentials (BW_CLIENTID / BW_CLIENTSECRET / BW_SESSION) and the script is
    started from a real console (e.g. double-clicking create_backup.bat) it
    switches to interactive mode automatically. Runs without a terminal (Task
    Scheduler, redirected output) refuse to prompt and require credentials
    in ".env".

    Requirements: a git checkout of this repository, Docker Desktop
    running, and a configured ".env" file. If Docker Desktop is still
    starting, the script waits DOCKER_WAIT_SECONDS (default 30) seconds
    for the daemon before giving up.
#>

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
#  Configuration - loaded from ".env" next to this script.
# ---------------------------------------------------------------------------

function Read-EnvFile {
    param([string]$Path)
    $envVars = @{}
    if (-not (Test-Path -Path $Path -PathType Leaf)) {
        return $envVars
    }
    Get-Content -Path $Path -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { return }
        # tolerate bash-style "export KEY=VALUE"
        if ($line -match '^export\s+') { $line = $line.Substring(7).Trim() }
        $eq = $line.IndexOf("=")
        if ($eq -lt 1) { return }
        $key   = $line.Substring(0, $eq).Trim()
        $value = $line.Substring($eq + 1).Trim()
        # strip one level of surrounding single or double quotes
        if ($value.Length -ge 2) {
            $first = $value.Substring(0, 1)
            $last  = $value.Substring($value.Length - 1)
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        $envVars[$key] = $value
    }
    return $envVars
}

$Cfg = Read-EnvFile (Join-Path $PSScriptRoot ".env")
if ($Cfg.Count -eq 0) {
    Write-Host ""
    Write-Host "[ERROR] No configuration found in '.env' next to this script."
    Write-Host "[ERROR] Copy '.env.example' to '.env' and adjust the values first."
    Write-Host ""
    exit 1
}

function Get-Cfg {
    param([string]$Key, $Default)
    if ($Cfg.ContainsKey($Key) -and $Cfg[$Key] -ne "") { return $Cfg[$Key] }
    return $Default
}

# Pause at the end unless "--no-pause" was given or input is redirected.
$WaitForKey = $true
if ("--no-pause" -in $args) { $WaitForKey = $false }
if ($WaitForKey -and [Console]::IsInputRedirected) { $WaitForKey = $false }

# True when stdin is attached to a real console (not a pipe / redirect).
$HasConsole = -not [Console]::IsInputRedirected

# "--interactive" forces the interactive export even if credentials are
# configured in ".env".
$ForceInteractive = ("--interactive" -in $args) -or ("-i" -in $args)

# Optional run log; empty means no log file.
$LogFile = Get-Cfg "LOG_FILE" ""

function Log-Line {
    param([string]$Prefix, [string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "{0} [{1}] {2}" -f $timestamp, $Prefix, $Message
    Write-Host $line
    if ($LogFile -ne "") {
        try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 }
        catch { }   # logging must never break the backup
    }
}

function Info {
    param([string]$m)
    Log-Line "INFO" $m
}

function Warn {
    param([string]$m)
    Log-Line "WARN" $m
}

function Exit-WithError {
    param([string]$Message)
    Log-Line "ERROR" $Message
    if ($WaitForKey) {
        Write-Host ""
        Read-Host "Press Enter to close..."
    }
    exit 1
}

# Run an external command from a token list and return its exit code.
# The command's output is discarded so it cannot leak into the return value:
# without this, stdout (e.g. `docker compose version`) would become part of
# the result (an Object[] instead of the plain exit code) and silently break
# every `-eq 0` / `-ne 0` check.
function Invoke-Cli {
    param([string[]]$Command)
    $exe  = $Command[0]
    $rest = @()
    if ($Command.Length -gt 1) { $rest = $Command[1..($Command.Length - 1)] }
    & $exe @rest | Out-Null
    return $LASTEXITCODE
}

# Convert a SecureString back to plain text. The container needs the literal
# value (entrypoint.sh reads it via `bw login --passwordenv`; run.py reads
# DATABASE_PASSWORD from its environment) - PowerShell's ConvertFrom-SecureString
# would produce an encrypted blob instead. Use the result immediately and clear
# the plaintext variable (set it to $null) as soon as the value has been passed
# to `docker compose run -e`.
function ConvertTo-PlainText {
    param([System.Security.SecureString]$Secure)
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try {
        return [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

# ---------------------------------------------------------------------------
#  1) Pre-flight checks
# ---------------------------------------------------------------------------
$RepoDir     = Get-Cfg "REPO_DIR" $null
$Service     = Get-Cfg "COMPOSE_SERVICE" "bitwarden-to-keepass"
$BackupStr   = Get-Cfg "BACKUP_DIRS" ""
$PullLatest  = (Get-Cfg "PULL_LATEST" "true") -match '^(1|true|yes|on)$'

# The container writes the database to DATABASE_PATH (inside "/exports"), and
# docker-compose mounts the host folder "<REPO_DIR>\exports" at "/exports".
# The host-side export file is therefore derived from DATABASE_PATH - a single
# source of truth, so there is no separate (easily mismatched) path setting.
$DatabasePath = Get-Cfg "DATABASE_PATH" "/exports/bitwarden-export.kdbx"
if ($DatabasePath -notmatch '^/exports/') {
    Exit-WithError "DATABASE_PATH must start with /exports/ (the docker-compose volume mount); got: $DatabasePath"
}
$ExportFile = Join-Path $RepoDir (Join-Path "exports" (($DatabasePath -replace '^/exports/', '').Replace('/', '\')))

Write-Host ""
Info "=== Bitwarden -> KeePass automated export ==="

if ($RepoDir -eq $null -or $RepoDir -eq "") {
    Exit-WithError "REPO_DIR is not set in '.env'. Point it at your local git checkout of this repository."
}
if (-not (Test-Path -Path (Join-Path $RepoDir ".git") -PathType Container)) {
    Exit-WithError "Repository directory is missing or is not a git checkout: $RepoDir"
}

$DockerCli = Get-Command docker -ErrorAction SilentlyContinue
if ($null -eq $DockerCli) {
    Exit-WithError "docker.exe was not found on the PATH. Docker Desktop adds it to the PATH when it starts - make sure Docker Desktop is fully started, then reopen your terminal / run the script again."
}
Info ("Using Docker CLI: {0}" -f $DockerCli.Source)

# Docker Desktop opens its UI long before the engine inside WSL2 is
# reachable, so a single check right after startup fails spuriously.
# Poll `docker info` for DOCKER_WAIT_SECONDS seconds before giving up.
$DockerWaitSeconds = [int](Get-Cfg "DOCKER_WAIT_SECONDS" 30)
$DockerReady = $false
$ProbeDeadline = (Get-Date).AddSeconds($DockerWaitSeconds)
while (-not $DockerReady) {
    # quiet probe - `docker info` prints "Cannot connect..." to stderr on failure
    $null = & $DockerCli.Source info 2>$null
    if ($LASTEXITCODE -eq 0) { $DockerReady = $true; break }
    if ((Get-Date) -ge $ProbeDeadline) { break }
    Warn ("Docker daemon not reachable yet - Docker Desktop is probably still starting. Retrying in 3 s (giving up after {0} s)..." -f $DockerWaitSeconds)
    Start-Sleep -Seconds 3
}
if (-not $DockerReady) {
    Exit-WithError "The Docker daemon is not reachable (waited $DockerWaitSeconds s). Is Docker Desktop running? Wait until the Docker Desktop whale icon is steady (it can take a few seconds to start), then run the script again."
}

# Prefer the modern "docker compose" plugin, fall back to "docker-compose".
$UseModernCompose = ((Invoke-Cli @("docker", "compose", "version")) -eq 0)
if (-not $UseModernCompose -and ((Invoke-Cli @("docker-compose", "version")) -ne 0)) {
    Exit-WithError "Docker Compose is not available. Is Docker Desktop running?"
}

# ---------------------------------------------------------------------------
#  SECURITY: prompts never run inside the container. The interactive export is
#  only available when the script is started from a terminal (double-click
#  create_backup.bat) or with "--interactive". The script collects the
#  Bitwarden email, master password, 2FA code (if enabled) and the KeePass
#  database password with masked prompts in its OWN window and hands them to a
#  NON-interactive container via `-e` environment variables (the entrypoint
#  reads them with `bw login --passwordenv` and clears them before run.py
#  starts). No container TTY is involved: on some Docker-for-Windows terminals
#  (VS Code / Windows Terminal, ConPTY) "docker compose run -it" allocates a
#  pseudo-terminal but never forwards the host keystrokes, which makes
#  in-container prompts hang.
#
#  * Interactive export (no secrets in ".env"): run from a real console
#    (double-click create_backup.bat) or pass "--interactive".
#  * Non-interactive / scheduled export: all secrets come from ".env" and the
#    script fails fast if they are missing.
# ---------------------------------------------------------------------------
$HasApiKey = (
    $Cfg.ContainsKey("BW_CLIENTID") -and $Cfg["BW_CLIENTID"] -ne "" -and
    $Cfg.ContainsKey("BW_CLIENTSECRET") -and $Cfg["BW_CLIENTSECRET"] -ne ""
)
$HasSession = $Cfg.ContainsKey("BW_SESSION") -and $Cfg["BW_SESSION"] -ne ""
$HasBwPassword = $Cfg.ContainsKey("BW_PASSWORD") -and $Cfg["BW_PASSWORD"] -ne ""

# Automatic fallback: started from a real console without Bitwarden
# credentials in ".env" -> run interactively. Redirected input (Task
# Scheduler, pipes) must keep refusing: no TTY, no safe prompt.
$Interactive = $ForceInteractive -or ($HasConsole -and -not ($HasApiKey -or $HasSession))

if (-not $Interactive) {
    if (-not ($HasApiKey -or $HasSession)) {
        Exit-WithError "Bitwarden credentials are missing in '.env'. Set BW_CLIENTID and BW_CLIENTSECRET (personal API key: vault.bitwarden.com -> Settings -> Security -> Keys) plus BW_PASSWORD (your Bitwarden master password - see next check) or BW_SESSION. Alternatively start the script from a real terminal (no input redirection) - it then runs interactively and asks for email, master password, 2FA and the KeePass database password instead."
    }
    if ($HasApiKey -and -not $HasSession -and -not $HasBwPassword) {
        Exit-WithError "BW_PASSWORD is missing in '.env': current Bitwarden/Vaultwarden servers only create a LOCKED session from the personal API key, so the container needs your Bitwarden MASTER password to unlock it (via 'bw unlock --passwordenv'; no interactive prompt, nothing is echoed). This is NOT the KeePass DATABASE_PASSWORD. Alternative: set BW_SESSION from a manual 'bw unlock --raw', or start the script from a real terminal for the interactive export."
    }
    if (-not ($Cfg.ContainsKey("DATABASE_PASSWORD") -and $Cfg["DATABASE_PASSWORD"] -ne "")) {
        Exit-WithError "DATABASE_PASSWORD is missing in '.env'. run.py uses it for the KeePass database; without it the export would prompt via getpass, which echoes the input in clear text when there is no TTY. Or start the script from a real terminal for the interactive export - it asks for the password with masked input instead."
    }
}

if ($Interactive) {
    Info "INTERACTIVE mode: you will be asked for your Bitwarden email, master password, 2FA code (if enabled) and the KeePass database password - right before the export starts, in this window."
}

function Run-Compose {
    param([string[]]$ComposeArgs)
    # Relay native stdout to the console but keep it out of the return value
    # (see Invoke-Cli): any native stdout line (e.g. "Image ... Built") inside
    # `if ((Run-Compose ...) -ne 0)` would become extra function output and
    # turn the return value into an Object[], making the `-ne 0` check always
    # true - a successful build would look like a failure.
    # Deliberately NO 2>&1 here: docker prints its progress to stderr, and under
    # $ErrorActionPreference = 'Stop' PowerShell 5.1 promotes native stderr to a
    # terminating error, which would abort the script mid-build.
    # Interactive runs no longer pass "-it" to `run`: the container is
    # non-interactive (secrets arrive via `-e` environment variables), so
    # piping stdout cannot expose a typed password and the TTY-attach hang on
    # Docker-for-Windows terminals is avoided.
    if ($UseModernCompose) { docker compose @ComposeArgs | ForEach-Object { Write-Host $_ } }
    else                   { docker-compose @ComposeArgs | ForEach-Object { Write-Host $_ } }
    return $LASTEXITCODE
}

# ---------------------------------------------------------------------------
#  2) Pull the latest version (optional)
#  If this fails there are usually local changes in the repository (e.g. a
#  modified ".env") - commit or stash them first, then run the script again.
# ---------------------------------------------------------------------------
Push-Location $RepoDir

if ($PullLatest) {
    Info "Pulling the latest version (git pull --ff-only)..."
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) {
        Exit-WithError "git pull failed. There are usually local changes in the repository (e.g. a modified '.env') - commit or stash them first, then run the script again."
    }
} else {
    Info "PULL_LATEST=false - skipping 'git pull' and 'docker compose build'."
}

# ---------------------------------------------------------------------------
#  3) Build the Docker image (optional)
# ---------------------------------------------------------------------------
if ($PullLatest) {
    Info "Building the Docker image (docker compose build --pull)..."
    if ((Run-Compose @("build", "--pull")) -ne 0) {
        Exit-WithError "Docker image build failed."
    }
}

# ---------------------------------------------------------------------------
#  4) Run the export
#  A previous export is removed up front so that a failed run can never
#  leave a stale KeePass database behind.
# ---------------------------------------------------------------------------
if (Test-Path -Path $ExportFile -PathType Leaf) { Remove-Item -Force -Path $ExportFile }

if ($Interactive) {
    Info "Starting the interactive Docker export - the prompts appear in this window:"
    $BwEmail = $null
    while (-not $BwEmail) {
        $BwEmail = Read-Host "Bitwarden email"
        if (-not $BwEmail) { Write-Host "  Email must not be empty - try again." }
    }
    $BwMaster = ConvertTo-PlainText (Read-Host -AsSecureString "Bitwarden master password (input is masked)")
    if (-not $BwMaster) { Exit-WithError "The Bitwarden master password must not be empty." }
    $BwTotp = Read-Host "2FA code (visible input; press Enter to skip if not enabled)"
    $CfgDbPass = Get-Cfg "DATABASE_PASSWORD" ""
    $DbPassHint = if ($CfgDbPass) {
        "KeePass database password (masked; press Enter to reuse the one from .env)"
    } else {
        "KeePass database password (masked)"
    }
    $DbPass = ConvertTo-PlainText (Read-Host -AsSecureString $DbPassHint)
    if (-not $DbPass) { $DbPass = $CfgDbPass }
    if (-not $DbPass) { Exit-WithError "No KeePass database password provided - re-run and type it, or set DATABASE_PASSWORD in '.env'." }

    # Hand the secrets to a NON-interactive container as environment variables
    # (entrypoint.sh reads them via `bw login --passwordenv` / `unlock
    # --passwordenv` and clears them before run.py starts). No TTY flags: the
    # container never needs keyboard input, and `docker compose run -it` can
    # hang on some Docker-for-Windows terminals (VS Code / Windows Terminal,
    # ConPTY) because the pseudo-terminal is allocated but the keystrokes are
    # never forwarded. The secrets are cleared from this session right after
    # the run, and from the container environment after the login.
    $runArgs = @("run", "--rm", "--remove-orphans", "-T",
                 "-e", ("BW_EMAIL=" + $BwEmail),
                 "-e", ("BW_MASTER_PASSWORD=" + $BwMaster),
                 "-e", ("DATABASE_PASSWORD=" + $DbPass))
    if ($BwTotp) { $runArgs += @("-e", ("BW_TOTP=" + $BwTotp)) }
    $runArgs += $Service
    if ((Run-Compose $runArgs) -ne 0) {
        Exit-WithError "The Docker export failed."
    }
    # Best-effort scrubbing of the secrets from this PowerShell session.
    $BwMaster = $null
    $BwTotp   = $null
    $DbPass   = $null
} else {
    Info "Starting the Docker export..."
    if ((Run-Compose @("run", "--rm", "--remove-orphans", $Service)) -ne 0) {
        Exit-WithError "The Docker export failed."
    }
}

# ---------------------------------------------------------------------------
#  5) Stamp the fresh export with today's date
# ---------------------------------------------------------------------------
if (-not (Test-Path -Path $ExportFile -PathType Leaf)) {
    Exit-WithError "The export finished but no database was created. Expected file: $ExportFile"
}

$DateStamp   = Get-Date -Format "yyyyMMdd"
$ExportDir   = Split-Path -Path $ExportFile -Parent
$StampedFile = Join-Path $ExportDir ("{0}_bitwarden_export.kdbx" -f $DateStamp)

# Remove a stale file from an earlier run on the same day.
if (Test-Path -Path $StampedFile -PathType Leaf) { Remove-Item -Force -Path $StampedFile }
Rename-Item -Path $ExportFile -NewName (Split-Path -Path $StampedFile -Leaf) -Force
Info ("Export file renamed to {0}" -f (Split-Path -Path $StampedFile -Leaf))

# ---------------------------------------------------------------------------
#  6) Copy the stamped export to every backup folder
# ---------------------------------------------------------------------------
$Copied = $false
if ($BackupStr.Trim() -eq "") {
    Warn "BACKUP_DIRS is empty - no backup copies were made. The export was kept at: $StampedFile"
} else {
    foreach ($dir in $BackupStr.Split(";")) {
        $dir = $dir.Trim()
        if ($dir -eq "") { continue }
        if (-not (Test-Path -Path $dir -PathType Container)) {
            Warn "Backup folder does not exist, skipping: $dir"
            continue
        }
        try {
            Copy-Item -Path $StampedFile -Destination $dir -Force
            Info "Copied to $dir"
            $Copied = $true
        } catch {
            Log-Line "ERROR" "Copy to $dir FAILED: $($_.Exception.Message)"
        }
    }
}

# Only remove the temporary export when at least one copy succeeded,
# otherwise that would delete the only remaining copy of the database.
if ($Copied) {
    Remove-Item -Force -Path $StampedFile
    Info "Temporary export file removed."
} elseif ($BackupStr.Trim() -ne "") {
    Warn "No backup copy succeeded - the export was kept at: $StampedFile"
}

Write-Host ""
Info "Process completed successfully."
if ($WaitForKey) {
    Write-Host ""
    Read-Host "Press Enter to close..."
}
exit 0

