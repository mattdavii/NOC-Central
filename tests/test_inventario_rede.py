"""Inventário de dispositivos por MAC (sem duplicar quando muda o IP, guardando quem saiu da rede)
e exportação da lista e do diagrama pelo acesso remoto (Central) e local (painel do sensor)."""
import json

import pytest

import database
import edge_local

MAC_S = 'AA:BB:CC:00:00:55'
CAM, NVR, CEL = '00:BC:99:12:34:56', '08:ED:ED:12:34:56', 'DA:A1:19:00:00:40'


def _d(ip, mac, status='online', **extra):
    return {'ip': ip, 'mac': mac, 'status': status, 'latencia': 2.0 if status == 'online' else 0,
            'nome': 'Desconhecido', 'fabricante': 'Desconhecido', **extra}


# ------------------------------------------------------------------ regras
def test_same_mac_on_two_ips_keeps_the_present_one():
    lista = [_d('192.168.0.20', CAM, 'desconhecido', neighbor_state='AUSENTE'), _d('192.168.0.21', CAM),
             _d('192.168.0.30', NVR, 'online'), _d('192.168.0.31', NVR, 'online'),       # dois IPs de verdade
             {'ip': '10.0.5.9', 'mac': None, 'status': 'online'}]
    saida = edge_local.deduplicar_por_mac(lista)
    assert sorted(d['ip'] for d in saida) == ['10.0.5.9', '192.168.0.21', '192.168.0.30', '192.168.0.31']
    assert edge_local.chave_dispositivo({'mac': 'aa-bb-cc-00-00-01'}) == 'AA:BB:CC:00:00:01'
    assert edge_local.chave_dispositivo({'ip': '10.0.5.9'}) == 'ip:10.0.5.9'


def test_local_inventory_updates_ip_by_mac_and_keeps_offline(tmp_path):
    inv = edge_local.InventarioLocal(str(tmp_path / 'l.db'))
    inv.iniciar()
    inv.atualizar([_d('192.168.0.20', CAM), _d('192.168.0.40', CEL)], '2026-11-05T10:00:00Z')
    inv.atualizar([_d('192.168.0.21', CAM)], '2026-11-05T11:00:00Z')              # câmera trocou de IP, celular saiu
    lista = {d['mac']: d for d in inv.listar()}
    assert len(lista) == 2
    assert lista[CAM]['ip'] == '192.168.0.21' and lista[CAM]['status'] == 'online'
    assert lista[CAM]['primeiro_visto'] == '2026-11-05T10:00:00Z' and lista[CAM]['visto_em'] == '2026-11-05T11:00:00Z'
    assert lista[CEL]['status'] == 'offline' and lista[CEL]['visto_em'] == '2026-11-05T10:00:00Z' and lista[CEL]['latencia'] is None
    assert not inv.esquecer(CAM)                                                      # presente: não esquece
    assert inv.esquecer(CEL) and [d['mac'] for d in inv.listar()] == [CAM]
    inv.atualizar([_d('192.168.0.21', CAM), _d('192.168.0.40', CEL)])                 # voltou: entra de novo
    assert len(inv.listar()) == 2


# ------------------------------------------------------------------ Central
def _sensor(conn):
    conn.execute("DELETE FROM dispositivos WHERE sensor_mac = ?", (MAC_S,))
    conn.execute("INSERT INTO sensores (mac_id, nome_local, cliente_id, ip_gateway, ip_sensor, status) "
                 "VALUES (?, 'Usina', 2, '192.168.0.1', '192.168.0.10', 'ONLINE')", (MAC_S,))
    conn.commit()


def _linhas(conn):
    return {r['mac'] or r['ip']: dict(r) for r in conn.execute('SELECT * FROM dispositivos WHERE sensor_mac = ?', (MAC_S,)).fetchall()}


