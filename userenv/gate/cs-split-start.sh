#!/bin/sh
# Getrennter Modus (Block 5): Root-Teil des Starts, dann Übergabe an yolo.
#
#   root   → Rechte, Netzwerkregeln, SSH-Schleuse (hält den Schlüssel)
#   agent  → Hermes :8001 (+ Claude/Codex als Kinder) — ohne sudo, ohne neue Privilegien,
#            ohne Schlüssel; Server nur über die Schleuse
#   yolo   → code-server :8080 (Werkbank, Terminal) — unverändert, mit sudo
#
# Fail closed: lassen sich die Netzwerkregeln nicht setzen, startet Hermes nicht (der
# Agent käme sonst an Port 22 und an die Werkbank). code-server startet trotzdem.
set -eu
AH=/home/agent
WS=/home/yolo/workspaces
log() { echo "cs-split: $*"; }

# ── Verzeichnisse und Eigentümer ─────────────────────────────────────────────
mkdir -p "$AH/.claude" "$AH/.hermes" "$AH/pip" "$AH/.codex" "$AH/.cache" \
         /etc/cs-ssh /var/log/cs-gate /run/cs-gate
chmod 700 /etc/cs-ssh /var/log/cs-gate
for d in "$AH/.claude" "$AH/.hermes" "$AH/pip"; do
    # Volumes können von früher yolo (1000) oder root gehören — einmalig umschreiben.
    [ "$(stat -c %u "$d")" = "1001" ] || { chown -R 1001:1001 "$d"; log "chowned $d → agent"; }
done
chown 1001:1001 "$AH" "$AH/.codex" "$AH/.cache"
chmod 700 "$AH"

# Der Agent muss die Claude-Binary (unter yolos ~/.local) ausführen und den Workspace
# erreichen — nur Durchgang (711), kein Auflisten; yolos code-server-Daten bleiben 700.
chmod 711 /home/yolo
for d in /home/yolo/.local /home/yolo/.local/bin /home/yolo/.local/share; do
    [ -d "$d" ] && chmod 711 "$d"
done
[ -d /home/yolo/.local/share/claude ] && chmod -R a+rX /home/yolo/.local/share/claude
# yolos code-server-Daten (Host-Mount): gehören sie noch root (früher als root angelegt),
# einmalig an yolo — sonst startet die Werkbank nicht. Dann nur für yolo lesbar.
CSD=/home/yolo/.local/share/code-server
if [ -d "$CSD" ]; then
    if [ -n "$(find "$CSD" -xdev ! -user 1000 -print -quit 2>/dev/null)" ]; then
        chown -R 1000:1000 "$CSD" && log "chowned $CSD → yolo"
    fi
    chmod 700 "$CSD"
fi

# Workspace: Gruppe cs-work, setgid. Verzeichnisse gruppen-beschreibbar (der Agent legt
# Berichte an); .git und .vscode bewusst NICHT — dort abgelegte Hooks/Tasks liefen sonst
# beim Menschen (yolo, mit sudo).
mkdir -p "$WS"
chgrp cs-work "$WS" && chmod 2775 "$WS"
if [ ! -f "$WS/.cs-split-perms" ]; then
    find "$WS" -xdev \( -name .git -o -name .vscode -o -name ansible \) -prune \
         -o -type d -exec chgrp cs-work {} + -exec chmod g+ws {} + 2>/dev/null || true
    touch "$WS/.cs-split-perms"
    log "workspace group permissions set (cs-work)"
fi

# ── Netzwerkregeln für den Agenten ───────────────────────────────────────────
IPT_OK=1
for rule in "-p tcp --dport 22" "-p tcp --dport 2222" "-p tcp --dport 8080"; do
    # shellcheck disable=SC2086
    iptables -C OUTPUT -m owner --uid-owner 1001 $rule -j REJECT 2>/dev/null \
      || iptables -A OUTPUT -m owner --uid-owner 1001 $rule -j REJECT \
      || IPT_OK=0
done
[ "$IPT_OK" = "1" ] && log "iptables: agent blocked from :22, :2222, :8080" \
                    || log "ERROR: iptables rules could not be set — Hermes will NOT start"

# ── SSH-Schleuse ─────────────────────────────────────────────────────────────
/usr/bin/python3 -I /opt/cs-gate/cs-ssh-gate.py &
for _ in 1 2 3 4 5 6 7 8 9 10; do [ -S /run/cs-gate/gate.sock ] && break; sleep 0.3; done
[ -S /run/cs-gate/gate.sock ] && log "ssh gate up" || log "ERROR: ssh gate did not start"

# ── Hermes als agent ─────────────────────────────────────────────────────────
if [ "$IPT_OK" = "1" ]; then
    [ -f /app/hermes_config.yaml ] && cp /app/hermes_config.yaml "$AH/.hermes/config.yaml" \
        && chown 1001:1001 "$AH/.hermes/config.yaml"
    AGENT_PATH="/usr/local/lib/cs-agent-bin:$AH/pip/venv/bin:/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    AS_AGENT="setpriv --reuid=1001 --regid=1001 --init-groups --no-new-privs --inh-caps=-all --bounding-set=-all"
    AGENT_ENV="HOME=$AH USER=agent LOGNAME=agent SHELL=/bin/bash PATH=$AGENT_PATH \
CLAUDE_CONFIG_DIR=$AH/.claude CODEX_HOME=$AH/.codex DISABLE_AUTOUPDATER=1 CS_AGENT_SPLIT=1"
    # shellcheck disable=SC2086
    [ -x "$AH/pip/venv/bin/python" ] || $AS_AGENT env $AGENT_ENV /usr/bin/python3 -m venv \
        "$AH/pip/venv" --system-site-packages >/dev/null 2>&1 || true
    # shellcheck disable=SC2086
    $AS_AGENT env $AGENT_ENV sh -c 'cd /app && exec /opt/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8001' &
    log "hermes started as agent (pid $!)"
fi

# ── Übergabe an yolo (code-server) ───────────────────────────────────────────
exec setpriv --reuid=1000 --regid=1000 --init-groups \
    env HOME=/home/yolo USER=yolo LOGNAME=yolo CS_SKIP_HERMES=1 /usr/local/bin/cs-entrypoint.sh
