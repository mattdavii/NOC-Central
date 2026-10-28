import sqlite3

import pytest

import app as central
import database


def test_connection_left_open_by_a_request_is_closed_at_teardown(client):
    with central.app.test_request_context('/'):
        conn = database.get_db()
        conn.execute('SELECT 1').fetchone()
        # a rota "esquece" de fechar (ou falhou antes do close)
    if isinstance(conn, sqlite3.Connection):
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute('SELECT 1')
    else:
        assert conn._closed


def test_forgotten_connections_do_not_exhaust_the_pool(client):
    # Mais requisições do que o tamanho do pool (10), cada uma esquecendo a conexão.
    for _ in range(database.DB_POOL_MAX + 5):
        with central.app.test_request_context('/'):
            database.get_db().execute('SELECT 1').fetchone()


def test_closing_twice_is_harmless(client):
    with central.app.test_request_context('/'):
        conn = database.get_db()
        conn.close()
