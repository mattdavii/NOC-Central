#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  echo "Execute este instalador como usuário normal: bash linux/install.sh"
  echo "O script solicitará sudo apenas quando necessário."
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="$(id -un)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${ROOT_DIR}/.venv"
ENV_FILE="/etc/default/noc-sensor"
SERVICE_FILE="/etc/systemd/system/noc-sensor.service"

echo "==> Instalando dependências do Linux..."
if command -v apt-get >/dev/null; then
  sudo apt-get update
  sudo apt-get install -y python3-venv python3-pip iproute2 iputils-ping net-tools traceroute lm-sensors
elif command -v pacman >/dev/null; then
  sudo pacman -S --needed --noconfirm python python-pip iproute2 iputils net-tools traceroute lm_sensors
elif command -v dnf >/dev/null; then
  sudo dnf install -y python3 python3-pip iproute iputils net-tools traceroute lm_sensors
elif command -v zypper >/dev/null; then
  sudo zypper --non-interactive install python3 python3-pip iproute2 iputils net-tools traceroute sensors
else
  echo "Gerenciador de pacotes não reconhecido. Instale: python3 (com venv e pip), iproute2, ping, traceroute e lm-sensors; depois rode de novo."
  exit 1
fi

echo "==> Preparando ambiente Python..."
"${PYTHON_BIN}" -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${ROOT_DIR}/requirements-agente.txt"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "==> Criando ${ENV_FILE}..."
  sudo tee "${ENV_FILE}" >/dev/null <<'EOF'
NOC_CENTRAL_URL=https://nexopulse.up.railway.app/api/v2/report_data
NOC_LOCAL_PORT=10000
NOC_TELEMETRIA_INTERVALO=5
NOC_WATCHDOG_INTERVALO=15
NOC_SCAN_REDE_INTERVALO=60
# Nome com que o sensor aparece na Central ao ser cadastrado (padrão: hostname da máquina):
# NOC_SENSOR_NOME=Loja Centro - Rack
# Gere uma chave individual na tela Sensores da Central e configure-a somente neste equipamento:
# NOC_SENSOR_API_KEY=cole-a-chave-individual-aqui
EOF
fi

echo "==> Instalando serviço systemd..."
sudo tee "${SERVICE_FILE}" >/dev/null <<EOF
[Unit]
Description=NexoPulse Edge Sensor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${ROOT_DIR}
EnvironmentFile=-${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStart=${VENV_DIR}/bin/python ${ROOT_DIR}/agente_v2.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=15

# Endurecimento sem bloquear leitura de /sys, rede ou SQLite local.
NoNewPrivileges=false
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now noc-sensor.service

echo
echo "✅ NexoPulse Edge instalado e iniciado."
echo "Status: sudo systemctl status noc-sensor --no-pager"
echo "Logs:   journalctl -u noc-sensor -f"
echo "Config: ${ENV_FILE}"
