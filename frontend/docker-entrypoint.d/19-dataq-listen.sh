#!/bin/sh
# Writes the server's `listen` directives before nginx starts.
#
# Unset (the default): plain HTTP on 8080, for a deployment whose edge terminates TLS.
# DATAQ_TLS_CERT + DATAQ_TLS_KEY set: this container terminates TLS itself on the same port.
set -eu

out=/etc/nginx/dataq-listen.conf
cert="${DATAQ_TLS_CERT:-}"
key="${DATAQ_TLS_KEY:-}"

if [ -z "$cert" ] && [ -z "$key" ]; then
    printf 'listen 8080;\n' > "$out"
    exit 0
fi

if [ -z "$cert" ] || [ -z "$key" ]; then
    echo "dataq: set both DATAQ_TLS_CERT and DATAQ_TLS_KEY, or neither" >&2
    exit 1
fi
for f in "$cert" "$key"; do
    if [ ! -r "$f" ]; then
        echo "dataq: TLS file $f is missing or unreadable" >&2
        exit 1
    fi
done

cat > "$out" <<CONF
listen 8080 ssl;
ssl_certificate $cert;
ssl_certificate_key $key;
ssl_protocols TLSv1.2 TLSv1.3;
# nginx answers a plain-HTTP request on a TLS port with its own 497; send the browser to the
# same host and port over HTTPS instead.
error_page 497 =308 https://\$http_host\$request_uri;
CONF
