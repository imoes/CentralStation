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
    # CSP font-src: allow data: URIs for codicon font (embedded as base64 in webview CSS)
    if [ -f "$_js" ] && ! grep -qF 'font-src ${e.cspSource} data:' "$_js"; then
        sed -i 's|font-src \${e\.cspSource}`|font-src \${e\.cspSource} data:`|g' "$_js" && \
            echo "cs-entrypoint: patched CSP font-src data: in $_js"
    fi
    # CSP style-src: cspSource = 'self' https://*.vscode-cdn.net, but the actual
    # webview CSS URL is at https://uuid+localhost.vscode-resource.vscode-cdn.net
    # (two subdomain levels). CSP wildcards only match one level, so the CSS is
    # blocked and CSS-module class names have no effect → huge ✓, concatenated text.
    # Adding https: allows any HTTPS stylesheet (same approach as font-src data:).
    if [ -f "$_js" ] && grep -qF "style-src \${e.cspSource} 'unsafe-inline'" "$_js" && \
       ! grep -qF "style-src \${e.cspSource} 'unsafe-inline' https:" "$_js"; then
        sed -i "s|style-src \${e\.cspSource} 'unsafe-inline'|style-src \${e.cspSource} 'unsafe-inline' https:|g" "$_js" && \
            echo "cs-entrypoint: patched CSP style-src https: in $_js"
    fi
    # Remote sessions: disableRemoteControl in managed-settings is defined but never
    # checked in the listRemoteSessions handler — it always calls fetchRemoteSessions()
    # which connects to claude.ai. Cloudflare blocks Node.js requests with 403, causing
    # "Failed to connect to remote server" spam. Patch to return empty list immediately.
    if [ -f "$_js" ] && grep -qF 'sessions:await this.teleportService.fetchRemoteSessions()' "$_js"; then
        sed -i 's|sessions:await this\.teleportService\.fetchRemoteSessions()|sessions:[]|g' "$_js" && \
            echo "cs-entrypoint: patched listRemoteSessions → empty in $_js"
    fi
    # Inline the webview CSS. The chat panel links its stylesheet via
    # <link href="${asWebviewUri(index.css)}">, which the browser loads as an external
    # vscode-resource URL routed through the webview service worker. In this proxied
    # subpath setup that resource never applies (SW cache / CSP host match / cross-origin
    # iframe), so CSS-module classes render unstyled — the mode-picker shows a giant ✓ and
    # concatenated labels. Inlining the file contents as a <style> block sidesteps the SW,
    # CSP host matching, resource URL scheme and caching entirely; CSP already allows
    # 'unsafe-inline' for styles, so the rules always apply. Idempotent: once the <link>
    # is gone the guard skips. node is used for an exact (non-regex) string replace.
    if [ -f "$_js" ] && grep -qF '<link href="${l}" rel="stylesheet">' "$_js"; then
        # code-server ships its own node; it is not on PATH in this entrypoint shell.
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
    # Force manual OAuth flow: open manualUrl (platform.claude.com/oauth/code/callback)
    # instead of automaticUrl (http://localhost:PORT/callback — loopback, unreachable from
    # the user's browser to the container). After auth the user copies a CODE#STATE string
    # from platform.claude.com and pastes it into the extension's webview input.
    # Variable names are extracted dynamically from the minified destructuring so the patch
    # survives version updates that rename single-letter variables.
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
        ' "$_js" && echo "cs-entrypoint: patched OAuth to manual flow (manualUrl) in $_js"
    fi
done
unset _ext_dir _ext _js

# Patch the code-server extension host so accessing `navigator` in the Node.js
# extension host returns a minimal browser-like object instead of throwing
# PendingMigrationError. Claude Code 2.1+ accesses navigator at module init time;
# without this shim the extension loads with errors and some UI state is broken.
# Idempotent — the grep guard prevents double-patching.
_ep_js="/usr/lib/code-server/lib/vscode/out/vs/workbench/api/node/extensionHostProcess.js"
if [ -f "$_ep_js" ] && grep -qF 'vscode-extensions/navigator' "$_ep_js" && \
   ! grep -qF 'userAgent:"node"' "$_ep_js"; then
    sed -i 's|get:()=>{ea(new Zs("navigator is now a global in nodejs, please see https://aka.ms/vscode-extensions/navigator for additional info on this error."))}|get:()=>({userAgent:"node",platform:process.platform,language:"en-US",languages:["en-US"],onLine:!0,hardwareConcurrency:2,cookieEnabled:!1,appName:"Netscape",appVersion:"5.0",product:"Gecko"})|g' "$_ep_js" && \
        echo "cs-entrypoint: patched navigator shim in extensionHostProcess.js"
fi
unset _ep_js

# Disable Claude Code remote-control / remote-sessions feature.
# In a containerised code-server the extension host cannot reach claude.ai
# (Cloudflare blocks non-browser requests with 403). Without this flag the
# extension calls fetchRemoteSessions on every panel open → "Failed to connect
# to remote server" error spam. autoUploadSessions=false prevents session mirroring.
_managed="$HOME/.claude/managed-settings.json"
if ! grep -q '"disableRemoteControl"' "$_managed" 2>/dev/null; then
    mkdir -p "$HOME/.claude"
    printf '{\n  "disableRemoteControl": true,\n  "autoUploadSessions": false\n}\n' \
        > "$_managed"
    echo "cs-entrypoint: wrote Claude Code managed-settings.json"
fi
unset _managed

exec code-server \
    --auth none \
    --bind-addr 0.0.0.0:8080 \
    --disable-telemetry \
    --disable-update-check \
    "$HOME/workspaces"
