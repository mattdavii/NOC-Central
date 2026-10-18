import pytest
from werkzeug.security import generate_password_hash
import app as central
import database


@pytest.fixture(autouse=True)
def _zerar_tentativas():
    central._falhas_senha.clear()
    yield
    central._falhas_senha.clear()

MAC = 'AA:00:00:00:20:01'
SENHA = 'senha-de-teste-123'


def _preparar(client, login, permitir=True):
    conn = database.get_db()
    conn.execute('UPDATE clientes SET senha=? WHERE id=1', (generate_password_hash(SENHA),))
    conn.execute("INSERT INTO sensores (mac_id, nome_local, acoes_remotas) VALUES (?, 'Rack', ?)", (MAC, 1 if permitir else 0))
    conn.commit(); conn.close()
    return login()


def test_only_master_can_list_or_run_actions(client, login):
    _preparar(client, login)
    cli = login(2, 'Cliente')
    assert cli.get(f'/api/v2/sensor/{MAC}/acoes').status_code == 403
    assert cli.post(f'/api/v2/sensor/{MAC}/acoes', json={'acao': 'diagnostico', 'senha': SENHA}).status_code == 403


def test_wrong_password_is_rejected_and_rate_limited(client, login):
    master = _preparar(client, login)
    for _ in range(5):
        assert master.post(f'/api/v2/sensor/{MAC}/acoes', json={'acao': 'diagnostico', 'senha': 'errada'}).status_code == 401
    assert master.post(f'/api/v2/sensor/{MAC}/acoes', json={'acao': 'diagnostico', 'senha': SENHA}).status_code == 429


def test_disabled_sensor_refuses_actions(client, login):
    master = _preparar(client, login, permitir=False)
    assert master.post(f'/api/v2/sensor/{MAC}/acoes', json={'acao': 'diagnostico', 'senha': SENHA}).status_code == 409


def test_unknown_action_is_rejected(client, login):
    master = _preparar(client, login)
    assert master.post(f'/api/v2/sensor/{MAC}/acoes', json={'acao': 'rm -rf /', 'senha': SENHA}).status_code == 400


def test_full_flow_queues_command_audits_and_stores_result(client, login):
    master = _preparar(client, login)
    resp = master.post(f'/api/v2/sensor/{MAC}/acoes', json={'acao': 'diagnostico', 'senha': SENHA})
    assert resp.status_code == 201
    id_acao = resp.json['id']
    conn = database.get_db()
    assert conn.execute('SELECT comando FROM comandos_pendentes WHERE sensor_mac=?', (MAC,)).fetchone()['comando'] == f'acao:{id_acao}:diagnostico'
    assert any('AÇÃO REMOTA' in r['message'] for r in conn.execute('SELECT message FROM telegram_outbox').fetchall())
    conn.close()
    with client.session_transaction() as s:
        s.clear()
    assert client.post('/api/v2/acao_resultado', json={'mac_id': MAC, 'id': id_acao, 'codigo': 0, 'saida': 'ok'}).status_code == 200
    conn = database.get_db()
    row = conn.execute('SELECT status, saida FROM acoes_remotas WHERE id=?', (id_acao,)).fetchone()
    conn.close()
    assert row['status'] == 'concluida' and row['saida'] == 'ok'


def test_result_from_another_sensor_is_refused(client, login):
    master = _preparar(client, login)
    id_acao = master.post(f'/api/v2/sensor/{MAC}/acoes', json={'acao': 'diagnostico', 'senha': SENHA}).json['id']
    with client.session_transaction() as s:
        s.clear()
    assert client.post('/api/v2/acao_resultado', json={'mac_id': 'AA:FF:FF:FF:FF:FF', 'id': id_acao, 'codigo': 0, 'saida': 'x'}).status_code == 403


def test_toggle_requires_password(client, login):
    master = _preparar(client, login, permitir=False)
    assert master.post(f'/api/v2/sensor/{MAC}/permitir_acoes', json={'permitir': True, 'senha': 'errada'}).status_code == 401
    assert master.post(f'/api/v2/sensor/{MAC}/permitir_acoes', json={'permitir': True, 'senha': SENHA}).json['permitido'] is True


def test_agent_package_contains_agent_and_installer(client):
    import io, tarfile
    resp = client.get('/agente/pacote.tar.gz')
    assert resp.status_code == 200
    nomes = tarfile.open(fileobj=io.BytesIO(resp.data), mode='r:gz').getnames()
    for esperado in ('nexopulse-edge/agente_v2.py', 'nexopulse-edge/linux/install.sh', 'nexopulse-edge/VERSION'):
        assert esperado in nomes
    assert not any('sensor_key' in n or '.venv' in n for n in nomes)


def test_installer_points_to_this_central(client):
    script = client.get('/instalar.sh', headers={'X-Forwarded-Proto': 'https'}).get_data(as_text=True)
    assert 'https://localhost/agente/pacote.tar.gz' in script
    assert 'NOC_CENTRAL_URL="https://localhost/api/v2/report_data"' in script


def test_action_without_answer_expires(client, login):
    master = _preparar(client, login)
    conn = database.get_db()
    conn.execute("INSERT INTO acoes_remotas (sensor_mac, acao, solicitante, status, criado_em) VALUES (?, 'diagnostico', 'admin', 'enviada', '2000-01-01 00:00:00')", (MAC,))
    conn.execute("INSERT INTO acoes_remotas (sensor_mac, acao, solicitante, status) VALUES (?, 'diagnostico', 'admin', 'enviada')", (MAC,))
    conn.commit(); conn.close()
    master.post(f'/api/v2/sensor/{MAC}/acoes', json={'acao': 'diagnostico', 'senha': SENHA})
    hist = master.get(f'/api/v2/sensor/{MAC}/acoes').get_json()['historico']
    assert hist[0]['status'] == 'pendente'  # acabou de ser criada: não pode expirar
    antiga = next(a for a in hist if a['criado_em'].startswith('2000-01-01'))
    assert antiga['status'] == 'erro' and 'não confirmou' in antiga['saida']
    assert any(a['status'] == 'enviada' for a in hist)
