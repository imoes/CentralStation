# Lokale VI für CentralStation: vi-mcp aus dem Debian-Paket (Release github.com/imoes/vi-mcp/releases/tag/v0.14.3).
# Ein Image, zwei Dienste im Compose: vi-mcp (Gedächtnis, Nutzer vi-mcp) und vi-p2p (Netzdienst, Nutzer vi-p2p,
# Profil „netz“) — wie die beiden systemd-Dienste des Pakets, getrennt: der Netzdienst liest fremde Daten und
# erreicht das Gedächtnis nur über das MCP-Werkzeugpaar vi_netz_ausgang/vi_netz_eingang.
FROM ubuntu:24.04

ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG NO_PROXY=""
ARG VI_MCP_DEB=vi-mcp_0.14.3-1_all.deb
ARG VI_MCP_SHA256=59941c90f4d21ab881f885d41de58b50b9a82b491d94b777d295f59916716405
ENV http_proxy=${HTTP_PROXY} https_proxy=${HTTPS_PROXY} no_proxy=${NO_PROXY} \
    HTTP_PROXY=${HTTP_PROXY} HTTPS_PROXY=${HTTPS_PROXY} NO_PROXY=${NO_PROXY}

COPY vi-mcp/pakete/${VI_MCP_DEB} /tmp/vi-mcp.deb

# apt-get ignoriert http_proxy — es braucht Acquire::*::Proxy. Das Paket prüfen wir gegen die festgehaltene Prüfsumme.
# Ohne Menü (VI_MCP_KEIN_MENUE), ohne Empfehlungen: python3-libtorrent nur, wenn das Netz Torrent nutzen soll.
RUN echo "${VI_MCP_SHA256}  /tmp/vi-mcp.deb" | sha256sum -c - \
 && if [ -n "$HTTP_PROXY" ]; then \
        printf 'Acquire::http::Proxy "%s";\nAcquire::https::Proxy "%s";\n' \
            "$HTTP_PROXY" "${HTTPS_PROXY:-$HTTP_PROXY}" > /etc/apt/apt.conf.d/01proxy; \
    fi \
 && apt-get update \
 && DEBIAN_FRONTEND=noninteractive VI_MCP_KEIN_MENUE=1 \
    apt-get install -y --no-install-recommends /tmp/vi-mcp.deb ca-certificates \
 && rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/01proxy /tmp/vi-mcp.deb

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
    VI_MCP_CONFIG=/etc/vi-mcp/config.json VI_P2P_CONFIG=/etc/vi-p2p/config.json PYTHONUNBUFFERED=1

USER vi-mcp
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/gesund', timeout=4)" || exit 1
CMD ["python3", "-m", "vi_mcp"]
