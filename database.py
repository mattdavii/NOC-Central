import os
import sqlite3
import threading
import time

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DB_POOL_MIN = max(1, int(os.environ.get("DB_POOL_MIN", "1")))
DB_POOL_MAX = max(DB_POOL_MIN, int(os.environ.get("DB_POOL_MAX", "10")))

_pg_pool = None
_pg_pool_lock = threading.Lock()
IDLE_IN_TRANSACTION_MS = max(10000, int(os.environ.get("DB_IDLE_IN_TRANSACTION_MS", "300000")))
# Rede instável até o banco: conexão que não completa o login em DB_CONNECT_TIMEOUT_S falha
# (sem isso fica presa até o Postgres desistir, 60 s, ocupando uma vaga do pool), e quem
# encontra o pool cheio espera até DB_POOL_WAIT_S por uma vaga em vez de falhar na hora.
DB_CONNECT_TIMEOUT_S = max(2, int(os.environ.get("DB_CONNECT_TIMEOUT_S", "10")))
DB_POOL_WAIT_S = max(0.0, float(os.environ.get("DB_POOL_WAIT_S", "5")))


def _cooperar_com_gevent():
    """Sob o worker gevent, faz o psycopg2 ceder a vez enquanto espera o banco.

    Sem isso, cada consulta (≈200 ms pela tailnet) bloqueia o único worker e uma
    requisição lenta congela a Central inteira.
    """
    try:
        from gevent import monkey
        if not monkey.is_module_patched("socket"):
            return
        from psycogreen.gevent import patch_psycopg
        patch_psycopg()
        print("✅ psycopg2 cooperativo com gevent (psycogreen).")
    except ImportError:
        print("⚠️ psycogreen ausente: consultas ao banco bloqueiam o worker gevent.")


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

    def __del__(self):
        # Rede de segurança: conexão esquecida (erro no meio de uma rota ou tarefa)
        # volta ao pool quando o objeto some, em vez de esgotar o pool aos poucos.
        try:
            self.close()
        except Exception:
            pass


def _get_postgres_pool():
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool

    with _pg_pool_lock:
        if _pg_pool is None:
            import psycopg2.pool
            import psycopg2.extras

            _cooperar_com_gevent()
            dsn = DATABASE_URL.replace("postgres://", "postgresql://", 1)
            _pg_pool = psycopg2.pool.ThreadedConnectionPool(
                DB_POOL_MIN,
                DB_POOL_MAX,
                dsn,
                cursor_factory=psycopg2.extras.DictCursor,
                # O banco fica do outro lado da tailnet: se o processo da Central
                # morre no meio de uma transação, a conexão pode continuar "viva"
                # no servidor segurando locks (e travando a migração do próximo
                # boot). O Postgres encerra transação ociosa além disso.
                options=f"-c idle_in_transaction_session_timeout={IDLE_IN_TRANSACTION_MS}",
                keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
                connect_timeout=DB_CONNECT_TIMEOUT_S,
            )
            print(f"✅ Pool PostgreSQL iniciado ({DB_POOL_MIN}-{DB_POOL_MAX} conexões).")
    return _pg_pool


def _registrar_na_requisicao(conn):
    """Guarda a conexão na requisição Flask atual para fechar_conexoes_da_requisicao()."""
    try:
        import flask
        if flask.has_request_context():
            flask.g.setdefault("_conexoes_db", []).append(conn)
    except ImportError:
        pass
    return conn


def fechar_conexoes_da_requisicao():
    """Fecha as conexões que uma requisição abriu e não fechou (rota que falhou no meio)."""
    import flask
    for conn in flask.g.pop("_conexoes_db", []):
        try:
            conn.close()  # idempotente: a rota pode já ter fechado
        except Exception:
            pass


def _pegar_do_pool(pool, espera_s=None):
    """getconn que espera vaga (até espera_s) em vez de falhar na hora com o pool cheio.

    Sob gevent o time.sleep cede a vez, então quem espera não trava o worker.
    """
    import psycopg2.pool
    espera_s = DB_POOL_WAIT_S if espera_s is None else espera_s
    limite = time.monotonic() + espera_s
    intervalo = 0.05
    while True:
        try:
            return pool.getconn()
        except psycopg2.pool.PoolError:
            if time.monotonic() >= limite:
                raise
            time.sleep(intervalo)
            intervalo = min(intervalo * 2, 0.5)


