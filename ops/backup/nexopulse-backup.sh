#!/usr/bin/env bash
# Backup diário do banco NexoPulse (TimescaleDB no Docker) para o Google Drive.
#
# Gera um pg_dump no formato custom, confere se o arquivo é restaurável,
# envia com rclone e apaga do Drive os backups mais velhos que a retenção.
# Configuração por variáveis (ou em ~/.config/nexopulse-backup.env):
#   NEXO_DB_CONTAINER  container do Postgres          (padrão: nexopulse-db)
#   NEXO_DB_USER       usuário do banco               (padrão: nexopulse)
#   NEXO_DB_NAME       nome do banco                  (padrão: nexopulse)
#   RCLONE_REMOTE      remote do rclone               (padrão: gdrive)
#   RCLONE_DIR         pasta no Drive                 (padrão: NexoPulse/backups)
#   RETENCAO_DIAS      dias mantidos no Drive         (padrão: 30)
#   BACKUP_LOCAL_DIR   cópia local dos últimos dumps  (padrão: ~/.local/share/nexopulse-backup)
#   MANTER_LOCAL       quantos dumps manter localmente (padrão: 7)
set -euo pipefail

CONFIG="${NEXO_BACKUP_CONFIG:-$HOME/.config/nexopulse-backup.env}"
# shellcheck disable=SC1090
[[ -f "$CONFIG" ]] && source "$CONFIG"

NEXO_DB_CONTAINER="${NEXO_DB_CONTAINER:-nexopulse-db}"
NEXO_DB_USER="${NEXO_DB_USER:-nexopulse}"
NEXO_DB_NAME="${NEXO_DB_NAME:-nexopulse}"
RCLONE_REMOTE="${RCLONE_REMOTE:-gdrive}"
RCLONE_DIR="${RCLONE_DIR:-NexoPulse/backups}"
RETENCAO_DIAS="${RETENCAO_DIAS:-30}"
BACKUP_LOCAL_DIR="${BACKUP_LOCAL_DIR:-$HOME/.local/share/nexopulse-backup}"
MANTER_LOCAL="${MANTER_LOCAL:-7}"

log() { printf '[nexopulse-backup] %s\n' "$*"; }

mkdir -p "$BACKUP_LOCAL_DIR"
arquivo="$BACKUP_LOCAL_DIR/nexopulse-$(date +%Y%m%d-%H%M).dump"
parcial="$arquivo.parcial"
trap 'rm -f "$parcial"' EXIT

log "gerando dump de $NEXO_DB_NAME em $NEXO_DB_CONTAINER"
# Os avisos de "circular foreign-key" vêm das hypertables do TimescaleDB e são esperados.
docker exec "$NEXO_DB_CONTAINER" pg_dump -U "$NEXO_DB_USER" -Fc "$NEXO_DB_NAME" > "$parcial" 2> >(grep -v -E '^pg_dump: (warning|detail|hint):' >&2)

# Um dump que o pg_restore não consegue listar não serve como backup.
tabelas=$(docker exec -i "$NEXO_DB_CONTAINER" pg_restore -l < "$parcial" | grep -c 'TABLE DATA' || true)
if [[ "$tabelas" -lt 1 ]]; then
  log "ERRO: dump inválido (nenhuma tabela listada); nada foi enviado"
  exit 1
fi
mv "$parcial" "$arquivo"
log "dump ok: $(du -h "$arquivo" | cut -f1), $tabelas tabelas"

log "enviando para $RCLONE_REMOTE:$RCLONE_DIR"
rclone copy "$arquivo" "$RCLONE_REMOTE:$RCLONE_DIR" --retries 3 --low-level-retries 10

log "removendo do Drive backups com mais de $RETENCAO_DIAS dias"
rclone delete "$RCLONE_REMOTE:$RCLONE_DIR" --min-age "${RETENCAO_DIAS}d" --include 'nexopulse-*.dump'

# Mantém só os N dumps locais mais recentes.
ls -1t "$BACKUP_LOCAL_DIR"/nexopulse-*.dump 2>/dev/null | tail -n +"$((MANTER_LOCAL + 1))" | xargs -r rm -f

log "concluído: $(basename "$arquivo")"
