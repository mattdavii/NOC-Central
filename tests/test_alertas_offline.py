"""Alertas registrados com a Central fora: o sensor guarda na fila e a Central recebe depois,
com o horário original, sem duplicar, e manda um resumo só no Telegram."""
import json
import sqlite3
import time
import urllib.request

import pytest

import app as central
import database
import fila_envio
import nexopulse_edge as agent

MAC = 'AA:BB:CC:00:00:55'


# ===================================================================== sensor
@pytest.fixture()
def fila(tmp_path, monkeypatch):
    monkeypatch.setattr(fila_envio, 'DB', str(tmp_path / 'fila.db'))
    ev = agent.edge_local.Eventos(str(tmp_path / 'ev.db')); ev.iniciar()
    monkeypatch.setattr(agent, 'EVENTOS_LOCAIS', ev)
    return ev


def _itens_na_fila():
    conn = sqlite3.connect(fila_envio.DB)
    try:
        return [json.loads(r[0]) for r in conn.execute('SELECT item FROM fila_envio ORDER BY id')]
    finally:
        conn.close()


def test_central_fora_alerta_vai_para_a_fila(fila, monkeypatch):
    def falha(*a, **k): raise OSError('Connection refused')
    monkeypatch.setattr(agent.urllib.request, 'urlopen', falha)
    antes = time.time()
    assert agent.enviar_alertas('https://central/api/v2/alertas_ia', [
        {'tipo': 'Queda de CFTV', 'gravidade': 'Crítica', 'detalhes': 'NVR (10.0.0.9) parou'},
        {'tipo': 'Queda de Energia', 'gravidade': 'Crítica', 'detalhes': 'Sem energia no rack'}]) is False
    itens = _itens_na_fila()
    assert [i['tipo'] for i in itens] == ['alerta', 'alerta']
    assert itens[0]['alerta'] == {'tipo': 'Queda de CFTV', 'gravidade': 'Crítica', 'detalhes': 'NVR (10.0.0.9) parou'}
    assert antes - 0.01 <= itens[0]['coletado_em'] <= time.time() and itens[0]['id'] != itens[1]['id']
    assert fila.listar()[0] == 2


def test_central_no_ar_nada_vai_para_a_fila(fila, monkeypatch):
    monkeypatch.setattr(agent.urllib.request, 'urlopen', lambda *a, **k: None)
    assert agent.enviar_alertas('https://central/api/v2/alertas_ia', [{'tipo': 'X', 'gravidade': 'OK', 'detalhes': 'y'}]) is True
    assert fila_envio.pendentes() == 0


def test_lote_informa_quantos_ficam_na_fila(fila, monkeypatch):
    for n in range(fila_envio.LOTE + 3):
        fila_envio.enfileirar({'tipo': 'rede', 'coletado_em': time.time(), 'n': n})
    enviados = []

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b'{}'
    monkeypatch.setattr(urllib.request, 'urlopen', lambda req, timeout=0: (enviados.append(json.loads(req.data)), Resp())[1])
    rotas = fila_envio.rotas_envio('https://central/api/v2/medicoes')
    assert fila_envio.enviar_lote(rotas, MAC, {}) == fila_envio.LOTE
    assert fila_envio.enviar_lote(rotas, MAC, {}) == 3
    assert [e['restantes'] for e in enviados] == [3, 0]


# ===================================================================== Central
@pytest.fixture()
def sensor(client, monkeypatch):
    conn = database.get_db()
    conn.execute('DELETE FROM resumos_offline')
    conn.execute("INSERT INTO sensores (mac_id, nome_local, cliente_id, status) VALUES (?, 'Usina Teste', 2, 'online')", (MAC,))
    conn.commit(); conn.close()
    mensagens = []
    monkeypatch.setattr(central, 'enviar_telegram', lambda texto, cliente_id=None, conn=None: mensagens.append((texto, cliente_id)))
    return client, mensagens


def _alerta(ts, ident, tipo='Queda de CFTV', gravidade='Crítica', detalhes='NVR parou'):
    return {'tipo': 'alerta', 'id': ident, 'coletado_em': ts, 'alerta': {'tipo': tipo, 'gravidade': gravidade, 'detalhes': detalhes}}


def _logs():
    conn = database.get_db()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM logs_ia WHERE sensor_mac=? ORDER BY id", (MAC,)).fetchall()]
    finally:
        conn.close()


def test_alerta_que_chega_logo_vira_alerta_normal(sensor):
    cli, msgs = sensor
    r = cli.post('/api/v2/medicoes', json={'mac_id': MAC, 'restantes': 0, 'itens': [_alerta(time.time() - 20, 'a' * 32)]})
    assert r.status_code == 200 and r.json['aceitos'] == 1
    log = _logs()[-1]
    assert log['tipo_evento'] == 'Queda de CFTV' and log['registrado_offline'] == 0
    assert len(msgs) == 1 and 'ALERTA CRÍTICO' in msgs[0][0] and msgs[0][1] == 2


