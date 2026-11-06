"""Conexão com o banco resistente a rede instável: espera por vaga no pool e limite para conectar."""
import time

import psycopg2.pool
import pytest

import database


class PoolFalso:
    def __init__(self, falhas):
        self.falhas = falhas
        self.chamadas = 0

    def getconn(self):
        self.chamadas += 1
        if self.chamadas <= self.falhas:
            raise psycopg2.pool.PoolError('connection pool exhausted')
        return 'conexao'


def test_espera_vaga_no_pool():
    pool = PoolFalso(falhas=3)
    assert database._pegar_do_pool(pool, espera_s=2) == 'conexao'
    assert pool.chamadas == 4


def test_desiste_depois_do_tempo():
    pool = PoolFalso(falhas=10_000)
    inicio = time.monotonic()
    with pytest.raises(psycopg2.pool.PoolError):
        database._pegar_do_pool(pool, espera_s=0.3)
    assert 0.25 <= time.monotonic() - inicio < 1.5


def test_sem_espera_falha_na_hora():
    with pytest.raises(psycopg2.pool.PoolError):
        database._pegar_do_pool(PoolFalso(falhas=1), espera_s=0)


@pytest.mark.skipif(not database.DATABASE_URL, reason='só no PostgreSQL')
def test_limite_de_conexao_chega_ao_driver():
    conn = database.get_db()
    try:
        dsn = conn.conn.get_dsn_parameters()
        assert dsn.get('connect_timeout') == str(database.DB_CONNECT_TIMEOUT_S)
    finally:
        conn.close()
