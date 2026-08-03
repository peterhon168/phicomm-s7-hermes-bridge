#!/bin/sh
set -eu

: "${HERMES_SSH_HOST:?set HERMES_SSH_HOST}"
: "${HERMES_SSH_USER:?set HERMES_SSH_USER}"
: "${HERMES_SSH_KEY:?set HERMES_SSH_KEY}"
: "${HERMES_SSH_KNOWN_HOSTS:?set HERMES_SSH_KNOWN_HOSTS}"
: "${HERMES_SCALE_PROJECT_DIR:?set HERMES_SCALE_PROJECT_DIR on the remote host}"

ssh \
  -tt \
  -i "$HERMES_SSH_KEY" \
  -o IdentitiesOnly=yes \
  -o UserKnownHostsFile="$HERMES_SSH_KNOWN_HOSTS" \
  -o StrictHostKeyChecking=yes \
  "$HERMES_SSH_USER@$HERMES_SSH_HOST" \
  "cd '$HERMES_SCALE_PROJECT_DIR' && sudo -- ./deploy/install-with-docker.sh"
