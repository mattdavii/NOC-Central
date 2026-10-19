#!/usr/bin/env bash
# Exporta o banco da NexoPulse para levar ao MiniPC (roda na máquina atual do banco).
#
#   bash ops/minipc/exportar.sh                         # só gera a pasta com dump e conferência
#   bash ops/minipc/exportar.sh --enviar mateus@nexopulse-minipc
#        também copia, pela tailnet, a pasta, a senha do banco (.env) e a config do rclone
#   bash ops/minipc/exportar.sh --congelar --enviar ...
#        antes do dump, tira o banco da tailnet: a Central para de gravar (fica esperando)
#        e os sensores guardam as medições. Use na virada, para não perder nada.
set -euo pipefail

CONTAINER="${NEXO_DB_CONTAINER:-nexopulse-db}"
DB_DIR="${NEXO_DB_DIR:-$HOME/Projetos/nexopulse-db}"
ENV_FILE="${NEXO_DB_ENV:-$DB_DIR/.env}"
SAIDA="${NEXO_MIGRACAO_DIR:-$HOME/nexopulse-migracao}/$(date +%Y%m%d-%H%M%S)"
ENVIAR=""
CONGELAR=0
while (($#)); do
  case "$1" in
    --enviar) ENVIAR="$2"; shift 2 ;;
    --congelar) CONGELAR=1; shift ;;
    *) echo "opção desconhecida: $1"; exit 1 ;;
  esac
done

[[ -f "$ENV_FILE" ]] || { echo "não achei $ENV_FILE (defina NEXO_DB_ENV)"; exit 1; }
DB_USER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | cut -d= -f2-)"
DB_NAME="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | cut -d= -f2-)"
psql_() { docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -At -v ON_ERROR_STOP=1 "$@"; }

if ((CONGELAR)); then
  echo "==> Congelando: tirando a porta 5432 da tailnet (a Central para de gravar)"
  tailscale serve --tcp=5432 off >/dev/null 2>&1 || true
  if tailscale serve status 2>/dev/null | grep -q ':5432'; then
    tailscale serve reset   # neste host o serve só publica o banco
  fi
  if tailscale serve status 2>/dev/null | grep -q ':5432'; then
    echo "não consegui tirar a porta 5432 da tailnet; nada foi exportado"; exit 1
  fi
  # Conexões que a Central já tinha aberto chegam pelo proxy do Docker (client_addr
  # preenchido); o psql do docker exec usa socket local e não é afetado.
  sleep 5
  psql_ -c "SELECT COUNT(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE datname='$DB_NAME' AND client_addr IS NOT NULL" >/dev/null
  echo "    Central desconectada. Para desfazer (rollback): tailscale serve --bg --tcp 5432 tcp://localhost:5432"
fi

mkdir -p "$SAIDA"
chmod 700 "$SAIDA"
echo "==> Dump de $DB_NAME ($CONTAINER) em $SAIDA"
docker exec "$CONTAINER" pg_dump -U "$DB_USER" -Fc "$DB_NAME" > "$SAIDA/nexopulse.dump" \
  2> >(grep -v -E '^pg_dump: (warning|detail|hint):' >&2)

echo "==> Contagem de linhas por tabela (para conferir depois da restauração)"
tabelas=$(psql_ -c "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
: > "$SAIDA/contagens.tsv"
for t in $tabelas; do
  printf '%s\t%s\n' "$t" "$(psql_ -c "SELECT COUNT(*) FROM \"$t\"" < /dev/null)" >> "$SAIDA/contagens.tsv"
done
{
  echo "origem=$(hostname)"
  echo "exportado_em=$(date -Is)"
  echo "imagem=$(docker inspect -f '{{.Config.Image}}' "$CONTAINER")"
  echo "timescaledb=$(psql_ -c "SELECT extversion FROM pg_extension WHERE extname='timescaledb'")"
  echo "postgres=$(psql_ -c 'SHOW server_version')"
  echo "congelado=$CONGELAR"
} > "$SAIDA/origem.txt"
(cd "$SAIDA" && sha256sum nexopulse.dump contagens.tsv origem.txt > SHA256SUMS)
echo "    $(wc -l < "$SAIDA/contagens.tsv") tabelas, $(awk -F'\t' '{s+=$2} END {print s}' "$SAIDA/contagens.tsv") linhas, dump de $(du -h "$SAIDA/nexopulse.dump" | cut -f1)"

if [[ -n "$ENVIAR" ]]; then
  echo "==> Enviando para $ENVIAR pela tailnet"
  ssh "$ENVIAR" 'mkdir -p ~/nexopulse-migracao ~/nexopulse-db ~/.config/rclone && chmod 700 ~/nexopulse-migracao ~/nexopulse-db'
  scp -q -r "$SAIDA" "$ENVIAR:nexopulse-migracao/"
  # Senha do banco: a mesma que o Railway usa, então tem que ser idêntica no MiniPC.
  scp -q "$ENV_FILE" "$ENVIAR:nexopulse-db/.env"
  if [[ -f "$HOME/.config/rclone/rclone.conf" ]]; then
    # Autorização do Google Drive do backup diário (não precisa autorizar de novo).
    scp -q "$HOME/.config/rclone/rclone.conf" "$ENVIAR:.config/rclone/rclone.conf"
  fi
  ssh "$ENVIAR" 'chmod 600 ~/nexopulse-db/.env ~/.config/rclone/rclone.conf 2>/dev/null; true'
  echo "    enviado: ~/nexopulse-migracao/$(basename "$SAIDA"), ~/nexopulse-db/.env e a config do rclone"
fi

echo
echo "✅ Exportado: $SAIDA"
echo "   No MiniPC: bash ops/minipc/restaurar.sh ~/nexopulse-migracao/$(basename "$SAIDA")"
