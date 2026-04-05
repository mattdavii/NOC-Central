import sqlite3
import os
from urllib.parse import urlparse

# A nuvem (Render) injeta essa variável automaticamente. No seu PC, ela não existe.
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    if DATABASE_URL:
        # --- CONEXÃO NUVEM (POSTGRESQL) ---
        import psycopg2
        # Corrige a URL caso o Render mande o formato antigo
        url_corrigida = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        url = urlparse(url_corrigida)
        
        conn = psycopg2.connect(
            database=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port
        )
        return conn
    else:
        # --- CONEXÃO LOCAL (SQLITE) ---
        conn = sqlite3.connect('database.db')
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Tabela Principal de Sensores (Sintaxe igual para os dois)
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
    
    # 2. Tabela de Histórico (Sintaxe diferente para Nuvem e PC)
    if DATABASE_URL:
        # No PostgreSQL usa-se "SERIAL" para o ID automático
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS historico_pings (
                id SERIAL PRIMARY KEY,
                sensor_mac TEXT,
                google INTEGER,
                cloudflare INTEGER,
                aws INTEGER,
                quad9 INTEGER,
                data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    else:
        # No SQLite usa-se "INTEGER PRIMARY KEY AUTOINCREMENT"
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS historico_pings (
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
    print("Banco de dados sincronizado e pronto para operação!")

get_db = get_db_connection

if __name__ == '__main__':
    # Se rodar este arquivo direto no PC, ele reseta o banco local para testes
    if not DATABASE_URL and os.path.exists('database.db'):
        os.remove('database.db')
    init_db()