def test_central_keeps_one_row_per_mac_and_marks_offline(client, login):
    conn = database.get_db()
    _sensor(conn)
    # Duplicado gravado pela versão anterior (o mesmo MAC em dois IPs).
    conn.execute("INSERT INTO dispositivos (sensor_mac, ip, mac, status) VALUES (?, '192.168.0.19', ?, 'online')", (MAC_S, CAM))
    conn.execute("INSERT INTO dispositivos (sensor_mac, ip, mac, status) VALUES (?, '192.168.0.20', ?, 'online')", (MAC_S, CAM))
    conn.commit(); conn.close()
    envio = lambda lista: client.post('/api/v2/atualizar_dispositivos', json={'mac_id': MAC_S, 'lista': lista})
    assert envio([_d('192.168.0.20', CAM), _d('192.168.0.40', CEL)]).status_code == 200
    assert envio([_d('192.168.0.25', CAM, 'online'), _d('192.168.0.20', CAM, 'desconhecido')]).status_code == 200
    conn = database.get_db()
    linhas = _linhas(conn)
    conn.close()
    assert len(linhas) == 2
    assert linhas[CAM]['ip'] == '192.168.0.25' and linhas[CAM]['status'] == 'online'
    assert linhas[CEL]['status'] == 'offline' and linhas[CEL]['visto_em']
    # Fora da rede aparece no diagrama (em vermelho) e no levantamento.
    md = login()
    arvore = md.get(f'/api/v2/sensor/{MAC_S}/mapa_rede').json['arvore']
    assert next(n for n in arvore if n.get('mac') == CEL)['status'] == 'offline'
    dados = json.loads(md.get(f'/api/v2/levantamento/{MAC_S}/json').data)
    assert next(d for d in dados['dispositivos'] if d['mac'] == CEL)['status'] == 'offline'
    # Esquecer: só quem está fora da rede.
    assert md.post(f'/api/v2/sensor/{MAC_S}/esquecer_dispositivo', json={'chave': CAM}).status_code == 400
    assert md.post(f'/api/v2/sensor/{MAC_S}/esquecer_dispositivo', json={'chave': CEL}).status_code == 200
    conn = database.get_db()
    assert list(_linhas(conn)) == [CAM]
    conn.close()
    assert login(3, 'Cliente').post(f'/api/v2/sensor/{MAC_S}/esquecer_dispositivo', json={'chave': CAM}).status_code == 403


def test_central_exports_diagram_as_pdf_and_svg(client, login):
    pytest.importorskip('weasyprint')
    conn = database.get_db()
    _sensor(conn)
    conn.close()
    client.post('/api/v2/atualizar_dispositivos', json={'mac_id': MAC_S, 'lista': [_d('192.168.0.20', CAM)]})
    md = login()
    r = md.get(f'/api/v2/sensor/{MAC_S}/diagrama.pdf')
    assert r.status_code == 200 and r.data.startswith(b'%PDF') and 'diagrama-rede-Usina.pdf' in r.headers['Content-Disposition']
    assert md.get(f'/api/v2/sensor/{MAC_S}/diagrama.svg').data.startswith(b'<svg')
    assert login(3, 'Cliente').get(f'/api/v2/sensor/{MAC_S}/diagrama.pdf').status_code == 403


def test_central_sends_the_saved_diagram_to_the_sensor_only_when_it_changes(client, login):
    conn = database.get_db()
    _sensor(conn)
    conn.close()
    r = client.post('/api/v2/report_data', json={'mac_id': MAC_S, 'nome_local': 'Usina'})
    versao = r.json['mapa_rede']['versao']
    assert r.json['mapa_rede']['mapa'] is None
    assert 'mapa_rede' not in client.post('/api/v2/report_data', json={'mac_id': MAC_S, 'nome_local': 'Usina', 'mapa_rede_versao': versao}).json
    mapa = {'versao': 1, 'extras': [{'id': 'm:sw1', 'nome': 'Switch do rack'}], 'nos': {f'd:{CAM}': {'pai': 'm:sw1', 'meio': 'cabo'}}}
    assert login().put(f'/api/v2/sensor/{MAC_S}/mapa_rede', json={'mapa': mapa}).status_code == 200
    novo = client.post('/api/v2/report_data', json={'mac_id': MAC_S, 'nome_local': 'Usina', 'mapa_rede_versao': versao}).json['mapa_rede']
    assert novo['versao'] != versao and novo['mapa']['extras'][0]['nome'] == 'Switch do rack'


