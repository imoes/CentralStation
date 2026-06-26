#!/bin/sh
# Unified Werkbank+Hermes entrypoint.
# 1. SSH setup (keys from host mount — user-specific config injected later via configure_ssh API)
# 2. All VS Code / Claude Code extension patches
# 3. Hermes (uvicorn) in background on :8001
# 4. code-server in foreground on :8080
set -e

SSH_DIR="$HOME/.ssh"
HOST_SSH="$HOME/.ssh_host"

mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"

# Default SSH config — user-specific settings are applied later via configure_ssh()
# (called by computer_proxy.py at each session create).  Only write default if no
# user config has been written yet (by a previous configure_ssh call).
if [ ! -f "$SSH_DIR/config" ]; then
    cat > "$SSH_DIR/config" <<EOF
Host *
    StrictHostKeyChecking accept-new
    ConnectTimeout 10
EOF
    chmod 600 "$SSH_DIR/config"
fi

# Copy SSH keys from host ~/.ssh mount (read-only bind mount).
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

mkdir -p "$HOME/workspaces"
mkdir -p "$HOME/.hermes"

# ── Seed baked VS Code extensions into per-user extensions dir ────────
_bundled_ext="/opt/cs-extensions"
_user_ext="$HOME/.local/share/code-server/extensions"
if [ -d "$_bundled_ext" ]; then
    mkdir -p "$_user_ext"
    _seeded=0
    for _src in "$_bundled_ext"/*/; do
        [ -d "$_src" ] || continue
        _name=$(basename "$_src")
        if [ ! -d "$_user_ext/$_name" ]; then
            cp -a "$_src" "$_user_ext/$_name" && _seeded=1 && \
                echo "cs-entrypoint: seeded extension $_name"
        fi
    done
    [ "$_seeded" = "1" ] && rm -f "$_user_ext/extensions.json"
fi
unset _bundled_ext _user_ext _src _name _seeded

# ── Claude Code extension patches ─────────────────────────────────────
_ext_dir="$HOME/.local/share/code-server/extensions"
for _ext in "$_ext_dir"/anthropic.claude-code-*/; do
    [ -d "$_ext" ] || continue
    _js="$_ext/extension.js"
    # CSP font-src: allow data: URIs for codicon font
    if [ -f "$_js" ] && ! grep -qF 'font-src ${e.cspSource} data:' "$_js"; then
        sed -i 's|font-src \${e\.cspSource}`|font-src \${e\.cspSource} data:`|g' "$_js" && \
            echo "cs-entrypoint: patched CSP font-src data: in $_js"
    fi
    # CSP style-src: allow https: for vscode-cdn subdomains
    if [ -f "$_js" ] && grep -qF "style-src \${e.cspSource} 'unsafe-inline'" "$_js" && \
       ! grep -qF "style-src \${e.cspSource} 'unsafe-inline' https:" "$_js"; then
        sed -i "s|style-src \${e\.cspSource} 'unsafe-inline'|style-src \${e.cspSource} 'unsafe-inline' https:|g" "$_js" && \
            echo "cs-entrypoint: patched CSP style-src https: in $_js"
    fi
    # Patch listRemoteSessions to return empty (avoids Cloudflare-blocked requests)
    if [ -f "$_js" ] && grep -qF 'sessions:await this.teleportService.fetchRemoteSessions()' "$_js"; then
        sed -i 's|sessions:await this\.teleportService\.fetchRemoteSessions()|sessions:[]|g' "$_js" && \
            echo "cs-entrypoint: patched listRemoteSessions → empty in $_js"
    fi
    # Inline the webview CSS as <style> block (sidesteps CSP/SW resource URL issues)
    if [ -f "$_js" ] && grep -qF '<link href="${l}" rel="stylesheet">' "$_js"; then
        "/usr/lib/code-server/lib/node" -e '
            const fs=require("fs"), p=process.argv[1];
            let s=fs.readFileSync(p,"utf8");
            s=s.replace(
                "<link href=\"${l}\" rel=\"stylesheet\">",
                "<style>${(()=>{try{return require(\"fs\").readFileSync(c.fsPath,\"utf8\")}catch(_){return\"\"}})()}</style>"
            );
            fs.writeFileSync(p,s);
        ' "$_js" && echo "cs-entrypoint: inlined webview CSS as <style> in $_js"
    fi
    # Force manual OAuth flow (loopback callback unreachable from user's browser)
    if [ -f "$_js" ] && ! grep -qF '/* cs-oauth-manual-patch */' "$_js"; then
        "/usr/lib/code-server/lib/node" -e '
            const fs=require("fs"),p=process.argv[1];
            let s=fs.readFileSync(p,"utf8");
            const dm=s.match(/\{manualUrl:([a-z]),automaticUrl:([a-z])\}/);
            if(!dm){process.exit(0);}
            const mu=dm[1],au=dm[2];
            const ctxKey="type:\"auth_url\",url:"+mu+",method:";
            const toReplace="this.openURL("+au+")";
            const patched="this.openURL("+mu+")";
            const ci=s.indexOf(ctxKey);
            if(ci<0||!s.includes(toReplace)){process.exit(0);}
            const ce=s.indexOf("}catch(",ci)+1;
            const sec=s.slice(ci,ce);
            if(!sec.includes(toReplace)){process.exit(0);}
            s=s.slice(0,ci)+sec.replace(toReplace,patched)+s.slice(ce)
             +"\n/* cs-oauth-manual-patch */";
            fs.writeFileSync(p,s);
        ' "$_js" && echo "cs-entrypoint: patched OAuth to manual flow in $_js"
    fi
