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
SERVICE="nexopulse-edge"
ENV_FILE="/etc/default/${SERVICE}"
SERVICE_FILE="/etc/systemd/system/${SERVICE}.service"
# Nomes até a 2.2: o serviço era noc-sensor e o agente, agente_v2.py.
ENV_ANTIGO="/etc/default/noc-sensor"
SERVICE_ANTIGO="/etc/systemd/system/noc-sensor.service"

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

# mtr melhora o diagnóstico de rota (perda por salto); sem ele o Edge usa traceroute.
if command -v apt-get >/dev/null; then sudo apt-get install -y mtr-tiny || true
elif command -v pacman >/dev/null; then sudo pacman -S --needed --noconfirm mtr || true
elif command -v dnf >/dev/null; then sudo dnf install -y mtr || true
elif command -v zypper >/dev/null; then sudo zypper --non-interactive install mtr || true
fi

echo "==> Preparando ambiente Python..."
"${PYTHON_BIN}" -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${ROOT_DIR}/requirements-agente.txt"

# Coletor Starlink (opcional, dependências mais pesadas): NOC_STARLINK=on força,
# off pula; no automático, instala só se a antena responder ou já estiver em uso.
starlink_modo="${NOC_STARLINK:-auto}"
instalar_starlink=0
case "${starlink_modo}" in
  on|1|sim|yes) instalar_starlink=1 ;;
  off|0|nao|no) instalar_starlink=0 ;;
  *)
    if "${VENV_DIR}/bin/python" -c 'import starlink_grpc' 2>/dev/null \
       || timeout 2 bash -c '</dev/tcp/192.168.100.1/9200' 2>/dev/null; then
      instalar_starlink=1
    fi
    ;;
esac
if ((instalar_starlink)) && [[ -f "${ROOT_DIR}/requirements-starlink.txt" ]]; then
  echo "==> Instalando o coletor Starlink..."
  "${VENV_DIR}/bin/pip" install -r "${ROOT_DIR}/requirements-starlink.txt" \
    || echo "⚠️  Dependências da Starlink não instaladas; o sensor funciona sem o coletor Starlink."
else
  echo "    Starlink não detectada: coletor não instalado (para instalar depois: NOC_STARLINK=on bash linux/install.sh)."
fi

echo "==> Migrando instalação antiga (noc-sensor), se houver..."
if [[ -f "${SERVICE_ANTIGO}" ]]; then
  sudo systemctl disable --now noc-sensor.service 2>/dev/null || true
  sudo rm -f "${SERVICE_ANTIGO}"
  sudo systemctl daemon-reload
fi
if [[ -f "${ENV_ANTIGO}" && ! -f "${ENV_FILE}" ]]; then
  # Mantém URL da Central, nome e chave de API do sensor.
  sudo mv "${ENV_ANTIGO}" "${ENV_FILE}"
fi
# Arquivos que saíram do pacote (agente renomeado; docs e Windows ficam só no servidor).
# Nunca dentro de um clone do repositório.
if [[ ! -d "${ROOT_DIR}/.git" ]]; then
  rm -f "${ROOT_DIR}/agente_v2.py" "${ROOT_DIR}/linux/README.md" "${ROOT_DIR}/linux/update.sh"
  rm -rf "${ROOT_DIR}/windows" "${ROOT_DIR}/__pycache__"
fi

CENTRAL_URL="${NOC_CENTRAL_URL:-https://nexopulse.up.railway.app/api/v2/report_data}"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "==> Criando ${ENV_FILE}..."
  sudo tee "${ENV_FILE}" >/dev/null <<EOF
NOC_CENTRAL_URL=${CENTRAL_URL}
NOC_LOCAL_PORT=10000
NOC_TELEMETRIA_INTERVALO=5
NOC_WATCHDOG_INTERVALO=15
NOC_SCAN_REDE_INTERVALO=60
# Nome com que o sensor aparece na Central ao ser cadastrado (padrão: hostname da máquina):
# NOC_SENSOR_NOME=Loja Centro - Rack
# A chave do sensor é gerada automaticamente em sensor_key.txt no primeiro uso.
EOF
elif [[ -n "${NOC_CENTRAL_URL:-}" ]]; then
  sudo sed -i "s|^NOC_CENTRAL_URL=.*|NOC_CENTRAL_URL=${CENTRAL_URL}|" "${ENV_FILE}"
fi
if [[ -n "${NOC_SENSOR_NOME:-}" ]]; then
  sudo sed -i '/^#\? *NOC_SENSOR_NOME=/d' "${ENV_FILE}"
  echo "NOC_SENSOR_NOME=${NOC_SENSOR_NOME}" | sudo tee -a "${ENV_FILE}" >/dev/null
fi

echo "==> Instalando serviço systemd..."
sudo tee "${SERVICE_FILE}" >/dev/null <<EOF
[Unit]
Description=NexoPulse Edge Sensor
After=network-online.target
Wants=network-online.target
# Nunca desiste de reiniciar, mesmo com várias quedas seguidas.
StartLimitIntervalSec=0

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${ROOT_DIR}
EnvironmentFile=-${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
# Guarda da atualização automática: desfaz uma versão nova que não consegue falar com a Central.
Environment=NEXOPULSE_GUARD=1
ExecStartPre=-/bin/bash ${ROOT_DIR}/linux/nexopulse-guard.sh
ExecStart=${VENV_DIR}/bin/python ${ROOT_DIR}/nexopulse_edge.py
# always: volta sozinho se cair, e também depois de "reiniciar/atualizar agente".
Restart=always
RestartSec=5
TimeoutStopSec=15

NoNewPrivileges=false
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

# Ações de root ("Atualizar sistema" e "Reiniciar máquina"): só com NEXOPULSE_ROOT=1.
# Se já foram liberadas antes, só atualiza o ajudante para a versão do pacote.
if [[ "${NEXOPULSE_ROOT:-0}" == "1" ]]; then
  echo "==> Liberando as ações de root (NEXOPULSE_ROOT=1)..."
  bash "${ROOT_DIR}/linux/liberar-root.sh"
elif [[ -f /usr/local/sbin/nexopulse-admin ]]; then
  sudo install -o root -g root -m 0755 "${ROOT_DIR}/linux/nexopulse-admin" /usr/local/sbin/nexopulse-admin
fi

sudo systemctl daemon-reload
# enable = sobe sozinho a cada boot; --now = já inicia agora.
sudo systemctl enable --now "${SERVICE}.service"
sudo systemctl restart "${SERVICE}.service"

echo
echo "✅ NexoPulse Edge instalado, iniciado e configurado para subir com a máquina."
echo "Status: sudo systemctl status ${SERVICE} --no-pager"
echo "Logs:   journalctl -u ${SERVICE} -f"
echo "Config: ${ENV_FILE}"
if [[ -f /usr/local/sbin/nexopulse-admin ]]; then
  echo "Ações de root: liberadas (desfazer: bash ${ROOT_DIR}/linux/liberar-root.sh --desfazer)"
else
  echo "Ações de root: não liberadas (para liberar: bash ${ROOT_DIR}/linux/liberar-root.sh)"
fi
