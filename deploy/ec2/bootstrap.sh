#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

archive=${1:?Provide the path to the deployment archive}
cd /srv
apt-get -o DPkg::Lock::Timeout=240 update
apt-get -o DPkg::Lock::Timeout=240 install -y nginx python3-venv ca-certificates curl zstd git

if ! id mateo >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/mateo --shell /usr/sbin/nologin mateo
fi
install -d -o mateo -g mateo -m 0750 /var/lib/mateo
install -d -m 0755 /srv/mateo/app
tar --no-same-owner -xzf "$archive" -C /srv/mateo/app
cd /srv/mateo/app
install -d -o mateo -g mateo -m 0750 medicine_data

python3 -m venv /srv/mateo/venv
/srv/mateo/venv/bin/pip install --disable-pip-version-check \
    -r requirements-backend.txt -r requirements-agent.txt 'livekit-agents==1.8.12' 'piper-tts>=1.3,<2'
install -d -m 0755 /srv/mateo/voice-models
/srv/mateo/venv/bin/python -m piper.download_voices --data-dir /srv/mateo/voice-models en_US-lessac-medium
runuser -u mateo -- /srv/mateo/venv/bin/python -m backend.medicine_dev_index index

work_dir=$(mktemp -d)
trap 'rm -f "$work_dir/wcp-install" "$work_dir/ollama-install"; rmdir "$work_dir"' EXIT
if ! getent group aws-wcp-token >/dev/null; then
    curl -fSL --retry 3 -o "$work_dir/wcp-install" \
        https://raw.githubusercontent.com/aws/aws-workload-credentials-provider/main/install.sh
    AWCP_VERSION=3.1.1 bash "$work_dir/wcp-install" --no-sudoers
fi
install -d -m 0755 /usr/local/lib/mateo
asm_rev=$(git ls-remote https://github.com/aws/agent-toolkit-for-aws.git HEAD | cut -f1)
[[ $asm_rev =~ ^[0-9a-f]{40}$ ]]
curl -fSL --retry 3 -o /usr/local/lib/mateo/asm-exec \
    "https://raw.githubusercontent.com/aws/agent-toolkit-for-aws/$asm_rev/plugins/aws-core/skills/aws-secrets-manager/references/asm-exec"

install -m 0644 deploy/ec2/mateo@.service /etc/systemd/system/mateo@.service
install -m 0644 deploy/ec2/nginx.conf /etc/nginx/sites-available/mateo
ln -sfn /etc/nginx/sites-available/mateo /etc/nginx/sites-enabled/mateo
if [[ -L /etc/nginx/sites-enabled/default ]]; then
    unlink /etc/nginx/sites-enabled/default
fi
nginx -t
systemctl daemon-reload
systemctl enable --now nginx
systemctl enable mateo@api.service mateo@agent.service
systemctl restart mateo@api.service mateo@agent.service
systemctl reload nginx

if ! command -v ollama >/dev/null; then
    curl -fSL --retry 3 -o "$work_dir/ollama-install" https://ollama.com/install.sh
    bash "$work_dir/ollama-install"
fi
systemctl enable --now ollama.service
ollama pull qwen3-vl:2b-instruct