def test_alertas_da_queda_entram_no_historico_e_viram_um_resumo(sensor):
    cli, msgs = sensor
    t0 = time.time() - 2 * 3600
    lote1 = [_alerta(t0 + 60, 'b' * 32, 'Queda de CFTV', 'Crítica', 'Câmera portão parou'),
             _alerta(t0 + 600, 'c' * 32, 'Queda de Energia', 'Crítica', 'Sem energia no rack')]
    lote2 = [_alerta(t0 + 3000, 'd' * 32, 'CFTV Restabelecido', 'OK', 'Câmera portão voltou')]
    assert cli.post('/api/v2/medicoes', json={'mac_id': MAC, 'restantes': 1, 'itens': lote1}).status_code == 200
    assert msgs == []                                                  # ainda há fila: nada de Telegram
    assert cli.post('/api/v2/medicoes', json={'mac_id': MAC, 'restantes': 0, 'itens': lote2}).status_code == 200
    assert len(msgs) == 1                                              # um resumo só
    texto = msgs[0][0]
    assert 'EVENTOS DURANTE A INDISPONIBILIDADE' in texto and 'Usina Teste' in texto
    assert '3 evento(s)' in texto and '2 crítico(s)' in texto and 'Queda de Energia' in texto
    logs = _logs()
    assert [l['registrado_offline'] for l in logs] == [1, 1, 1]
    assert all('Central indisponível' in l['detalhes'] for l in logs)
    # reenvio do mesmo lote (resposta perdida no caminho): não duplica nem manda de novo
    assert cli.post('/api/v2/medicoes', json={'mac_id': MAC, 'restantes': 0, 'itens': lote1}).json['aceitos'] == 2
    assert len(_logs()) == 3 and len(msgs) == 1


def test_horario_do_alerta_e_o_de_quando_aconteceu(sensor):
    cli, _ = sensor
    ts = time.time() - 5 * 3600
    cli.post('/api/v2/medicoes', json={'mac_id': MAC, 'restantes': 0, 'itens': [_alerta(ts, 'e' * 32)]})
    conn = database.get_db()
    try:
        agora_banco = conn.execute('SELECT CURRENT_TIMESTAMP AS t').fetchone()['t']
        registrado = conn.execute('SELECT data_hora FROM logs_ia WHERE origem_id=?', ('e' * 32,)).fetchone()['data_hora']
    finally:
        conn.close()
    para_dt = lambda v: v if hasattr(v, 'year') else __import__('datetime').datetime.fromisoformat(str(v))
    diferenca = (para_dt(agora_banco) - para_dt(registrado)).total_seconds()
    assert 5 * 3600 - 120 < diferenca < 5 * 3600 + 120


def test_alerta_mal_formado_e_descartado(sensor):
    cli, _ = sensor
    ruins = [{'tipo': 'alerta', 'id': 'x', 'coletado_em': time.time(), 'alerta': {'tipo': 'A', 'gravidade': 'OK', 'detalhes': 'b'}},
             {'tipo': 'alerta', 'id': 'f' * 32, 'coletado_em': time.time(), 'alerta': 'texto'},
             {'tipo': 'alerta', 'id': 'g' * 32, 'coletado_em': time.time() - 30 * 86400, 'alerta': {'tipo': 'A', 'gravidade': 'OK', 'detalhes': 'b'}}]
    r = cli.post('/api/v2/medicoes', json={'mac_id': MAC, 'restantes': 0, 'itens': ruins})
    assert r.json == {'status': 'OK', 'aceitos': 0, 'descartados': 3}
    assert _logs() == []


def test_ponta_a_ponta_sensor_guarda_e_central_recebe_quando_volta(fila, sensor, monkeypatch):
    cli, msgs = sensor
    # 1. Central fora: o sensor guarda dois alertas com o horário real (simulando 1 h atrás)
    def fora(*a, **k): raise OSError('Central fora')
    monkeypatch.setattr(agent.urllib.request, 'urlopen', fora)
    real_time = time.time
    monkeypatch.setattr(agent.time, 'time', lambda: real_time() - 3600)
    agent.enviar_alertas('https://central/api/v2/alertas_ia', [{'tipo': 'Queda de Energia', 'gravidade': 'Crítica', 'detalhes': 'Rack sem energia'}])
    agent.enviar_alertas('https://central/api/v2/alertas_ia', [{'tipo': 'Energia Restaurada', 'gravidade': 'OK', 'detalhes': 'Rack voltou'}])
    monkeypatch.setattr(agent.time, 'time', real_time)
    assert fila_envio.pendentes() == 2

    # 2. Central volta: a fila do sensor entrega na rota /api/v2/medicoes da Central de verdade
    class Resp:
        def __init__(self, r): self.r = r
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return self.r.data

    def central_no_ar(req, timeout=0):
        r = cli.post('/api/v2/medicoes', data=req.data, content_type='application/json')
        assert r.status_code == 200
        return Resp(r)
    monkeypatch.setattr(urllib.request, 'urlopen', central_no_ar)
    fila_envio.enviar_lote(fila_envio.rotas_envio('https://central/api/v2/medicoes'), MAC, {'Content-Type': 'application/json'})
    assert fila_envio.pendentes() == 0
    logs = _logs()
    assert [(l['tipo_evento'], l['registrado_offline']) for l in logs] == [('Queda de Energia', 1), ('Energia Restaurada', 1)]
    assert len(msgs) == 1 and '2 evento(s)' in msgs[0][0]
