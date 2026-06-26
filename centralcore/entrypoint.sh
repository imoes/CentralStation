#!/bin/sh
# Set up SSH access from mounted host keys at /root/.ssh_host
# SSH config comes from the project: centralcore/ssh_config (mounted as /root/.ssh_project_config)
set -e

SSH_DIR=/root/.ssh
HOST_SSH=/root/.ssh_host

mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"

# Project-local SSH config (centralcore/ssh_config) is the primary config.
# Falls back to a minimal default if the mount is missing.
if [ -f /root/.ssh_project_config ]; then
    cp /root/.ssh_project_config "$SSH_DIR/config"
else
    printf 'Host *\n    StrictHostKeyChecking accept-new\n    ConnectTimeout 10\n' \
        > "$SSH_DIR/config"
fi
chmod 600 "$SSH_DIR/config"

# Copy SSH keys from host ~/.ssh mount
if [ -d "$HOST_SSH" ]; then
    for KEY in id_rsa id_ed25519 id_ecdsa; do
        if [ -f "$HOST_SSH/$KEY" ]; then
            cp "$HOST_SSH/$KEY" "$SSH_DIR/$KEY"
            chmod 600 "$SSH_DIR/$KEY"
        fi
    done
    if [ -f "$HOST_SSH/known_hosts" ]; then
        cp "$HOST_SSH/known_hosts" "$SSH_DIR/known_hosts"
        chmod 644 "$SSH_DIR/known_hosts"
    fi
fi

exec uvicorn main:app --host 0.0.0.0 --port 8001
