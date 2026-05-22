#!/bin/sh
# Selbstsigniertes TLS-Zertifikat für nginx generieren.
# Für Produktion durch echtes Zertifikat (z.B. von der internen CA) ersetzen.
#
# Verwendung:
#   sh nginx/generate-certs.sh
#   oder: sh nginx/generate-certs.sh centralstation.ippen.media

DOMAIN="${1:-centralstation.ippen.media}"

docker run --rm \
  -v "$(pwd)/nginx/ssl:/ssl" \
  alpine/openssl req -x509 -nodes -newkey rsa:4096 -days 3650 \
  -keyout /ssl/key.pem \
  -out    /ssl/cert.pem \
  -subj "/C=DE/ST=Bayern/L=Muenchen/O=ippen.media/CN=${DOMAIN}" \
  -addext "subjectAltName=DNS:${DOMAIN},DNS:localhost"

echo ""
echo "Zertifikat erstellt: nginx/ssl/cert.pem"
echo "Um ein echtes Zertifikat zu nutzen, cert.pem und key.pem ersetzen"
echo "und 'docker compose restart nginx' ausführen."
