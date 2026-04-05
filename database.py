import sqlite3
import os
from urllib.parse import urlparse

DATABASE_URL = os.environ.get('DATABASE_URL')

# --- NOSSA CAPA PROTETORA (O TRADUTOR OFICIAL) ---
class PostgresWrapper:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, query, params=None):
        cur = self.conn.cursor()
        query_pg = query.replace('?', '%s')
        try:
            if params:
                cur.execute(query_pg, params)
            else:
                cur.execute(query_pg)
            return cur
        except Exception as e:
            # O SEGREDO: Se o comando falhar (ex: coluna já existe), limpa a memória do Postgres para ele não travar o resto do site!
            self.conn.rollback()
            raise e # Repassa o erro

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

    def cursor(self):
        return self.conn.cursor()
    
def get_db_connection():
    if DATABASE_URL:
        import psycopg2
        import psycopg2.extras
        
        url_corrigida = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        url = urlparse(url_corrigida)
        
        conn = psycopg2.connect(
            database=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port,
            cursor_factory=psycopg2.extras.DictCursor
        )
        # Entregamos a conexão VESTIDA COM A CAPA
        return PostgresWrapper(conn)
    else:
        conn = sqlite3.connect('database.db')
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    conn = get_db_connection()
    
    # 1. CRIAÇÃO BASE (Caso o banco venha do zero)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sensores (
            mac_id TEXT PRIMARY KEY, nome_local TEXT, ip_sensor TEXT,
            cpu_usage REAL, ram_usage REAL, temp REAL, status TEXT,
            lat REAL, lon REAL, ping_gateway REAL, ping_global TEXT,
            ip_gateway TEXT, last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    if DATABASE_URL:
        conn.execute('''CREATE TABLE IF NOT EXISTS historico_pings (id SERIAL PRIMARY KEY, sensor_mac TEXT, google INTEGER, cloudflare INTEGER, aws INTEGER, quad9 INTEGER, data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS clientes (id SERIAL PRIMARY KEY, usuario TEXT, senha TEXT, role TEXT)''')
    else:
        conn.execute('''CREATE TABLE IF NOT EXISTS historico_pings (id INTEGER PRIMARY KEY AUTOINCREMENT, sensor_mac TEXT, google INTEGER, cloudflare INTEGER, aws INTEGER, quad9 INTEGER, data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS clientes (id INTEGER PRIMARY KEY AUTOINCREMENT, usuario TEXT, senha TEXT, role TEXT)''')

    # ========================================================
    # 🚀 2. AUTO-MIGRAÇÃO (ATUALIZAÇÃO DE BANCOS EXISTENTES)
    # ========================================================
    
    # Adiciona a Logomarca na tabela de Clientes
    try:
        conn.execute("ALTER TABLE clientes ADD COLUMN logo_url TEXT DEFAULT ''")
    except Exception:
        # Se a coluna já existir, o Postgres joga um erro e bloqueia a transação. 
        # Nós usamos o rollback para "limpar" o erro e seguir em frente!
        if DATABASE_URL: conn.conn.rollback() 
        pass 

    # Adiciona a vinculação do Nome do Cliente na tabela de Sensores
    try:
        conn.execute("ALTER TABLE sensores ADD COLUMN cliente_nome TEXT DEFAULT 'Cliente Padrão'")
    except Exception:
        if DATABASE_URL: conn.conn.rollback()
        pass

    conn.commit()
    conn.close()
    print("✅ Banco de dados sincronizado (Com Suporte a White-Label e Filtros) e pronto para operação!")

# O famoso apelido para o app.py não quebrar
get_db = get_db_connection

if __name__ == '__main__':
    if not DATABASE_URL and os.path.exists('database.db'):
        os.remove('database.db')
    init_db()