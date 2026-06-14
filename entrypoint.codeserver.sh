#!/bin/sh
# Per-user code-server entrypoint.
# Sets up SSH access (same marvin key pattern as centralcore) from the host
# ~/.ssh mount, then starts code-server with auth disabled (nginx auth_request
# is the gate — the container has no published host port).
set -e

SSH_DIR="$HOME/.ssh"
HOST_SSH="$HOME/.ssh_host"

mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"

# SSH config: route *.ippen.media to the marvin key (path adjusted for $HOME).
cat > "$SSH_DIR/config" <<EOF
Host *.ippen.media
    User marvin
    IdentityFile $SSH_DIR/marvin.key
    StrictHostKeyChecking accept-new
    ConnectTimeout 10
EOF
chmod 600 "$SSH_DIR/config"

# Copy keys from the read-only host ~/.ssh mount.
if [ -d "$HOST_SSH" ]; then
    for KEY in id_rsa id_ed25519 id_ecdsa marvin.key; do
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

mkdir -p "$HOME/workspaces"

exec code-server \
    --auth none \
    --bind-addr 0.0.0.0:8080 \
    --disable-telemetry \
    --disable-update-check \
    "$HOME/workspaces"
