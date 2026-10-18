#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo 'Há alterações locais. Revise e preserve-as antes de atualizar; nada foi descartado.'
  exit 1
fi
TARGET_VERSION="${1:-}"
if [[ ! "${TARGET_VERSION}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-rc[0-9]+)?$ && ! "${TARGET_VERSION}" =~ ^[0-9a-f]{40}$ ]]; then
  echo 'Informe uma tag ou SHA completo, por exemplo v2.2.0-rc1 ou e5537ed24da09add6b3b541e03fe89c1577a1344.'
  exit 1
fi
ROLLBACK_BRANCH="rollback/linux-$(date -u +%Y%m%dT%H%M%SZ)"
git branch "${ROLLBACK_BRANCH}" HEAD
if [[ "${TARGET_VERSION}" == v* ]]; then
  git fetch origin "tag" "${TARGET_VERSION}"
else
  git fetch origin "${TARGET_VERSION}"
fi
git switch --detach "${TARGET_VERSION}"
"${ROOT_DIR}/.venv/bin/python" -m pip install -r requirements-agente.txt
"${ROOT_DIR}/.venv/bin/python" -m py_compile agente_v2.py
SERVICE_FILE="/etc/systemd/system/noc-sensor.service"
if [[ -f "${SERVICE_FILE}" ]]; then
  sudo sed -i 's/^Description=.*/Description=NexoPulse Edge Sensor/' "${SERVICE_FILE}"
  sudo systemctl daemon-reload
fi
sudo systemctl restart noc-sensor.service
sleep 3
sudo systemctl is-active --quiet noc-sensor.service
sudo systemctl status noc-sensor.service --no-pager
printf '\nRollback preservado: %s\n' "${ROLLBACK_BRANCH}"
printf 'Confira os logs: journalctl -u noc-sensor.service -n 80 --no-pager\n'
