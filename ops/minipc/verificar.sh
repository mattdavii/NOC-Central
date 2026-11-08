#!/usr/bin/env bash
# Lista de conferência do MiniPC (rode depois da virada, e sempre que quiser).
#   bash ops/minipc/verificar.sh
set -uo pipefail

CONTAINER="${NEXO_DB_CONTAINER:-nexopulse-db}"
ENV_FILE="${NEXO_DB_ENV:-$HOME/nexopulse-db/.env}"
DB_USER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" 2>/dev/null | cut -d= -f2-)"
DB_NAME="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" 2>/dev/null | cut -d= -f2-)"
psql_() { docker exec -i "$CONTAINER" psql -U "${DB_USER:-nexopulse}" -d "${DB_NAME:-nexopulse}" -At "$@" 2>/dev/null; }
falhas=0
item() {  # item <ok 0/1> <texto> [dica]
  if [[ "$1" == 0 ]]; then printf '  ✅ %s\n' "$2"; else printf '  ❌ %s%s\n' "$2" "${3:+ — $3}"; falhas=$((falhas + 1)); fi
}
testa() { "$@" >/dev/null 2>&1; echo $?; }

echo "Máquina"
item "$(testa systemctl is-enabled docker)" "Docker sobe com a máquina" "sudo systemctl enable --now docker"
item "$([[ "$(systemctl is-enabled sleep.target 2>/dev/null)" == masked ]]; echo $?)" "Suspensão desligada" "rode o preparar.sh"
item "$([[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" == yes ]]; echo $?)" "Serviços do usuário sem sessão aberta (linger)" "sudo loginctl enable-linger $USER"

echo "Banco"
item "$([[ "$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null)" == healthy ]]; echo $?)" "Container $CONTAINER saudável" "cd ~/nexopulse-db && docker compose up -d"
item "$([[ "$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$CONTAINER" 2>/dev/null)" == unless-stopped ]]; echo $?)" "Container volta sozinho após reinício"
tabelas="$(psql_ -c "SELECT COUNT(*) FROM pg_tables WHERE schemaname='public'")"
item "$([[ "${tabelas:-0}" -gt 0 ]]; echo $?)" "Banco com dados (${tabelas:-0} tabelas)" "rode o restaurar.sh"

echo "Rede"
item "$([[ "$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState",""))' 2>/dev/null)" == Running ]]; echo $?)" "Tailscale conectado" "sudo tailscale up"
item "$(tailscale serve status 2>/dev/null | grep -q ':5432'; echo $?)" "Banco publicado na tailnet (5432)" "tailscale serve --bg --tcp 5432 tcp://localhost:5432"
conexoes="$(psql_ -c "SELECT COUNT(*) FROM pg_stat_activity WHERE datname=current_database() AND client_addr IS NOT NULL")"
item "$([[ "${conexoes:-0}" -gt 0 ]]; echo $?)" "Central conectada a ESTE banco (${conexoes:-0} conexões)" "no Railway, TS_DB_HOST deve apontar para este MiniPC"

echo "Central e serviços"
url="$(psql_ -c "SELECT valor FROM config_sistema WHERE chave='central_url'")"
url="${url:-https://nexopulse.up.railway.app}"
item "$([[ "$(curl -s -o /dev/null -w '%{http_code}' -m 20 "$url/healthz")" == 200 ]]; echo $?)" "Central no ar ($url)"
item "$(testa systemctl --user is-enabled nexopulse-backup.timer)" "Backup diário instalado" "bash ops/backup/install.sh"
item "$(rclone listremotes 2>/dev/null | grep -qx 'gdrive:'; echo $?)" "Google Drive do backup configurado (rclone)" "copie ~/.config/rclone/rclone.conf do notebook"
item "$(testa systemctl --user is-enabled nexopulse-vigia.timer)" "Vigia instalado" "bash ops/vigia/install.sh"
host_vigia="$(psql_ -c "SELECT valor FROM config_sistema WHERE chave='vigia_host'")"
item "$([[ "$host_vigia" == "$(hostname)" ]]; echo $?)" "Vigia reportando desta máquina (último: ${host_vigia:-nenhum})" "python3 ~/.local/bin/nexopulse-vigia --agora"

echo
if ((falhas)); then echo "❌ $falhas item(ns) pendente(s)."; exit 1; fi
echo "✅ MiniPC pronto e servindo a Central."
