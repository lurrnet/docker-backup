#!/bin/sh
set -eu

CONFIG="${DOCKBACK_PULL_CONFIG:-${1:-./dockback-pull.conf}}"
if [ ! -f "$CONFIG" ]; then
  echo "Config not found: $CONFIG" >&2
  exit 2
fi

# shellcheck disable=SC1090
. "$CONFIG"

: "${REMOTE_HOST:?REMOTE_HOST is required}"
: "${REMOTE_USER:?REMOTE_USER is required}"
: "${REMOTE_STAGING:?REMOTE_STAGING is required}"
: "${REMOTE_ROOT:?REMOTE_ROOT is required}"
: "${LOCAL_ROOT:?LOCAL_ROOT is required}"

SSH_PORT="${SSH_PORT:-22}"
SSH_KEY="${SSH_KEY:-}"
RESTIC_ENABLE="${RESTIC_ENABLE:-false}"
RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-}"
RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-}"
KEEP_DAILY="${KEEP_DAILY:-7}"
KEEP_WEEKLY="${KEEP_WEEKLY:-4}"
KEEP_MONTHLY="${KEEP_MONTHLY:-12}"\nREMOTE_KEEP_STAGING="${REMOTE_KEEP_STAGING:-2}"

SSH_ARGS="-p $SSH_PORT -o BatchMode=yes"
if [ -n "$SSH_KEY" ]; then
  SSH_ARGS="$SSH_ARGS -i $SSH_KEY"
fi

REMOTE="$REMOTE_USER@$REMOTE_HOST"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TMP="$LOCAL_ROOT/.incoming-$STAMP"
CURRENT="$LOCAL_ROOT/current"

mkdir -p "$LOCAL_ROOT" "$TMP"

echo "[dockback-pull] triggering remote backup"
ssh $SSH_ARGS "$REMOTE"   "dockback --root '$REMOTE_ROOT' --staging '$REMOTE_STAGING' backup --all"

echo "[dockback-pull] pulling staging data"
rsync -aH --partial --delete-after   -e "ssh $SSH_ARGS"   "$REMOTE:$REMOTE_STAGING/"   "$TMP/"

rm -rf "$CURRENT"
mv "$TMP" "$CURRENT"

if [ "$RESTIC_ENABLE" = "true" ]; then
  : "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required when RESTIC_ENABLE=true}"
  : "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE is required when RESTIC_ENABLE=true}"

  echo "[dockback-pull] creating restic snapshot"
  RESTIC_REPOSITORY="$RESTIC_REPOSITORY"   RESTIC_PASSWORD_FILE="$RESTIC_PASSWORD_FILE"   restic backup "$CURRENT"

  echo "[dockback-pull] applying retention"
  RESTIC_REPOSITORY="$RESTIC_REPOSITORY"   RESTIC_PASSWORD_FILE="$RESTIC_PASSWORD_FILE"   restic forget     --keep-daily "$KEEP_DAILY"     --keep-weekly "$KEEP_WEEKLY"     --keep-monthly "$KEEP_MONTHLY"     --prune
fi

echo "[dockback-pull] pruning old remote staging after successful pull"
ssh $SSH_ARGS "$REMOTE" \
  "dockback --staging '$REMOTE_STAGING' prune --keep '$REMOTE_KEEP_STAGING'"

echo "[dockback-pull] completed successfully"
