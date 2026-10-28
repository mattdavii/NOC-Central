#!/usr/bin/env bash
# Restaura no MiniPC o dump gerado pelo exportar.sh e confere tabela a tabela.
#
#   bash ops/minipc/restaurar.sh ~/nexopulse-migracao/<pasta>
#   bash ops/minipc/restaurar.sh ~/nexopulse-migracao/<pasta> --substituir
#        apaga o banco do MiniPC antes (use para repetir o ensaio ou na virada,
#        se um ensaio anterior já deixou dados lá)
set -euo pipefail

PASTA="${1:?informe a pasta gerada pelo exportar.sh}"
SUBSTITUIR=0
[[ "${2:-}" == "--substituir" ]] && SUBSTITUIR=1
CONTAINER="${NEXO_DB_CONTAINER:-nexopulse-db}"
ENV_FILE="${NEXO_DB_ENV:-$HOME/nexopulse-db/.env}"

[[ -f "$ENV_FILE" ]] || { echo "não achei $ENV_FILE (rode o preparar.sh)"; exit 1; }
DB_USER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | cut -d= -f2-)"
DB_NAME="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | cut -d= -f2-)"
psql_() { docker exec -i "$CONTAINER" psql -U "$DB_USER" -At -v ON_ERROR_STOP=1 "$@"; }

echo "==> Conferindo os arquivos (sha256)"
(cd "$PASTA" && sha256sum -c --quiet SHA256SUMS) || { echo "arquivos corrompidos ou incompletos: exporte de novo"; exit 1; }
cat "$PASTA/origem.txt" | sed 's/^/    /'
imagem_origem="$(grep '^imagem=' "$PASTA/origem.txt" | cut -d= -f2)"
imagem_aqui="$(docker inspect -f '{{.Config.Image}}' "$CONTAINER")"
if [[ "$imagem_origem" != "$imagem_aqui" ]]; then
  echo "⚠️  imagem diferente da origem ($imagem_origem × $imagem_aqui): a versão do TimescaleDB precisa ser a mesma."
  exit 1
fi

tabelas_aqui=$(psql_ -d "$DB_NAME" -c "SELECT COUNT(*) FROM pg_tables WHERE schemaname='public'")
if [[ "$tabelas_aqui" != "0" ]]; then
  if ((!SUBSTITUIR)); then
    echo "o banco $DB_NAME aqui já tem $tabelas_aqui tabelas. Para apagar e restaurar de novo, use --substituir."
    exit 1
  fi
  echo "==> Apagando o banco $DB_NAME deste MiniPC (--substituir)"
  psql_ -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DB_NAME' AND pid <> pg_backend_pid()" >/dev/null
  psql_ -d postgres -c "DROP DATABASE \"$DB_NAME\"" >/dev/null
  psql_ -d postgres -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$DB_USER\"" >/dev/null
fi

echo "==> Restaurando (modo de restauração do TimescaleDB)"
psql_ -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS timescaledb" >/dev/null
psql_ -d "$DB_NAME" -c "SELECT timescaledb_pre_restore()" >/dev/null
log="$PASTA/restauracao.log"
set +e
docker exec -i "$CONTAINER" pg_restore -U "$DB_USER" -d "$DB_NAME" --no-owner < "$PASTA/nexopulse.dump" > "$log" 2>&1
set -e
psql_ -d "$DB_NAME" -c "SELECT timescaledb_post_restore()" >/dev/null
psql_ -d "$DB_NAME" -c "ANALYZE" >/dev/null
# Avisos esperados: a extensão já existe e os catálogos do TimescaleDB.
erros=$(grep -c 'error:' "$log" || true)
esperados=$(grep -cE 'extension "timescaledb" already exists|already exists' "$log" || true)

echo "==> Conferindo linhas por tabela"
diferencas=0
conferidas=0
# Lê pelo descritor 3: o docker exec -i do psql consumiria a entrada padrão.
while IFS=$'\t' read -r -u 3 tabela esperado; do
  atual=$(psql_ -d "$DB_NAME" -c "SELECT COUNT(*) FROM \"$tabela\"" < /dev/null 2>/dev/null || echo "ausente")
  conferidas=$((conferidas + 1))
  if [[ "$atual" == "$esperado" ]]; then
    printf '    ✅ %-28s %s\n' "$tabela" "$atual"
  else
    printf '    ❌ %-28s origem %s · aqui %s\n' "$tabela" "$esperado" "$atual"
    diferencas=$((diferencas + 1))
  fi
done 3< "$PASTA/contagens.tsv"

echo
if ((diferencas)); then
  echo "❌ $diferencas tabela(s) diferentes. Veja $log ($erros mensagem(ns) de erro)."
  exit 1
fi
if ((erros > esperados)); then
  echo "⚠️  contagens batem, mas o pg_restore registrou $erros erro(s) ($esperados esperados). Confira: $log"
fi
esperadas=$(wc -l < "$PASTA/contagens.tsv")
if ((conferidas != esperadas)); then
  echo "❌ só $conferidas de $esperadas tabelas foram conferidas."
  exit 1
fi
echo "✅ Banco restaurado e conferido: $conferidas tabelas iguais à origem."