def get_db_connection():
    if DATABASE_URL:
        pool = _get_postgres_pool()
        conn = _pegar_do_pool(pool)
        return _registrar_na_requisicao(PostgresWrapper(conn, pool))

    conn = sqlite3.connect(os.environ.get("NOC_DB_PATH", "database.db"), timeout=10)
    conn.row_factory = sqlite3.Row
    return _registrar_na_requisicao(conn)


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
            # Lock preso por outra sessão não pode travar o boot para sempre:
            # falha em 30 s e o start script tenta de novo.
            conn.execute("SET LOCAL lock_timeout = '30s'")
            conn.execute('SELECT pg_advisory_xact_lock(220001)')
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE IF NOT EXISTS sensores (mac_id TEXT PRIMARY KEY, nome_local TEXT, ip_sensor TEXT, cpu_usage REAL, ram_usage REAL, temp REAL, status TEXT, lat REAL, lon REAL, ping_gateway REAL, ping_global TEXT)")
        conn.execute(f"CREATE TABLE IF NOT EXISTS clientes (id {identity}, usuario TEXT, senha TEXT, role TEXT)")
        ensure_columns(conn, 'sensores', SENSOR_COLUMNS)
        ensure_columns(conn, 'clientes', CLIENT_COLUMNS)
        duplicate_user = conn.execute(
            'SELECT usuario, COUNT(*) AS total FROM clientes '
            'WHERE usuario IS NOT NULL GROUP BY usuario HAVING COUNT(*) > 1 LIMIT 1'
        ).fetchone()
        if duplicate_user is None:
            conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_clientes_usuario_unique ON clientes (usuario)')
        else:
            print('⚠️ Existem usuários duplicados; índice único será criado após saneamento dos dados.')
        tables = {
            'servicos_os': "sensor_mac TEXT, nome_servico TEXT, descricao TEXT, status TEXT DEFAULT 'ONLINE'",
            'historico_pings': 'sensor_mac TEXT, google REAL, cloudflare REAL, aws REAL, quad9 REAL, data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
            'historico_telemetria': 'sensor_mac TEXT, download REAL, upload REAL, data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
            'comandos_pendentes': 'sensor_mac TEXT NOT NULL, comando TEXT NOT NULL, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
            'logs_ia': 'sensor_mac TEXT, tipo_evento TEXT, gravidade TEXT, detalhes TEXT, data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
            'ips_custom': 'sensor_mac TEXT, ip TEXT, descricao TEXT, latencia REAL DEFAULT 0',
            'ips_energia': 'sensor_mac TEXT, ip TEXT, descricao TEXT, latencia REAL DEFAULT 0',
            'dispositivos': 'sensor_mac TEXT, ip TEXT, mac TEXT, fabricante TEXT, nome_custom TEXT',
            'acoes_remotas': "sensor_mac TEXT NOT NULL, acao TEXT NOT NULL, solicitante TEXT, status TEXT DEFAULT 'pendente', codigo INTEGER, saida TEXT, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP, concluido_em TIMESTAMP",
            # Diagnóstico de rede: horários em epoch (segundos, UTC) para não depender do fuso da sessão.
            'medicoes_rede': 'sensor_mac TEXT NOT NULL, coletado_em DOUBLE PRECISION NOT NULL, tipo TEXT, papel TEXT, alvo TEXT, latencia_ms DOUBLE PRECISION, jitter_ms DOUBLE PRECISION, perda_pct DOUBLE PRECISION, amostras INTEGER',
            'starlink_medicoes': 'sensor_mac TEXT NOT NULL, coletado_em DOUBLE PRECISION NOT NULL, dados TEXT',
            'rotas_rede': 'sensor_mac TEXT NOT NULL, coletado_em DOUBLE PRECISION NOT NULL, motivo TEXT, ferramenta TEXT, saltos TEXT, pior_salto TEXT',
            'diagnosticos': "sensor_mac TEXT, provedor TEXT, chave TEXT NOT NULL, camada TEXT, causa TEXT, gravidade TEXT, confianca TEXT, inicio DOUBLE PRECISION NOT NULL, fim DOUBLE PRECISION, atualizado_em DOUBLE PRECISION, evidencias TEXT, acao TEXT, detalhe TEXT, mensagem TEXT, estado TEXT DEFAULT 'ativo', janelas INTEGER DEFAULT 1, janelas_ok INTEGER DEFAULT 0, notificado INTEGER DEFAULT 0, grupo_id INTEGER",
        }
        for table, definition in tables.items():
            conn.execute(f'CREATE TABLE IF NOT EXISTS {table} (id {identity}, {definition})')
        ensure_columns(conn, 'dispositivos', ["status TEXT DEFAULT 'desconhecido'", 'latencia REAL', 'neighbor_state TEXT',
                                              'fabricante_registro TEXT', 'fabricante_fonte TEXT', 'hostname TEXT',
                                              'hostname_fonte TEXT', 'visto_em TEXT'])
        conn.execute('CREATE TABLE IF NOT EXISTS nomes_conhecidos (mac TEXT PRIMARY KEY, nome TEXT)')
        conn.execute('''CREATE TABLE IF NOT EXISTS sensor_credentials (
            sensor_mac TEXT PRIMARY KEY,
            key_hash TEXT NOT NULL,
            key_prefix TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            revoked_at TIMESTAMP
        )''')
        conn.execute('CREATE TABLE IF NOT EXISTS telegram_outbox (event_key TEXT PRIMARY KEY, message TEXT NOT NULL, client_id INTEGER, attempts INTEGER NOT NULL, next_at DOUBLE PRECISION NOT NULL, expires_at DOUBLE PRECISION NOT NULL, state TEXT NOT NULL, result TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS telegram_status (destination TEXT PRIMARY KEY, result TEXT, last_attempt DOUBLE PRECISION, last_success DOUBLE PRECISION, last_failure DOUBLE PRECISION)')
        # Saúde do servidor: configuração e estado ficam no banco (não na máquina),
        # para o vigia e os alertas seguirem iguais quando o banco mudar de máquina.
        conn.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT, atualizado_em DOUBLE PRECISION)')
        # Família Nexo (NexoHub): contrato da empresa por aplicativo e nível do usuário por
        # aplicativo. Sem registro vale o padrão (ver acessos.py); registro é decisão explícita.
        conn.execute('''CREATE TABLE IF NOT EXISTS empresa_aplicativos (empresa_id INTEGER NOT NULL, aplicativo TEXT NOT NULL,
            ativo INTEGER NOT NULL, atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (empresa_id, aplicativo))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS usuario_aplicativos (usuario_id INTEGER NOT NULL, aplicativo TEXT NOT NULL,
            nivel TEXT NOT NULL, atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (usuario_id, aplicativo))''')
        conn.execute(f'''CREATE TABLE IF NOT EXISTS auditoria_acessos (id {identity}, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            autor_id INTEGER, autor TEXT, empresa_id INTEGER, acao TEXT NOT NULL, alvo TEXT, detalhes TEXT)''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_auditoria_acessos_empresa ON auditoria_acessos (empresa_id, id)')
        conn.execute('''CREATE TABLE IF NOT EXISTS saude_estado (chave TEXT PRIMARY KEY, origem TEXT NOT NULL, rotulo TEXT, estado TEXT,
            valor TEXT, detalhe TEXT, desde DOUBLE PRECISION, falhas INTEGER DEFAULT 0, notificado INTEGER DEFAULT 0, atualizado_em DOUBLE PRECISION)''')
        for table, cols in [('sensores','status,last_seen'),('sensores','cliente_id'),('logs_ia','sensor_mac,id'),('historico_pings','sensor_mac,id'),('historico_telemetria','sensor_mac,id'),('comandos_pendentes','sensor_mac,id'),('telegram_outbox','state,next_at'),('medicoes_rede','sensor_mac,coletado_em'),('starlink_medicoes','sensor_mac,coletado_em'),('rotas_rede','sensor_mac,coletado_em'),('diagnosticos','estado,sensor_mac'),('diagnosticos','sensor_mac,inicio')]:
            name = 'idx_' + table + '_' + cols.replace(',', '_')
            conn.execute(f'CREATE INDEX IF NOT EXISTS {name} ON {table} ({cols})')
        if not conn.execute("SELECT version FROM schema_migrations WHERE version='2.2.0-rc1'").fetchone():
            # Preserve an audit copy before removing historical placeholder coordinates.
            conn.execute('CREATE TABLE IF NOT EXISTS location_migration_backup (mac_id TEXT PRIMARY KEY, lat REAL, lon REAL)')
            conn.execute('INSERT INTO location_migration_backup SELECT mac_id, lat, lon FROM sensores WHERE lat IS NOT NULL OR lon IS NOT NULL')
            conn.execute('UPDATE sensores SET lat=NULL, lon=NULL WHERE ABS(lat + 14.235) < 0.00001 AND ABS(lon + 51.925) < 0.00001')
            conn.execute("UPDATE sensores SET latitude=lat, longitude=lon, location_source='legacy' WHERE lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180 AND latitude IS NULL")
            conn.execute("INSERT INTO schema_migrations (version) VALUES ('2.2.0-rc1')")
        if not conn.execute("SELECT version FROM schema_migrations WHERE version='2.3.0-medicoes-unicas'").fetchone():
            # O agente reenvia o lote quando a resposta demora: a mesma medição não
            # pode entrar duas vezes (nem contar duas janelas para o diagnóstico).
            for table, cols in (('medicoes_rede', 'sensor_mac, coletado_em, tipo, papel, alvo'),
                                ('starlink_medicoes', 'sensor_mac, coletado_em'),
                                ('rotas_rede', 'sensor_mac, coletado_em')):
                conn.execute(f'DELETE FROM {table} WHERE id NOT IN (SELECT MIN(id) FROM {table} GROUP BY {cols})')
                conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table} ON {table} ({cols})")
            conn.execute("INSERT INTO schema_migrations (version) VALUES ('2.3.0-medicoes-unicas')")
        if not conn.execute("SELECT version FROM schema_migrations WHERE version='2.2.0-sensor-auth'").fetchone():
            conn.execute("INSERT INTO schema_migrations (version) VALUES ('2.2.0-sensor-auth')")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SENSOR_COLUMNS = ['last_seen TIMESTAMP', 'ip_gateway TEXT', 'ultima_rota TEXT', 'download REAL', 'upload REAL', 'alerta_reconhecido INTEGER DEFAULT 1', 'disco REAL', 'net_up REAL', 'net_down REAL', 'portas TEXT', 'em_manutencao INTEGER DEFAULT 0', 'cliente_id INTEGER', 'gpu_temp REAL', "memoria_alerta TEXT DEFAULT 'ONLINE'", 'so_nome TEXT', 'so_versao TEXT', 'so_arquitetura TEXT', 'interface_nome TEXT', 'interface_mac TEXT', 'rede_mascara TEXT', 'rede_cidr TEXT', 'link_speed_mbps REAL', 'interface_up BOOLEAN', 'scan_rede TEXT', 'scan_limitado BOOLEAN', 'cliente_nome TEXT', 'latitude REAL', 'longitude REAL', 'accuracy_m REAL', 'location_source TEXT', 'location_updated_at TEXT', 'agent_version TEXT', 'traceroute TEXT', 'acoes_remotas INTEGER DEFAULT 0', 'host_info TEXT', 'alertas_host TEXT', 'provedor TEXT', 'provedor_origem TEXT', 'starlink_info TEXT', 'camadas_rede TEXT', 'diag_atualizado_em DOUBLE PRECISION', 'agent_build TEXT', 'build_recusado TEXT', 'auto_update_guard INTEGER', 'auto_atualizar INTEGER DEFAULT 1']
CLIENT_COLUMNS = ['nome TEXT', 'cliente_pai_id INTEGER', 'ativo INTEGER DEFAULT 1', 'logo_url TEXT', 'telegram_token TEXT', 'telegram_chat_id TEXT', 'delegacao_acessos INTEGER DEFAULT 1']
get_db = get_db_connection

if __name__ == '__main__':
    init_db()