done
unset _ext_dir _ext _js

# ── navigator shim for extension host ─────────────────────────────────
_ep_js="/usr/lib/code-server/lib/vscode/out/vs/workbench/api/node/extensionHostProcess.js"
if [ -f "$_ep_js" ] && grep -qF 'vscode-extensions/navigator' "$_ep_js" && \
   ! grep -qF 'userAgent:"node"' "$_ep_js"; then
    sed -i 's|get:()=>{ea(new Zs("navigator is now a global in nodejs, please see https://aka.ms/vscode-extensions/navigator for additional info on this error."))}|get:()=>({userAgent:"node",platform:process.platform,language:"en-US",languages:["en-US"],onLine:!0,hardwareConcurrency:2,cookieEnabled:!1,appName:"Netscape",appVersion:"5.0",product:"Gecko"})|g' "$_ep_js" && \
        echo "cs-entrypoint: patched navigator shim in extensionHostProcess.js"
fi
unset _ep_js

# ── Claude Code managed-settings ──────────────────────────────────────
_managed="$HOME/.claude/managed-settings.json"
if ! grep -q '"disableRemoteControl"' "$_managed" 2>/dev/null; then
    mkdir -p "$HOME/.claude"
    printf '{\n  "disableRemoteControl": true,\n  "autoUploadSessions": false\n}\n' \
        > "$_managed"
    echo "cs-entrypoint: wrote Claude Code managed-settings.json"
fi
unset _managed

# ── Hermes on :8001 (background) ─────────────────────────────────────
# Per-user hermes_config.yaml is bind-mounted to /app/hermes_config.yaml by
# userenv_manager.py (written from the user's DB connectors). We always copy it
# to ~/.hermes/config.yaml so it takes precedence over any stale config inside
# the hermes-state volume (which Docker volume mounts shadow file bind-mounts).
# Fallback: generate minimal config from env var if no per-user file is mounted.
_hermes_cfg="$HOME/.hermes/config.yaml"
if [ -f "/app/hermes_config.yaml" ]; then
    cp /app/hermes_config.yaml "$_hermes_cfg"
    echo "cs-entrypoint: loaded per-user hermes_config.yaml ($(grep -c 'transport:' /app/hermes_config.yaml) servers)"
else
    _backend_url="${CENTRALSTATION_BACKEND_URL:-http://backend:8000}"
    cat > "$_hermes_cfg" <<YAML
mcp_servers:
  centralstation:
    transport: sse
    url: ${_backend_url}/api/mcp/sse
YAML
    echo "cs-entrypoint: wrote fallback hermes_config.yaml (backend=${_backend_url})"
    unset _backend_url
fi
unset _hermes_cfg

cd /app && uvicorn main:app --host 0.0.0.0 --port 8001 &
echo "cs-entrypoint: hermes started on :8001 (pid $!)"

# ── code-server in foreground on :8080 ────────────────────────────────
exec code-server \
    --auth none \
    --bind-addr 0.0.0.0:8080 \
    --disable-telemetry \
    --disable-update-check \
    "$HOME/workspaces"
