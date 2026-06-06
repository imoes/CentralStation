#!/bin/sh
# Set up SSH access from mounted host keys at /root/.ssh_host
set -e

SSH_DIR=/root/.ssh
HOST_SSH=/root/.ssh_host

mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"

# Copy SSH keys from host mount (id_rsa or id_ed25519)
for KEY in id_rsa id_ed25519 id_ecdsa; do
    if [ -f "$HOST_SSH/$KEY" ]; then
        cp "$HOST_SSH/$KEY" "$SSH_DIR/$KEY"
        chmod 600 "$SSH_DIR/$KEY"
    fi
    if [ -f "$HOST_SSH/${KEY}.pub" ]; then
        cp "$HOST_SSH/${KEY}.pub" "$SSH_DIR/${KEY}.pub"
        chmod 644 "$SSH_DIR/${KEY}.pub"
    fi
done

# Copy known_hosts if present (speeds up first connect, not strictly required)
if [ -f "$HOST_SSH/known_hosts" ]; then
    cp "$HOST_SSH/known_hosts" "$SSH_DIR/known_hosts"
    chmod 644 "$SSH_DIR/known_hosts"
fi

# Write SSH config: accept new keys for ippen.media without prompt
# config_centralcore from Dockerfile is the template; merge with any host config
cat "$SSH_DIR/config_centralcore" > "$SSH_DIR/config"
# Append host config if it exists and doesn't conflict (skip Host * blocks from host)
if [ -f "$HOST_SSH/config" ]; then
    echo "" >> "$SSH_DIR/config"
    grep -v "^Host \*" "$HOST_SSH/config" >> "$SSH_DIR/config" 2>/dev/null || true
fi
chmod 600 "$SSH_DIR/config"

exec uvicorn main:app --host 0.0.0.0 --port 8001
