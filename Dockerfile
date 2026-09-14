# Bitwarden CLI version and integrity check - pinned for reproducible builds and
# to prevent a tampered binary from running with access to the whole vault.
# Downloaded from the versioned GitHub release asset (the vault.bitwarden.com
# CDN ignores the `version` query parameter and would serve the latest build,
# making the SHA-256 check fail instead of pinning the wanted version).
# SHA-256 verified against:
# https://github.com/bitwarden/clients/releases/download/cli-v2026.8.0/bw-linux-2026.8.0.zip
ARG BW_VERSION=2026.8.0
ARG BW_SHA256=367f618e9fcccaac4980ec12c7bafd01df739b5f3cb1af31bc9045cf75eea1d6

FROM python:3.12-slim-bookworm

# ARGs declared before the first FROM are only in scope for the FROM line.
# They must be redeclared inside the stage (without a value, which inherits
# the default declared above) to be usable in the RUN instructions below.
ARG BW_VERSION
ARG BW_SHA256

RUN apt-get update && \
    apt-get install -y --no-install-recommends wget unzip && \
    wget -O "bw.zip" "https://github.com/bitwarden/clients/releases/download/cli-v${BW_VERSION}/bw-linux-${BW_VERSION}.zip" && \
    echo "${BW_SHA256}  bw.zip" | sha256sum -c - && \
    unzip bw.zip && \
    chmod +x ./bw && \
    mv ./bw /usr/local/bin/bw && \
    apt-get purge -y --auto-remove wget unzip && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf bw.zip

WORKDIR /bitwarden-to-keepass

# Run as an unprivileged user: the container must not rely on root privileges
# (a compromised `bw` binary would otherwise inherit them) and it only needs to
# write to the /exports volume. $HOME points at the app user's directory so
# the Bitwarden CLI stores its config there - the docker-compose 'bw-config'
# volume is mounted exactly at that location.
RUN useradd --create-home --shell /bin/bash --uid 1000 appuser && \
    chown appuser:appuser /bitwarden-to-keepass && \
    # Pre-create the Bitwarden CLI config directory as 'appuser'. When a fresh
    # named volume is mounted at a path that does NOT exist in the image,
    # Docker creates that directory (and thus the volume) as ROOT, and the CLI
    # then crashes with EACCES the moment it writes 'data.json'. With the
    # directory present and appuser-owned here, the copy Docker performs into a
    # new volume inherits the appuser ownership.
    mkdir -p "/home/appuser/.config/Bitwarden CLI" && \
    chown appuser:appuser "/home/appuser/.config/Bitwarden CLI"
ENV HOME=/home/appuser

COPY pyproject.toml poetry.lock ./

# Create the virtualenv inside the project (not under $HOME) so `poetry run`
# resolves it independently of the user the container eventually runs as.
RUN pip install --no-cache-dir poetry==2.4.3 && \
    poetry config virtualenvs.in-project true && \
    poetry install && \
    # poetry ran as root while $HOME already pointed at /home/appuser, so its
    # config and cache belong to root; hand the directory back to the runtime
    # user or `poetry run` fails with a permission error later.
    chown -R appuser:appuser /home/appuser /bitwarden-to-keepass/.venv

COPY . .

USER appuser
