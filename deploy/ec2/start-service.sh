#!/usr/bin/env bash
set -euo pipefail

cd /srv/mateo/app
export AWS_REGION=ap-south-1
export AWS_TOKEN
AWS_TOKEN=$(cat /run/awssmatoken)
export MATEO_RUNTIME_SETTINGS="{{resolve:secretsmanager:livekit-avatar-starter/ec2/runtime:SecretString:settings}}"
export MATEO_DB_PATH=/var/lib/mateo/analytics.sqlite3
export MEDICINE_RAG_DB=/var/lib/mateo/medicine.sqlite3
export MATEO_BACKEND_URL=http://127.0.0.1:8000
export FRONTEND_ORIGIN=http://localhost:8080,http://127.0.0.1:8080

python=/srv/mateo/venv/bin/python
case "$1" in
    api)
        command=("$python" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --no-access-log)
        ;;
    guest)
        command=("$python" -m uvicorn backend.guest_app:app --host 127.0.0.1 --port 8010 --no-access-log)
        ;;
    agent)
        export MATEO_TTS_PROVIDER=piper
        export MATEO_PIPER_MODEL=/srv/mateo/voice-models/en_US-lessac-medium.onnx
        command=("$python" agent/agent.py start)
        ;;
    *)
        exit 2
        ;;
esac

exec "$python" /usr/local/lib/mateo/asm-exec "$python" deploy/ec2/runtime.py "${command[@]}"
