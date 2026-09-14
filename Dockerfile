# Bitwarden CLI version and integrity check - pinned for reproducible builds and
# to prevent a tampered binary from running with access to the whole vault.
# SHA-256 verified against:
# https://github.com/bitwarden/clients/releases/download/cli-v2026.8.0/bw-linux-2026.8.0.zip
ARG BW_VERSION=2026.8.0
ARG BW_SHA256=367f618e9fcccaac4980ec12c7bafd01df739b5f3cb1af31bc9045cf75eea1d6

FROM python:3.12-slim-bookworm

RUN apt-get update && \
    apt-get install -y --no-install-recommends wget unzip && \
    wget -O "bw.zip" "https://vault.bitwarden.com/download/?app=cli&platform=linux&version=${BW_VERSION}" && \
    echo "${BW_SHA256}  bw.zip" | sha256sum -c - && \
    unzip bw.zip && \
    chmod +x ./bw && \
    mv ./bw /usr/local/bin/bw && \
    apt-get purge -y --auto-remove wget unzip && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf bw.zip

WORKDIR /bitwarden-to-keepass
COPY pyproject.toml poetry.lock ./

RUN pip install --no-cache-dir poetry==1.8.5 && \
    poetry install

COPY . .
