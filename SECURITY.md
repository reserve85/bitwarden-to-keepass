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
  command line.
- The container refuses interactive `bw login` / `bw unlock` prompts because a
  non-TTY prompt would echo the master password in clear text. When the
  personal API key is used, the master password is supplied through the
  `BW_PASSWORD` environment variable and handed to the CLI via
  `bw unlock --passwordenv` (no prompt, no echo, not stored by the script).
- Interactive login (`docker compose run -it`) is only accepted when stdin is a
  real terminal. The CLI masks the master password on input; the one-time 2FA
  code is shown as typed (official CLI behavior) but is single-use and expires
  within seconds. Nothing is stored or written to redirected logs; the same
  commands started unattended use the environment-based path instead.
- The Docker image pins the Bitwarden CLI version and verifies its SHA-256.
- gitleaks (pre-commit) and gitleaks + pip-audit (CI) scan for secrets and
  vulnerable dependencies.
- `.env` and `exports/` are excluded from git and the Docker build context.

## Supported versions

The HEAD of `master` is supported. Past releases are not actively maintained;
apply the fixes from later commits if you stay on an older checkout.