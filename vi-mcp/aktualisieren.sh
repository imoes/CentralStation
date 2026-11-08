#!/bin/bash
# Lokale VI (vi-mcp) auf ein GitHub-Release aktualisieren — Ablauf nach Absprache mit der vi-mcp-Session (08.11.2026).
#
#   vi-mcp/aktualisieren.sh 0.15.1        # Version ohne „v“
#
# 1. Release-.deb + SHA256SUMS holen und prüfen (später zusätzlich die Signatur, sobald es einen Release-Schlüssel gibt)
# 2. Volumes von vi-mcp und vi-p2p sichern (Dienste dafür kurz angehalten: SQLite mit WAL, ein Schreiber)
# 3. Paket in vi-mcp/pakete/ tauschen, Dockerfile-ARGs und Image-Tag setzen
# 4. Image bauen, vi-mcp und vi-p2p neu erstellen
# 5. Prüfen: /gesund nennt die neue Version, `vi-mcp netz status` ohne Verstöße
# Das Backend (Hook der Konsole kommt beim Bau aus dem Paket) baut das Skript NICHT neu — Neustart-Schutz:
# es nennt nur, ob sich der Hook geändert hat. Committen tut das Skript nicht.
set -euo pipefail
cd "$(dirname "$0")/.."
V="${1:?Version angeben, z. B. 0.15.1}"
V="${V#v}"
REPO=imoes/vi-mcp
DEB="vi-mcp_${V}-1_all.deb"
SICHERUNG="${VI_SICHERUNG:-$HOME/vi-mcp-sicherung}"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

echo "== 1. Release v$V holen und prüfen"
gh release download "v$V" -R "$REPO" -D "$TMP" -p "$DEB" -p SHA256SUMS
(cd "$TMP" && sha256sum -c --ignore-missing SHA256SUMS) | grep -q "$DEB: OK" || { echo "ABBRUCH: Prüfsumme passt nicht"; exit 1; }
if [ -f vi-mcp/release-schluessel.pub ]; then
    echo "ABBRUCH: Release-Schlüssel liegt vor, die Signaturprüfung ist aber noch nicht gebaut"; exit 1
fi
H=$(sha256sum "$TMP/$DEB" | cut -d' ' -f1)
ALT=$(ls vi-mcp/pakete/vi-mcp_*_all.deb 2>/dev/null | head -1 || true)
echo "   $DEB  sha256 $H  (bisher: ${ALT:-keins})"

hook() { dpkg-deb --fsys-tarfile "$1" | tar -xO ./usr/share/vi-mcp/hook/vi_hook.py | sha256sum | cut -d' ' -f1; }
HOOK_NEU=$(hook "$TMP/$DEB"); HOOK_ALT=$([ -n "$ALT" ] && hook "$ALT" || echo -)

echo "== 2. Volumes sichern nach $SICHERUNG"
mkdir -p "$SICHERUNG"; chmod 700 "$SICHERUNG"
IMG=$(docker inspect -f '{{.Config.Image}}' centralstation-vi-mcp-1)
docker compose --profile netz stop vi-p2p vi-mcp >/dev/null
STEMPEL=$(date -u +%Y%m%dT%H%M%SZ)
for vol in vi-mcp-daten vi-mcp-konfig vi-p2p-daten vi-p2p-konfig; do
    docker run --rm -u 0 --entrypoint tar -v "centralstation_${vol}:/q:ro" -v "$SICHERUNG:/z" "$IMG" \
        czf "/z/${vol}-${STEMPEL}.tgz" -C /q .
done
ls -l "$SICHERUNG"/*-"$STEMPEL".tgz | awk '{print "   " $5, $NF}'

echo "== 3. Paket tauschen"
[ -n "$ALT" ] && git rm -q --cached "$ALT" 2>/dev/null; rm -f vi-mcp/pakete/vi-mcp_*_all.deb
mkdir -p vi-mcp/pakete; cp "$TMP/$DEB" vi-mcp/pakete/
sed -i -e "s/^ARG VI_MCP_DEB=.*/ARG VI_MCP_DEB=$DEB/" -e "s/^ARG VI_MCP_SHA256=.*/ARG VI_MCP_SHA256=$H/" \
       -e "s|releases/tag/v[0-9.]*|releases/tag/v$V|" vi-mcp/Dockerfile
sed -i -E "s/centralstation-vi-mcp:[0-9.]+/centralstation-vi-mcp:$V/g" docker-compose.yml

echo "== 4. Bauen und neu erstellen"
timeout 900 docker compose build vi-mcp 2>&1 | grep -E "ERROR|Built" | tail -2
docker compose --profile netz up -d vi-mcp vi-p2p 2>&1 | tail -2

echo "== 5. Prüfen"
for i in $(seq 1 30); do
    G=$(docker compose exec -T vi-mcp python3 -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8765/gesund',timeout=3).read().decode())" 2>/dev/null || true)
    [ -n "$G" ] && break; sleep 2
done
echo "   /gesund: ${G:-keine Antwort}"
case "$G" in *"$V"*) ;; *) echo "FEHLER: /gesund nennt nicht $V — Sicherung in $SICHERUNG (*-$STEMPEL.tgz)"; exit 1;; esac
sleep 15
docker compose --profile netz exec -T vi-p2p vi-mcp netz status | tail -1 | sed 's/^/   /'
if [ "$HOOK_NEU" != "$HOOK_ALT" ]; then
    echo "HINWEIS: Hook der Konsole hat sich geändert — Backend neu bauen (Deploy mit Neustart-Schutz), dann Konsolen-Sitzungen neu starten."
else
    echo "   Hook der Konsole unverändert — Backend muss nicht neu gebaut werden."
fi
echo "Fertig. Noch committen: git add vi-mcp docker-compose.yml && git commit"
