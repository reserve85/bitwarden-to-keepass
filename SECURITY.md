# Security Policy

This project exports a Bitwarden vault (including passwords, TOTP seeds and
attachments) into a KeePass database. Treat any report of secret leakage,
injection or credential mishandling with high priority.

## Reporting a vulnerability

Do **not** open a public GitHub issue for vulnerabilities. Instead, report the
finding privately - for example by emailing the maintainer or opening a
*private* advisory via GitHub's "Security" tab on the repository.

Please include:

- affected version / commit
- a description of the issue and its impact
- steps to reproduce (without real secrets)

## Known historical issue (IMPORTANT)

Older commits of this repository (before `66ee3f6`, "remove secret leakage")
tracked a **real `DATABASE_PASSWORD` in the `.env` file** in git history. The
file itself is now gitignored and replaced with `.env.example`, but the value
is still reachable in the git history of any clone.

If you ever used a KeePass database password that was committed to git:

1. **Rotate the password** - change it in KeePass (Database -> Change Master Key)
   or use a fresh database.
2. **Purge history** in your clone and on GitHub, e.g.:

   ```sh
   git filter-repo --invert-paths --path .env
   git push origin --force --all
   ```

   (or use BFG Repo-Cleaner), then force-push all branches and tags.
3. Check GitHub for forks/PRs that may still contain the leaked value.

## Secure defaults

- Secrets (`BW_SESSION`, `BW_PASSWORD`, `DATABASE_PASSWORD`, API keys) are
  only ever read from the environment or a hidden prompt - never from the
  command line. The single exception is the *one-time 2FA code* that an
  interactive login has to hand to the CLI (`bw login --code ...`): the CLI
  accepts it nowhere else. It is only used for interactive runs, is
  single-use, expires within seconds and is never persisted.
- The container refuses interactive `bw login` / `bw unlock` prompts because a
  non-TTY prompt would echo the master password in clear text. When the
  personal API key is used, the master password is supplied through the
  `BW_PASSWORD` environment variable and handed to the CLI via
  `bw unlock --passwordenv` (no prompt, no echo, not stored by the script).
- The interactive export (`create_backup.ps1 --interactive`, or auto-detected
  when credentials are missing) never prompts inside the container: the
  PowerShell script collects the Bitwarden email, master password, 2FA code
  (if enabled) and the KeePass database password with masked prompts in its
  own console window and passes them to a *non-interactive* container as
  environment variables (`docker compose run -T -e ...`), which the entrypoint
  reads via `bw login --passwordenv --raw` (no echo, no interactive CLI
  prompt; a separate `bw unlock --raw` step would trip the known
  `bitwarden/clients#20720` bug that leaves the vault locked) and then `unset`s
  before the export script runs. A manual `docker compose run -it` is still
  accepted when stdin is a real terminal (prompts go to stderr - the
  container's stdout may not be a terminal on some Docker-for-Windows hosts -
  and the master password goes through `--passwordenv`; the one-time 2FA code
  is prompted by the CLI, shown as typed, but is single-use and expires within
  seconds). The no-TTY design exists because on some Docker-for-Windows
  terminals (VS Code / Windows Terminal, ConPTY) `-it` allocates a
  pseudo-terminal but never forwards the host keystrokes, which makes
  in-container prompts hang. Nothing is stored in redirected logs; unattended
  runs use the environment-based (API key / session) path instead.
- The Docker image pins the Bitwarden CLI version and verifies its SHA-256.
- gitleaks (pre-commit) and gitleaks + pip-audit (CI) scan for secrets and
  vulnerable dependencies.
- `.env` and `exports/` are excluded from git and the Docker build context.

## Supported versions

The HEAD of `master` is supported. Past releases are not actively maintained;
apply the fixes from later commits if you stay on an older checkout.