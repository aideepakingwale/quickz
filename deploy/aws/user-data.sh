#!/bin/bash
# Paste into "Advanced details > User data" when launching an Ubuntu 24.04 (arm64) instance, after editing the values.
# (quickz-ec2.yaml in this folder does the same thing for you from a form.)
DOMAIN=""                                      # your own domain pointing at this server, e.g. quiz.example.com; or leave empty and use DuckDNS:
DUCKDNS_NAME="myquiz"                          # the free name you created at duckdns.org (without .duckdns.org)
DUCKDNS_TOKEN=""                               # the token shown on duckdns.org
SETUP_TOKEN=""                                 # a long random string you type once to create the Super Admin (empty = generated)
BACKUP_BUCKET=""                               # optional S3 bucket name for off-server backups
REPO_URL="https://github.com/OWNER/REPO.git"   # private repo: https://x-access-token:TOKEN@github.com/OWNER/REPO.git
REPO_BRANCH="main"

export DOMAIN DUCKDNS_NAME DUCKDNS_TOKEN SETUP_TOKEN BACKUP_BUCKET
apt-get update -y && apt-get install -y git
git clone --depth 1 -b "$REPO_BRANCH" "$REPO_URL" /opt/quickz
bash /opt/quickz/deploy/aws/bootstrap.sh
