#!/usr/bin/env bash
# Prepara o MiniPC para receber o banco da NexoPulse.
#
# Rode como usuário normal, de dentro do repositório clonado:
#   bash ops/minipc/preparar.sh
# Pode rodar de novo quantas vezes quiser: cada passo confere antes de agir.
# Não restaura dados nem mexe na Central: isso é o restaurar.sh e a virada.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DESTINO="${NEXO_DB_DIR:-$HOME/nexopulse-db}"
TS_NOME="${TS_HOSTNAME_MINIPC:-nexopulse-minipc}"

passo() { printf '\n==> %s\n' "$*"; }
ok() { printf '    ✅ %s\n' "$*"; }
falta() { printf '    ⚠️  %s\n' "$*"; }

# ---------------------------------------------------------------- pacotes
passo "Pacotes (docker, compose, tailscale, rclone, python3, git, curl)"
instalar() {
  if command -v apt-get >/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y docker.io docker-compose-v2 rclone python3 git curl \
      || sudo apt-get install -y docker.io docker-compose-plugin rclone python3 git curl
  elif command -v pacman >/dev/null; then
    sudo pacman -S --needed --noconfirm docker docker-compose rclone python git curl
  elif command -v dnf >/dev/null; then
    sudo dnf install -y docker docker-compose-plugin rclone python3 git curl \
      || sudo dnf install -y moby-engine docker-compose rclone python3 git curl
  else
    echo "Gerenciador de pacotes não reconhecido: instale docker, docker compose, rclone, python3, git e curl."
    exit 1
  fi
}
faltando=()
for cmd in docker rclone python3 git curl; do command -v "$cmd" >/dev/null || faltando+=("$cmd"); done
docker compose version >/dev/null 2>&1 || faltando+=("docker-compose")
if ((${#faltando[@]})); then instalar; fi
if ! command -v tailscale >/dev/null; then
  # Instalador oficial do Tailscale (repositório da distribuição).
  curl -fsSL https://tailscale.com/install.sh | sh
fi
ok "pacotes instalados"

passo "SSH (o notebook envia o dump e a senha do banco por aqui, pela tailnet)"
if ! systemctl list-unit-files 2>/dev/null | grep -qE '^(sshd|ssh)\.service'; then
  if command -v apt-get >/dev/null; then sudo apt-get install -y openssh-server
  elif command -v pacman >/dev/null; then sudo pacman -S --needed --noconfirm openssh
  elif command -v dnf >/dev/null; then sudo dnf install -y openssh-server
  fi
fi
sudo systemctl enable --now sshd 2>/dev/null || sudo systemctl enable --now ssh
ok "ssh ativo"

passo "Docker"
sudo systemctl enable --now docker >/dev/null
if ! id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
  sudo usermod -aG docker "$USER"
  falta "seu usuário entrou no grupo docker agora: saia e entre de novo (ou reinicie) e rode este script outra vez."
  exit 0
fi
docker info >/dev/null 2>&1 || { falta "o Docker não responde para este usuário; reinicie a sessão e rode de novo."; exit 1; }
ok "docker ativo e acessível"

# ---------------------------------------------------------------- energia
passo "Energia: o MiniPC não pode suspender (a Central depende do banco)"
if systemctl is-enabled sleep.target 2>/dev/null | grep -qx masked; then
  ok "suspensão e hibernação já desligadas"
else
  sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target >/dev/null
  ok "suspensão e hibernação desligadas (para religar: sudo systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target)"
fi
if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]]; then
  sudo loginctl enable-linger "$USER"
fi
ok "serviços do usuário rodam sem sessão aberta (linger)"

# ---------------------------------------------------------------- tailscale
passo "Tailscale"
sudo systemctl enable --now tailscaled >/dev/null
if [[ "$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState",""))' 2>/dev/null)" != "Running" ]]; then
  echo "    Abra o link que aparecer para colocar o MiniPC na sua tailnet:"
  sudo tailscale up --hostname="$TS_NOME"
fi
# Sem isso o "tailscale serve" pede sudo a cada uso.
sudo tailscale set --operator="$USER" >/dev/null 2>&1 || true
ip_ts="$(tailscale ip -4 2>/dev/null | head -1)"
ok "na tailnet como $(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["HostName"])') ($ip_ts)"

# ---------------------------------------------------------------- banco
passo "Banco (TimescaleDB em Docker, porta só em 127.0.0.1)"
mkdir -p "$DESTINO/initdb"
install -m644 "$REPO/ops/minipc/banco/docker-compose.yml" "$DESTINO/docker-compose.yml"
install -m755 "$REPO/ops/minipc/banco/initdb/10-nexopulse.sh" "$DESTINO/initdb/10-nexopulse.sh"
if [[ ! -f "$DESTINO/.env" ]]; then
  falta "falta $DESTINO/.env com a senha do banco. Ele vem do notebook, junto com o dump:"
  falta "  no notebook: bash ops/minipc/exportar.sh --enviar $USER@$TS_NOME"
  falta "depois rode este script de novo."
  exit 0
fi
chmod 600 "$DESTINO/.env"
(cd "$DESTINO" && docker compose up -d)
for _ in $(seq 1 60); do
  [[ "$(docker inspect -f '{{.State.Health.Status}}' nexopulse-db 2>/dev/null)" == "healthy" ]] && break
  sleep 2
done
[[ "$(docker inspect -f '{{.State.Health.Status}}' nexopulse-db)" == "healthy" ]] || { falta "o banco não ficou pronto: cd $DESTINO && docker compose logs db"; exit 1; }
ok "banco no ar (container nexopulse-db)"

passo "Banco publicado na tailnet (só a tailnet alcança)"
if tailscale serve status 2>/dev/null | grep -q ':5432'; then
  ok "porta 5432 já publicada"
else
  tailscale serve --bg --tcp 5432 tcp://localhost:5432 >/dev/null
  ok "porta 5432 publicada"
fi

passo "Pronto para restaurar"
echo "    Próximo passo: bash ops/minipc/restaurar.sh ~/nexopulse-migracao/<pasta do dump>"
echo "    Na virada, o Railway passa a usar TS_DB_HOST=$TS_NOME (ou $ip_ts)."
