#!/usr/bin/env bash
set -euo pipefail

public_ip=${1:?Provide the public IPv4 address}
python3 -c 'import ipaddress, sys; ipaddress.IPv4Address(sys.argv[1])' "$public_ip"
public_host=${2:-$public_ip}
server_names=$public_ip
cert_name=mateo-ip
certificate_options=(--preferred-profile shortlived --ip-address "$public_ip")
if [[ $public_host != "$public_ip" ]]; then
    [[ $public_host =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*[a-zA-Z0-9]$ ]]
    server_names="$public_ip $public_host"
    cert_name=mateo-hostname
    certificate_options=(-d "$public_host")
fi
cd /srv/mateo/app/deploy/ec2

render_http() {
    sed "s/__SERVER_NAMES__/$server_names/g; s/__PUBLIC_HOST__/$1/g" web-http.conf \
        > /etc/nginx/sites-available/mateo-web-http
}

install -d -m 0755 /var/www/mateo-acme
if [[ -s /etc/letsencrypt/live/$cert_name/fullchain.pem ]]; then
    render_http "$public_host"
else
    render_http "$public_ip"
fi
ln -sfn /etc/nginx/sites-available/mateo-web-http /etc/nginx/sites-enabled/mateo-web-http
nginx -t
systemctl reload nginx

python3 -m venv /srv/mateo/certbot
/srv/mateo/certbot/bin/pip install --disable-pip-version-check 'certbot>=5.4,<6'
certbot=/srv/mateo/certbot/bin/certbot
"$certbot" certonly --non-interactive --agree-tos --register-unsafely-without-email \
    --keep-until-expiring --webroot --webroot-path /var/www/mateo-acme \
    "${certificate_options[@]}" --cert-name "$cert_name"

systemctl enable --now mateo@api.service mateo@guest.service
for attempt in {1..20}; do
    if curl -fsS -o /dev/null http://127.0.0.1:8000/web-access && \
        curl -fsS -o /dev/null http://127.0.0.1:8010/join; then
        break
    fi
    if [[ $attempt == 20 ]]; then
        exit 1
    fi
    sleep 1
done

sed "s/__PUBLIC_HOST__/$public_host/g; s/__CERT_NAME__/$cert_name/g" web-https.conf \
    > /etc/nginx/sites-available/mateo-web-https
ln -sfn /etc/nginx/sites-available/mateo-web-https /etc/nginx/sites-enabled/mateo-web-https
if [[ $public_host != "$public_ip" && -s /etc/letsencrypt/live/mateo-ip/fullchain.pem ]]; then
    sed "s/__PUBLIC_IP__/$public_ip/g; s/__PUBLIC_HOST__/$public_host/g" web-ip-redirect.conf \
        > /etc/nginx/sites-available/mateo-web-ip-redirect
    ln -sfn /etc/nginx/sites-available/mateo-web-ip-redirect /etc/nginx/sites-enabled/mateo-web-ip-redirect
elif [[ -L /etc/nginx/sites-enabled/mateo-web-ip-redirect ]]; then
    unlink /etc/nginx/sites-enabled/mateo-web-ip-redirect
fi
render_http "$public_host"
nginx -t
systemctl reload nginx
install -m 0644 mateo-renew-web.service mateo-renew-web.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now mateo-renew-web.timer
