# Lokale VI für CentralStation: vi-mcp aus dem signierten apt-Archiv (https://imoes.github.io/vi-mcp-packages/,
# Release github.com/imoes/vi-mcp/releases/tag/v0.62.5). Version fest gepinnt; vi-mcp/aktualisieren.sh setzt sie.
# Ein Image, zwei Dienste im Compose: vi-mcp (Gedächtnis, Nutzer vi-mcp) und vi-p2p (Netzdienst, Nutzer vi-p2p,
# Profil „netz“) — wie die beiden systemd-Dienste des Pakets, getrennt: der Netzdienst liest fremde Daten und
# erreicht das Gedächtnis nur über das MCP-Werkzeugpaar vi_netz_ausgang/vi_netz_eingang.
# Das Backend holt den Konsolen-Hook aus DIESEM Image (backend/Dockerfile, Stufe vi-paket) — eine Quelle.
FROM ubuntu:24.04

ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG NO_PROXY=""
ARG VI_MCP_VERSION=0.62.5-1
ARG VI_MCP_REPO=https://imoes.github.io/vi-mcp-packages/apt
#: Fingerabdruck des Archiv-Schlüssels (ed25519, 11.11.2026–10.11.2029), verglichen mit deploy/repo-key.asc im
#: vi-mcp-Repo am 13.11.2026. Der Schlüssel liegt fest im Repo (vi-mcp/repo-schluessel.gpg), nie aus dem Download.
ARG VI_MCP_REPO_FP=4598315414EBF86CABD322D9730B75BFAC3036B7
ENV http_proxy=${HTTP_PROXY} https_proxy=${HTTPS_PROXY} no_proxy=${NO_PROXY} \
    HTTP_PROXY=${HTTP_PROXY} HTTPS_PROXY=${HTTPS_PROXY} NO_PROXY=${NO_PROXY}

COPY vi-mcp/repo-schluessel.gpg /usr/share/keyrings/vi-mcp.gpg

# apt-get ignoriert http_proxy — es braucht Acquire::*::Proxy. Erst ca-certificates und gpg (für das https-Archiv und
# die Prüfung des Schlüssels), dann die Quelle, dann vi-mcp in genau der gepinnten Version.
# Ohne Menü (VI_MCP_KEIN_MENUE), ohne Empfehlungen: python3-libtorrent nur, wenn das Netz Torrent nutzen soll.
# clingo (vi_logic, ab 0.62.0) in ein eigenes Verzeichnis im Image — /var/lib/vi-mcp ist ein Volume und würde es
# verdecken; vi-mcp findet es zur Laufzeit über VI_MCP_EXTRA_PYTHON (ENV unten). pip wird danach wieder entfernt.
# Die Variable auch beim Installieren: bis 0.62.3 prüfte `logic install --target` nur sie und die Standardorte
# („still not importable“); ab 0.62.4 behoben (vi-mcp 1977805), schadet aber nicht.
RUN if [ -n "$HTTP_PROXY" ]; then \
        printf 'Acquire::http::Proxy "%s";\nAcquire::https::Proxy "%s";\n' \
            "$HTTP_PROXY" "${HTTPS_PROXY:-$HTTP_PROXY}" > /etc/apt/apt.conf.d/01proxy; \
    fi \
 && apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates gpg \
 && FP=$(gpg --show-keys --with-colons /usr/share/keyrings/vi-mcp.gpg | awk -F: '/^fpr/{print $10; exit}') \
 && [ "$FP" = "$VI_MCP_REPO_FP" ] || { echo "Archiv-Schlüssel hat Fingerabdruck $FP, erwartet $VI_MCP_REPO_FP"; exit 1; } \
 && chmod 0644 /usr/share/keyrings/vi-mcp.gpg \
 && printf 'Types: deb\nURIs: %s\nSuites: stable\nComponents: main\nSigned-By: /usr/share/keyrings/vi-mcp.gpg\n' \
        "$VI_MCP_REPO" > /etc/apt/sources.list.d/vi-mcp.sources \
 && apt-get update \
 && DEBIAN_FRONTEND=noninteractive VI_MCP_KEIN_MENUE=1 \
    apt-get install -y --no-install-recommends "vi-mcp=${VI_MCP_VERSION}" python3-pip \
 && VI_MCP_EXTRA_PYTHON=/opt/vi-mcp-python vi-mcp logic install --target /opt/vi-mcp-python \
 && DEBIAN_FRONTEND=noninteractive apt-get purge -y --auto-remove python3-pip gpg \
 && rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/01proxy /root/.cache

# Im Container lauscht vi-mcp im Compose-Netz (nicht auf 127.0.0.1): nur Backend und vi-p2p erreichen ihn,
# kein Port wird nach außen veröffentlicht. Rechte der Paket-Konfiguration bleiben (root:vi-mcp 0640).
RUN python3 - <<'PY'
import json, os
p = "/etc/vi-mcp/config.json"
k = json.load(open(p))
k["bind"] = ["0.0.0.0"]
st = os.stat(p)
open(p, "w").write(json.dumps(k, indent=2, ensure_ascii=False) + "\n")
os.chmod(p, st.st_mode & 0o777)
PY

# Laufzeit ohne Build-Proxy; der Netzdienst bekommt seinen Proxy, falls nötig, über das Override.
ENV http_proxy= https_proxy= HTTP_PROXY= HTTPS_PROXY= \
    VI_MCP_CONFIG=/etc/vi-mcp/config.json VI_P2P_CONFIG=/etc/vi-p2p/config.json PYTHONUNBUFFERED=1 \
    VI_MCP_EXTRA_PYTHON=/opt/vi-mcp-python

USER vi-mcp
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/gesund', timeout=4)" || exit 1
CMD ["python3", "-m", "vi_mcp"]
