import json
import subprocess

import database
import localizacao_sensor as ls

MAC = 'AA:00:00:00:30:01'


def _sensor(cliente_id=2):
    conn = database.get_db()
    conn.execute("INSERT INTO sensores (mac_id, nome_local, cliente_id) VALUES (?, 'UFV', ?)", (MAC, cliente_id))
    conn.commit(); conn.close()


def _responder(client, id_acao, codigo, saida):
    with client.session_transaction() as s:
        s.clear()
    assert client.post('/api/v2/acao_resultado', json={'mac_id': MAC, 'id': id_acao, 'codigo': codigo, 'saida': saida}).status_code == 200


# ---------- Central ----------

def test_client_user_can_ask_own_sensor_without_master_password(client, login):
    _sensor(cliente_id=2)
    cli = login(2, 'Cliente')
    r = cli.post(f'/api/v2/sensor/{MAC}/capturar_local', json={})
    assert r.status_code == 201
    id_acao = r.json['id']
    conn = database.get_db()
    assert conn.execute('SELECT comando FROM comandos_pendentes WHERE sensor_mac=?', (MAC,)).fetchone()['comando'] == f'acao:{id_acao}:capturar_local'
    conn.close()
    assert cli.get(f'/api/v2/sensor/{MAC}/capturar_local/{id_acao}').json == {'status': 'aguardando', 'entregue': False}


def test_other_tenant_cannot_ask(client, login):
    _sensor(cliente_id=2)
    assert login(3, 'Cliente').post(f'/api/v2/sensor/{MAC}/capturar_local', json={}).status_code == 403


def test_result_from_starlink_is_returned_for_the_form(client, login):
    _sensor()
    id_acao = login().post(f'/api/v2/sensor/{MAC}/capturar_local', json={}).json['id']
    saida = json.dumps({'ok': True, 'tentativas': [], 'local': {'latitude': -23.13, 'longitude': -52.30, 'accuracy_m': None, 'location_source': 'starlink'}})
    _responder(client, id_acao, 0, saida)
    r = login().get(f'/api/v2/sensor/{MAC}/capturar_local/{id_acao}').json
    assert r['status'] == 'ok' and r['location_source'] == 'starlink' and r['latitude'] == -23.13
    # Só preenche o formulário: a posição salva só muda quando a pessoa clica em salvar.
    conn = database.get_db()
    assert conn.execute('SELECT latitude FROM sensores WHERE mac_id=?', (MAC,)).fetchone()['latitude'] is None
    conn.close()


def test_failure_explains_each_attempt(client, login):
    _sensor()
    id_acao = login().post(f'/api/v2/sensor/{MAC}/capturar_local', json={}).json['id']
    _responder(client, id_acao, 1, json.dumps({'ok': False, 'local': None, 'tentativas': ['Starlink: x', 'Internet: y']}))
    r = login().get(f'/api/v2/sensor/{MAC}/capturar_local/{id_acao}').json
    assert r == {'status': 'erro', 'tentativas': ['Starlink: x', 'Internet: y']}


def test_old_agent_gets_update_hint(client, login):
    _sensor()
    id_acao = login().post(f'/api/v2/sensor/{MAC}/capturar_local', json={}).json['id']
    _responder(client, id_acao, 2, 'ação desconhecida pelo agente: capturar_local')
    r = login().get(f'/api/v2/sensor/{MAC}/capturar_local/{id_acao}').json
    assert r['status'] == 'erro' and 'Atualize o agente' in r['tentativas'][0]


def test_invalid_coordinates_from_sensor_are_refused(client, login):
    _sensor()
    id_acao = login().post(f'/api/v2/sensor/{MAC}/capturar_local', json={}).json['id']
    _responder(client, id_acao, 0, json.dumps({'ok': True, 'tentativas': [], 'local': {'latitude': 500, 'longitude': 0, 'location_source': 'starlink'}}))
    assert login().get(f'/api/v2/sensor/{MAC}/capturar_local/{id_acao}').json['status'] == 'erro'


def test_saving_windows_source_is_accepted(client, login):
    _sensor()
    r = login().post('/api/v2/configurar_sensor', json={'mac_id': MAC, 'nome': 'UFV', 'latitude': -23.1, 'longitude': -52.3,
                                                         'location_source': 'windows', 'accuracy_m': 35})
    assert r.status_code == 200


# ---------- Agente ----------

class _Starlink:
    def __init__(self, dados=None, erro=None):
        self.dados, self.erro = dados, erro

    class ChannelContext:
        def __init__(self, target): pass
        def close(self): pass

    def location_data(self, context):
        if self.erro:
            raise self.erro
        return self.dados


def test_starlink_gps_wins():
    r = ls.capturar(_Starlink({'latitude': -23.133, 'longitude': -52.305, 'altitude': 400}),
                    fontes=None)
    assert r['ok'] and r['local']['location_source'] == 'starlink'


def test_starlink_without_permission_explains_the_app_setting():
    local, motivo = ls.pela_starlink(_Starlink({'latitude': None, 'longitude': None}), 'x')
    assert local is None and 'Permitir acesso à localização' in motivo


def test_falls_back_in_order_and_keeps_reasons():
    r = ls.capturar(fontes=(lambda: (None, 'A falhou'), lambda: (None, None),
                            lambda: ({'latitude': 1.5, 'longitude': 2.5, 'location_source': 'ip'}, None)))
    assert r['ok'] and r['local']['location_source'] == 'ip' and r['tentativas'] == ['A falhou']


def test_windows_parses_powershell_output(monkeypatch):
    monkeypatch.setattr(ls, 'no_wsl', lambda: True)
    monkeypatch.setattr(ls.shutil, 'which', lambda nome: '/x/powershell.exe')
    feito = subprocess.CompletedProcess([], 0, stdout='-23.133101;-52.305631;48\r\n', stderr='')
    local, motivo = ls.pelo_windows(rodar=lambda *a, **k: feito)
    assert local == {'latitude': -23.133101, 'longitude': -52.305631, 'accuracy_m': 48.0, 'location_source': 'windows'}


def test_windows_location_off_explains_the_setting(monkeypatch):
    monkeypatch.setattr(ls, 'no_wsl', lambda: True)
    monkeypatch.setattr(ls.shutil, 'which', lambda nome: '/x/powershell.exe')
    negado = subprocess.CompletedProcess([], 3, stdout='NEGADO\r\n', stderr='')
    local, motivo = ls.pelo_windows(rodar=lambda *a, **k: negado)
    assert local is None and 'Serviços de localização' in motivo


def test_not_wsl_skips_windows_silently(monkeypatch):
    monkeypatch.setattr(ls, 'no_wsl', lambda: False)
    assert ls.pelo_windows() == (None, None)


def test_agent_package_ships_the_module():
    import app as central
    assert 'localizacao_sensor.py' in central.ARQUIVOS_AGENTE
