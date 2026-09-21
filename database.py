import os
import sqlite3
import threading

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DB_POOL_MIN = max(1, int(os.environ.get("DB_POOL_MIN", "1")))
DB_POOL_MAX = max(DB_POOL_MIN, int(os.environ.get("DB_POOL_MAX", "10")))

_pg_pool = None
_pg_pool_lock = threading.Lock()


class PostgresWrapper:
    """Compatibilidade mínima com a interface sqlite usada pelo projeto.

    Em produção, a conexão vem de um pool e close() devolve a conexão ao pool
    em vez de abrir/fechar um socket PostgreSQL a cada requisição.
    """

    def __init__(self, conn, pool):
        self.conn = conn
        self._pool = pool
        self._closed = False

    def execute(self, query, params=None):
        cur = self.conn.cursor()
        query_pg = query.replace("?", "%s")
        try:
            if params is not None:
                cur.execute(query_pg, params)
            else:
                cur.execute(query_pg)
            return cur
        except Exception:
            self.conn.rollback()
            cur.close()
            raise

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def cursor(self):
        return self.conn.cursor()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            # Nunca devolve ao pool uma transação quebrada/aberta.
            self.conn.rollback()
        except Exception:
            pass
        self._pool.putconn(self.conn)


def _get_postgres_pool():
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool

    with _pg_pool_lock:
        if _pg_pool is None:
            import psycopg2.pool
            import psycopg2.extras

            dsn = DATABASE_URL.replace("postgres://", "postgresql://", 1)
            _pg_pool = psycopg2.pool.ThreadedConnectionPool(
                DB_POOL_MIN,
                DB_POOL_MAX,
                dsn,
                cursor_factory=psycopg2.extras.DictCursor,
            )
            print(f"✅ Pool PostgreSQL iniciado ({DB_POOL_MIN}-{DB_POOL_MAX} conexões).")
    return _pg_pool


def get_db_connection():
    if DATABASE_URL:
        pool = _get_postgres_pool()
        conn = pool.getconn()
        return PostgresWrapper(conn, pool)

    conn = sqlite3.connect("database.db", timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_execute(conn, sql):
    try:
        conn.execute(sql)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def init_db():
    conn = get_db_connection()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sensores (
            mac_id TEXT PRIMARY KEY, nome_local TEXT, ip_sensor TEXT,
            cpu_usage REAL, ram_usage REAL, temp REAL, status TEXT,
            lat REAL, lon REAL, ping_gateway REAL, ping_global TEXT,
            ip_gateway TEXT, last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    if DATABASE_URL:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS historico_pings (
                id SERIAL PRIMARY KEY,
                sensor_mac TEXT,
                google INTEGER,
                cloudflare INTEGER,
                aws INTEGER,
                quad9 INTEGER,
                data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clientes (
                id SERIAL PRIMARY KEY,
                usuario TEXT,
                senha TEXT,
                role TEXT
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS historico_pings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_mac TEXT,
                google INTEGER,
                cloudflare INTEGER,
                aws INTEGER,
                quad9 INTEGER,
                data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT,
                senha TEXT,
                role TEXT
            )
            """
        )

    conn.commit()

    # Migrações compatíveis com bancos existentes.
    _safe_execute(conn, "ALTER TABLE clientes ADD COLUMN logo_url TEXT DEFAULT ''")
    _safe_execute(conn, "ALTER TABLE sensores ADD COLUMN cliente_nome TEXT DEFAULT 'Cliente Padrão'")

    # Índices para as consultas mais frequentes do dashboard e histórico.
    _safe_execute(
        conn,
        "CREATE INDEX IF NOT EXISTS idx_sensores_status_last_seen ON sensores(status, last_seen)",
    )
    _safe_execute(
        conn,
        "CREATE INDEX IF NOT EXISTS idx_sensores_cliente ON sensores(cliente_id)",
    )
    _safe_execute(
        conn,
        "CREATE INDEX IF NOT EXISTS idx_historico_pings_sensor_id ON historico_pings(sensor_mac, id)",
    )

    conn.close()
    print("✅ Banco de dados sincronizado, indexado e pronto para operação.")


# Alias histórico usado pelo app.py.
get_db = get_db_connection


if __name__ == "__main__":
    if not DATABASE_URL and os.path.exists("database.db"):
        os.remove("database.db")
    init_db()
