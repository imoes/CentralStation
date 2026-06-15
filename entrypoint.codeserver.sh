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

# Patch the Claude Code extension so the bundled codicon font (data:font/ttf;base64)
# is not blocked by CSP. The extension builds font-src dynamically in extension.js
# (not in static HTML), so we patch extension.js directly. Idempotent — already-
# patched files are skipped via the grep guard.
_ext_dir="$HOME/.local/share/code-server/extensions"
for _ext in "$_ext_dir"/anthropic.claude-code-*/; do
    [ -d "$_ext" ] || continue
    _js="$_ext/extension.js"
    if [ -f "$_js" ] && ! grep -qF 'font-src ${e.cspSource} data:' "$_js"; then
        sed -i 's|font-src \${e\.cspSource}`|font-src \${e\.cspSource} data:`|g' "$_js" && \
            echo "cs-entrypoint: patched CSP font-src data: in $_js"
    fi
done
unset _ext_dir _ext _js

exec code-server \
    --auth none \
    --bind-addr 0.0.0.0:8080 \
    --disable-telemetry \
    --disable-update-check \
    "$HOME/workspaces"
