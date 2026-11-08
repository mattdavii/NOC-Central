"""Tela de operação: menos tráfego e menos consultas sem perder o "ao vivo"."""
import gzip
import json

import app as central
import database


def _sensores(n):
    conn = database.get_db()
    for i in range(n):
        conn.execute("INSERT INTO sensores (mac_id, nome_local, status) VALUES (?, ?, 'online')",
                     (f'AA:00:00:00:10:{i:02X}', f'Local de teste número {i} com um nome comprido'))
    conn.commit(); conn.close()


def test_mapa_vai_comprimido_e_responde_304_quando_nao_mudou(client, login):
    _sensores(20)
    md = login(1)
    r = md.get('/api/v2/mapa_sensores', headers={'Accept-Encoding': 'gzip, br'})
    assert r.status_code == 200 and r.headers['Content-Encoding'] == 'gzip'
    bruto = gzip.decompress(r.get_data())
    assert len(r.get_data()) < len(bruto) / 3
    assert len(json.loads(bruto)['sensores']) == 20
    etag = r.headers['ETag']
    igual = md.get('/api/v2/mapa_sensores', headers={'If-None-Match': etag})
    assert igual.status_code == 304 and igual.get_data() == b''
    # Sem Accept-Encoding: sai sem compressão (clientes antigos e testes continuam funcionando)
    assert 'Content-Encoding' not in md.get('/api/v2/mapa_sensores').headers


def test_cache_curto_do_mapa_e_por_escopo(client, login, monkeypatch):
    _sensores(2)
    chamadas = []
    original = central._montar_mapa_sensores
    monkeypatch.setattr(central, '_montar_mapa_sensores', lambda: chamadas.append(1) or original())
    md = login(1)
    for _ in range(5):
        assert len(md.get('/api/v2/mapa_sensores').json['sensores']) == 2
    assert len(chamadas) == 1                      # cinco telas, uma consulta
    cliente = login(2, 'Cliente')
    assert cliente.get('/api/v2/mapa_sensores').json['sensores'] == []   # cliente não herda o mapa da MD
    assert len(chamadas) == 2


def test_aviso_as_telas_tem_limite_de_frequencia(monkeypatch):
    enviados, agendados = [], []
    monkeypatch.setattr(central.socketio, 'emit', lambda *a, **k: enviados.append(a[0]))
    monkeypatch.setattr(central.socketio, 'start_background_task', lambda f: agendados.append(f))
    central._aviso_telas.update(ultimo=0.0, agendado=False)
    for _ in range(50):                            # 50 batimentos quase juntos
        central.avisar_telas()
    assert enviados == ['atualizacao_global'] and len(agendados) == 1   # um agora, um no fim do intervalo
    monkeypatch.setattr(central.socketio, 'sleep', lambda s: None)
    agendados[0]()
    assert enviados == ['atualizacao_global', 'atualizacao_global']
    central._aviso_telas.update(ultimo=0.0, agendado=False)


def test_paginas_e_respostas_pequenas(client, login):
    md = login(1)
    pagina = md.get('/pulse', headers={'Accept-Encoding': 'gzip'})
    assert pagina.headers.get('Content-Encoding') == 'gzip'
    pequena = md.get('/healthz', headers={'Accept-Encoding': 'gzip'})
    assert 'Content-Encoding' not in pequena.headers
