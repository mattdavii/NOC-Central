import os
import tempfile
import pytest

os.environ['DISABLE_BACKGROUND_GUARDIAN'] = '1'
os.environ['ADMIN_SENHA_INICIAL'] = 'test-password-only'
os.environ['FLASK_SECRET_KEY'] = 'test-session-key'
os.environ['NOC_DB_PATH'] = tempfile.mktemp(suffix='.db')
os.environ.pop('NOC_SENSOR_API_KEY', None)

import app as central
import database

@pytest.fixture()
def client():
    conn = database.get_db()
    for table in ('sensores','clientes','telegram_outbox','telegram_status','logs_ia','dispositivos','historico_pings','historico_telemetria','comandos_pendentes','ips_custom'):
        conn.execute(f'DELETE FROM {table}')
    for ident, role, parent in ((1,'Administrador Master',None),(2,'Cliente',None),(3,'Cliente',None),(4,'Administrador Cliente',2)):
        conn.execute('INSERT INTO clientes (id, usuario, senha, role, ativo, cliente_pai_id) VALUES (?, ?, ?, ?, 1, ?)', (ident, f'user{ident}', 'not-a-real-password', role, parent))
    conn.commit();conn.close()
    central.app.config['TESTING'] = True
    return central.app.test_client()

@pytest.fixture()
def login(client):
    def as_user(ident=1,role='Administrador Master'):
        with client.session_transaction() as session:
            session.update(user_id=ident, usuario=f'user{ident}', role=role)
        return client
    return as_user