# ------------------------------------------------------------------ painel local
def test_local_panel_exports_inventory_with_offline_and_diagram(monkeypatch, tmp_path):
    import nexopulse_edge as agent
    from werkzeug.security import generate_password_hash
    monkeypatch.setattr(agent, 'get_mac', lambda: MAC_S)
    monkeypatch.chdir(tmp_path)
    acesso = edge_local.AcessoLocal('sensor_local.db')
    monkeypatch.setattr(agent, 'ACESSO_LOCAL', acesso)
    monkeypatch.setattr(agent._EstadoPainel, 'acesso', acesso)
    inv = edge_local.InventarioLocal('sensor_local.db')
    monkeypatch.setattr(agent, 'INVENTARIO_LOCAL', inv)
    monkeypatch.setattr(agent._EstadoPainel, 'inventario', inv)
    monkeypatch.setattr(agent, 'EVENTOS_LOCAIS', edge_local.Eventos('sensor_local.db'))
    monkeypatch.delenv('NOC_EDGE_SENHA', raising=False)
    acesso.receber_da_central({'usuario': 'nexopulse', 'senha_hash': generate_password_hash('senha-do-painel', method='pbkdf2:sha256')})
    agent.init_local_db()
    monkeypatch.setitem(agent.dados_sensores, 'gateway_ip', '192.168.0.1')
    monkeypatch.setitem(agent.dados_sensores, 'meu_ip', '192.168.0.10')
    inv.atualizar([_d('192.168.0.1', 'F4:F2:6D:AA:BB:CC'), _d('192.168.0.20', CAM), _d('192.168.0.40', CEL)])
    inv.atualizar([_d('192.168.0.1', 'F4:F2:6D:AA:BB:CC'), _d('192.168.0.21', CAM)])
    monkeypatch.setitem(agent.dados_sensores, 'topologia', [_d('192.168.0.1', 'F4:F2:6D:AA:BB:CC'), _d('192.168.0.21', CAM)])
    mapa = {'versao': 1, 'extras': [{'id': 'm:sw1', 'nome': 'Switch do rack'}], 'nos': {f'd:{CAM}': {'pai': 'm:sw1'}}}
    edge_local.gravar_config('sensor_local.db', 'mapa_rede', {'versao': 'v1', 'em': '2026-11-05T10:00:00+00:00', 'provedor': 'Starlink', 'mapa': mapa})
    agent.app.config['TESTING'] = True
    cli = agent.app.test_client()
    assert cli.post('/login', data={'usuario': 'nexopulse', 'senha': 'senha-do-painel'}).status_code == 302

    dados = json.loads(cli.get('/api/levantamento.json').data)
    por_mac = {d['mac']: d for d in dados['dispositivos'] if d['mac']}
    assert por_mac[CAM]['ip'] == '192.168.0.21' and por_mac[CEL]['status'] == 'offline'
    assert sum(1 for d in dados['dispositivos'] if d['mac'] == CAM) == 1
    assert any(l['equipamento'] == 'Switch do rack' for l in dados['diagrama'])
    svg = cli.get('/api/diagrama.svg?baixar=1')
    assert svg.status_code == 200 and b'Switch do rack' in svg.data and b'Starlink' in svg.data and 'attachment' in svg.headers['Content-Disposition']
    pagina = cli.get('/diagrama')
    assert pagina.status_code == 200 and b'Imprimir ou salvar em PDF' in pagina.data and b'Switch do rack' in pagina.data
    assert cli.post('/api/inventario/esquecer', json={'chave': CAM}).status_code == 400
    assert cli.post('/api/inventario/esquecer', json={'chave': CEL}).status_code == 200
    dados = json.loads(cli.get('/api/levantamento.json').data)
    assert CEL not in {d['mac'] for d in dados['dispositivos']}
    # Sem diagrama salvo, todo mundo aparece ligado direto no gateway.
    edge_local.gravar_config('sensor_local.db', 'mapa_rede', {})
    assert cli.get('/api/diagrama.svg').status_code == 200
