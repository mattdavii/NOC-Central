# Backup diário para o Google Drive

Uma vez por dia (03:30), a máquina onde roda o banco gera um `pg_dump` do
`nexopulse-db`, confere se o arquivo é restaurável e envia para o Google Drive
com o `rclone`. Backups com mais de 30 dias são apagados do Drive; os 7 mais
recentes ficam também em `~/.local/share/nexopulse-backup`.

## Instalação

1. Autorize o Google Drive no rclone (abre o navegador):

   ```bash
   rclone config create gdrive drive scope=drive.file
   ```

   `drive.file` dá ao rclone acesso só aos arquivos que ele mesmo cria.

2. Instale o timer:

   ```bash
   bash ops/backup/install.sh
   sudo loginctl enable-linger $USER   # para rodar mesmo sem sessão aberta
   ```

3. Teste uma vez:

   ```bash
   systemctl --user start nexopulse-backup
   journalctl --user -u nexopulse-backup -n 20 --no-pager
   ```

## Configuração

Opcionalmente em `~/.config/nexopulse-backup.env` (veja as variáveis no topo de
`nexopulse-backup.sh`), por exemplo `RETENCAO_DIAS=60` ou `RCLONE_DIR=Backups/NexoPulse`.

## Restauração

O banco usa TimescaleDB, que exige o modo de restauração dele:

```bash
docker exec -i nexopulse-db psql -U nexopulse -d nexopulse -c "SELECT timescaledb_pre_restore();"
docker exec -i nexopulse-db pg_restore -U nexopulse -d nexopulse --clean --if-exists < nexopulse-AAAAMMDD-HHMM.dump
docker exec -i nexopulse-db psql -U nexopulse -d nexopulse -c "SELECT timescaledb_post_restore();"
```

Restaure primeiro num banco de teste para conferir antes de sobrescrever o de produção.
