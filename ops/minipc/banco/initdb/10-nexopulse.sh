#!/bin/bash
# Executado só na primeira inicialização do volume.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
    CREATE EXTENSION IF NOT EXISTS timescaledb;
    -- Banco separado para a suíte de testes, que apaga as tabelas a cada teste.
    CREATE DATABASE ${POSTGRES_DB}_test OWNER ${POSTGRES_USER};
SQL
