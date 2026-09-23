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
            self._pool.putconn(self.conn, close=True)
            return
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

    conn = sqlite3.connect(os.environ.get("NOC_DB_PATH", "database.db"), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_columns(conn, table, definitions):
    if DATABASE_URL:
        existing = {row['column_name'] for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?", (table,)).fetchall()}
    else:
        existing = {row['name'] for row in conn.execute(f'PRAGMA table_info({table})').fetchall()}
    for definition in definitions:
        if definition.split()[0] not in existing:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {definition}')


def init_db():
    """Idempotent additive migration; unexpected failures stop startup."""
    conn = get_db_connection()
    identity = 'SERIAL PRIMARY KEY' if DATABASE_URL else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    try:
        if DATABASE_URL:
            conn.execute('SELECT pg_advisory_xact_lock(220001)')
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE IF NOT EXISTS sensores (mac_id TEXT PRIMARY KEY, nome_local TEXT, ip_sensor TEXT, cpu_usage REAL, ram_usage REAL, temp REAL, status TEXT, lat REAL, lon REAL, ping_gateway REAL, ping_global TEXT)")
        conn.execute(f"CREATE TABLE IF NOT EXISTS clientes (id {identity}, usuario TEXT, senha TEXT, role TEXT)")
        ensure_columns(conn, 'sensores', SENSOR_COLUMNS)
        ensure_columns(conn, 'clientes', CLIENT_COLUMNS)
        tables = {
            'servicos_os': "sensor_mac TEXT, nome_servico TEXT, descricao TEXT, status TEXT DEFAULT 'ONLINE'",
            'historico_pings': 'sensor_mac TEXT, google REAL, cloudflare REAL, aws REAL, quad9 REAL, data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
            'historico_telemetria': 'sensor_mac TEXT, download REAL, upload REAL, data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
            'comandos_pendentes': 'sensor_mac TEXT NOT NULL, comando TEXT NOT NULL, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
            'logs_ia': 'sensor_mac TEXT, tipo_evento TEXT, gravidade TEXT, detalhes TEXT, data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
            'ips_custom': 'sensor_mac TEXT, ip TEXT, descricao TEXT, latencia REAL DEFAULT 0',
            'ips_energia': 'sensor_mac TEXT, ip TEXT, descricao TEXT, latencia REAL DEFAULT 0',
            'dispositivos': 'sensor_mac TEXT, ip TEXT, mac TEXT, fabricante TEXT, nome_custom TEXT',
        }
        for table, definition in tables.items():
            conn.execute(f'CREATE TABLE IF NOT EXISTS {table} (id {identity}, {definition})')
        ensure_columns(conn, 'dispositivos', ["status TEXT DEFAULT 'desconhecido'", 'latencia REAL', 'neighbor_state TEXT'])
        conn.execute('CREATE TABLE IF NOT EXISTS nomes_conhecidos (mac TEXT PRIMARY KEY, nome TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS telegram_outbox (event_key TEXT PRIMARY KEY, message TEXT NOT NULL, client_id INTEGER, attempts INTEGER NOT NULL, next_at DOUBLE PRECISION NOT NULL, expires_at DOUBLE PRECISION NOT NULL, state TEXT NOT NULL, result TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS telegram_status (destination TEXT PRIMARY KEY, result TEXT, last_attempt DOUBLE PRECISION, last_success DOUBLE PRECISION, last_failure DOUBLE PRECISION)')
        for table, cols in [('sensores','status,last_seen'),('sensores','cliente_id'),('logs_ia','sensor_mac,id'),('historico_pings','sensor_mac,id'),('historico_telemetria','sensor_mac,id'),('comandos_pendentes','sensor_mac,id'),('telegram_outbox','state,next_at')]:
            name = 'idx_' + table + '_' + cols.replace(',', '_')
            conn.execute(f'CREATE INDEX IF NOT EXISTS {name} ON {table} ({cols})')
        if not conn.execute("SELECT version FROM schema_migrations WHERE version='2.2.0-rc1'").fetchone():
            # Preserve an audit copy before removing historical placeholder coordinates.
            conn.execute('CREATE TABLE IF NOT EXISTS location_migration_backup (mac_id TEXT PRIMARY KEY, lat REAL, lon REAL)')
            conn.execute('INSERT INTO location_migration_backup SELECT mac_id, lat, lon FROM sensores WHERE lat IS NOT NULL OR lon IS NOT NULL')
            conn.execute('UPDATE sensores SET lat=NULL, lon=NULL WHERE ABS(lat + 14.235) < 0.00001 AND ABS(lon + 51.925) < 0.00001')
            conn.execute("UPDATE sensores SET latitude=lat, longitude=lon, location_source='legacy' WHERE lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180 AND latitude IS NULL")
            conn.execute("INSERT INTO schema_migrations (version) VALUES ('2.2.0-rc1')")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SENSOR_COLUMNS = ['last_seen TIMESTAMP', 'ip_gateway TEXT', 'ultima_rota TEXT', 'download REAL', 'upload REAL', 'alerta_reconhecido INTEGER DEFAULT 1', 'disco REAL', 'net_up REAL', 'net_down REAL', 'portas TEXT', 'em_manutencao INTEGER DEFAULT 0', 'cliente_id INTEGER', 'gpu_temp REAL', "memoria_alerta TEXT DEFAULT 'ONLINE'", 'so_nome TEXT', 'so_versao TEXT', 'so_arquitetura TEXT', 'interface_nome TEXT', 'interface_mac TEXT', 'rede_mascara TEXT', 'rede_cidr TEXT', 'link_speed_mbps REAL', 'interface_up BOOLEAN', 'scan_rede TEXT', 'scan_limitado BOOLEAN', 'cliente_nome TEXT', 'latitude REAL', 'longitude REAL', 'accuracy_m REAL', 'location_source TEXT', 'location_updated_at TEXT', 'agent_version TEXT', 'traceroute TEXT']
CLIENT_COLUMNS = ['nome TEXT', 'cliente_pai_id INTEGER', 'ativo INTEGER DEFAULT 1', 'logo_url TEXT', 'telegram_token TEXT', 'telegram_chat_id TEXT']
get_db = get_db_connection

if __name__ == '__main__':
    init_db()
