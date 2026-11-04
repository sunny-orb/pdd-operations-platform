#!/bin/sh
set -eu
test -s /run/secrets/web_htpasswd
mkdir -p /run/pdd
cp /run/secrets/web_htpasswd /run/pdd/htpasswd
chown nginx:nginx /run/pdd/htpasswd
chmod 600 /run/pdd/htpasswd
exec /docker-entrypoint.sh "$@"
