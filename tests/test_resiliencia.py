"""Parte 4: buffer persistente do Edge e 'Site sem comunicação' na Central."""
import importlib
import json
import time

import pytest

import app as central
import database
import fila_envio


@pytest.fixture()
def fila(tmp_path, monkeypatch):
    monkeypatch.setenv('NOC_FILA_DB', str(tmp_path / 'fila.db'))
    monkeypatch.delenv('NOC_LINK_SECUNDARIO', raising=False)
    mod = importlib.reload(fila_envio)
    yield mod
    monkeypatch.delenv('NOC_FILA_DB')
    importlib.reload(fila_envio)


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{}'


def test_items_survive_restart_and_keep_original_time(fila):
    fila.enfileirar({'tipo': 'rede', 'coletado_em': 1000})
    fila.enfileirar({'tipo': 'rede', 'coletado_em': 1060})
    reiniciado = importlib.reload(fila)  # simula o agente reiniciando
    ids, itens = reiniciado._proximo_lote()
    assert [i['coletado_em'] for i in itens] == [1000, 1060] and reiniciado.pendentes() == 2


def test_failed_send_keeps_queue_then_drains(fila, monkeypatch):
    for i in range(3):
        fila.enfileirar({'tipo': 'rede', 'coletado_em': 1000 + i})
    enviados = []

    def offline(req, timeout=None):
        raise OSError('sem link')
    monkeypatch.setattr(fila.urllib.request, 'urlopen', offline)
    with pytest.raises(OSError):
        fila.enviar_lote(fila.rotas_envio('http://central/api/v2/medicoes'), 'AA', {})
    assert fila.pendentes() == 3

    def online(req, timeout=None):
        enviados.append(json.loads(req.data))
        return _Resp()
    monkeypatch.setattr(fila.urllib.request, 'urlopen', online)
    assert fila.enviar_lote(fila.rotas_envio('http://central/api/v2/medicoes'), 'AA', {}, {'provedor': {'nome': 'X'}}) == 3
    assert fila.pendentes() == 0
    assert [i['coletado_em'] for i in enviados[0]['itens']] == [1000, 1001, 1002] and enviados[0]['provedor'] == {'nome': 'X'}


def test_queue_is_bounded(fila, monkeypatch):
    monkeypatch.setattr(fila, 'MAX_ITENS', 150)
    agora = time.time()
    fila.enfileirar({'tipo': 'rede', 'coletado_em': agora - 30 * 86400})  # mais velho que NOC_FILA_MAX_DIAS
    for i in range(199):
        fila.enfileirar({'tipo': 'rede', 'coletado_em': agora + i})
    assert fila.pendentes() <= 150
    _, itens = fila._proximo_lote()
    assert itens[0]['coletado_em'] >= agora  # os mais antigos saíram primeiro


def test_secondary_link_is_prepared_but_off_by_default(fila, monkeypatch):
    assert [r['nome'] for r in fila.rotas_envio('http://p')] == ['principal']
    monkeypatch.setenv('NOC_CENTRAL_URL_SECUNDARIA', 'http://s')
    assert [r['nome'] for r in fila.rotas_envio('http://p')] == ['principal']
    monkeypatch.setenv('NOC_LINK_SECUNDARIO', '1')
    rotas = fila.rotas_envio('http://p')
    assert [r['url'] for r in rotas] == ['http://p', 'http://s']
    fila.enfileirar({'tipo': 'rede', 'coletado_em': 1})
    usados = []

    def so_secundaria(req, timeout=None):
        usados.append(req.full_url)
        if req.full_url == 'http://p':
            raise OSError('link principal fora')
        return _Resp()
    monkeypatch.setattr(fila.urllib.request, 'urlopen', so_secundaria)
    assert fila.enviar_lote(rotas, 'AA', {}) == 1 and usados == ['http://p', 'http://s']


# ---------------------------------------------------------------- Central
def _sensor_caido(mac, nome, provedor=None):
    conn = database.get_db()
    conn.execute("INSERT INTO sensores (mac_id, nome_local, status, memoria_alerta, em_manutencao, provedor, last_seen) "
                 "VALUES (?, ?, 'online', 'ONLINE', 0, ?, '2000-01-01 00:00:00')", (mac, nome, provedor))
    conn.commit(); conn.close()


def _guardiao():
    conn = database.get_db()
    try:
        central.verificar_quedas_global(conn, force=True)
    finally:
        conn.close()


def _q(sql):
    conn = database.get_db()
    rows = [dict(r) for r in conn.execute(sql).fetchall()]
    conn.close()
    return rows


def test_missing_heartbeat_opens_site_without_communication(client):
    _sensor_caido('AA:00:00:00:40:01', 'Loja Sul')
    _guardiao()
    d = _q("SELECT * FROM diagnosticos")
    assert [(x['chave'], x['estado']) for x in d] == [('sem_comunicacao', 'ativo')]
    msgs = [r['message'] for r in _q('SELECT message FROM telegram_outbox')]
    assert len(msgs) == 1 and 'OFFLINE TOTAL' in msgs[0] and 'Site Loja Sul sem comunicação com a Central desde' in msgs[0]

    r = client.post('/api/v2/report_data', json={'mac_id': 'AA:00:00:00:40:01', 'ip_local': '10.0.0.2'})
    assert r.status_code == 200
    assert _q("SELECT estado FROM diagnosticos")[0]['estado'] == 'resolvido'
    assert any('Fora do ar' in r['message'] for r in _q('SELECT message FROM telegram_outbox'))


def test_many_sites_offline_on_same_provider_send_one_alert(client):
    for i in range(3):
        _sensor_caido(f'AA:00:00:00:41:0{i}', f'Filial {i}', provedor='Claro')
    _guardiao()
    grupo = _q("SELECT * FROM diagnosticos WHERE chave='queda_provedor'")
    assert len(grupo) == 1 and json.loads(grupo[0]['evidencias'])['sites'] == 3
    msgs = [r['message'] for r in _q('SELECT message FROM telegram_outbox')]
    assert len(msgs) == 1 and 'POSSÍVEL QUEDA DO PROVEDOR' in msgs[0]


def test_communication_timeout_is_configurable(client, monkeypatch):
    conn = database.get_db()
    ha_2_min = "NOW() - INTERVAL '120 seconds'" if database.DATABASE_URL else "datetime('now', '-120 seconds')"
    conn.execute(f"INSERT INTO sensores (mac_id, nome_local, status, memoria_alerta, em_manutencao, last_seen) "
                 f"VALUES ('AA:00:00:00:42:01', 'Loja Norte', 'online', 'ONLINE', 0, {ha_2_min})")
    conn.commit(); conn.close()
    monkeypatch.setattr(central, 'SITE_SEM_COMUNICACAO_S', 600)
    _guardiao()
    assert _q('SELECT * FROM diagnosticos') == []
    monkeypatch.setattr(central, 'SITE_SEM_COMUNICACAO_S', 60)
    _guardiao()
    assert [d['chave'] for d in _q('SELECT chave FROM diagnosticos')] == ['sem_comunicacao']
