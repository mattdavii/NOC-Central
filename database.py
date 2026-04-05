import sqlite3
import os
import urlparse # Para Python 2. Se usar Python 3, é 'urllib.parse'

# Detecta se estamos no Render (eles enviam a variável DATABASE_URL)
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    """Retorna uma conexão com o banco de dados correto (Postgres na nuvem, SQLite local)"""
    
    if DATABASE_URL:
        # --- MÁGICA DO POSTGRESQL (NUVEM) ---
        import psycopg2
        # O Render dá uma URL tipo: postgres://user:password@host:port/dbname
        # Precisamos parsear isso
        url = urlparse.urlparse(DATABASE_URL)
        
        conn = psycopg2.connect(
            database=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port
        )
        return conn
    else:
        # --- MÁGICA DO SQLITE (LOCAL) ---
        # Se não houver URL, estamos no PC local
        conn = sqlite3.connect('database.db')
        conn.row_factory = sqlite3.Row # Permite acessar colunas pelo nome localmente
        return conn

def init_db():
    """Cria as tabelas iniciais se elas não existirem"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Sintaxe compatível com ambos os bancos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sensores (
            mac_id TEXT PRIMARY KEY,
            nome_local TEXT,
            ip_sensor TEXT,
            cpu_usage REAL,
            ram_usage REAL,
            temp REAL,
            status TEXT,
            lat REAL,
            lon REAL,
            ping_gateway REAL,
            ping_global TEXT,
            ip_gateway TEXT,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS historico_pings (
            id SERIAL PRIMARY KEY, -- SERIAL no Postgres, INTEGER AUTOINCREMENT no SQLite
            sensor_mac TEXT,
            google INTEGER,
            cloudflare INTEGER,
            aws INTEGER,
            quad9 INTEGER,
            data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Correção para o SQLite não aceitar SERIAL
    if not DATABASE_URL:
        # Se for SQLite, precisamos corrigir a tabela historico_pings que criamos acima
        cursor.execute("DROP TABLE historico_pings")
        cursor.execute('''
            CREATE TABLE historico_pings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_mac TEXT,
                google INTEGER,
                cloudflare INTEGER,
                aws INTEGER,
                quad9 INTEGER,
                data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

    conn.commit()
    conn.close()
    print("Banco de dados sincronizado!")

if __name__ == '__main__':
    # Se rodar este arquivo direto, ele reseta o banco local
    if os.path.exists('database.db'):
        os.remove('database.db')
    init_db()