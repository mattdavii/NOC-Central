"""NexoGuard fase 1: o sensor manda as portas, a Central gera os achados, a MD aceita riscos; cliente não vê nada."""
import json
import time

import database
import descoberta
import nexoguard

SENSOR = 'AA:00:00:00:90:01'
ROTEADOR = {'ip': '10.0.0.1', 'mac': '08:55:31:00:00:01', 'status': 'online', 'tipo_equipamento': 'Roteador/Firewall'}
SERVIDOR = {'ip': '10.0.0.20', 'mac': '00:15:5D:00:00:20', 'status': 'online'}


def _preparar():
    conn = database.get_db()
    conn.execute('DELETE FROM guard_achados')
    conn.execute('DELETE FROM dispositivos WHERE sensor_mac = ?', (SENSOR,))
    conn.execute('DELETE FROM sensores WHERE mac_id = ?', (SENSOR,))
    conn.execute("INSERT INTO sensores (mac_id, nome_local, ip_gateway, cliente_id) VALUES (?, 'Escritório', '10.0.0.1', 2)", (SENSOR,))
    conn.commit(); conn.close()


def _enviar(client, *dispositivos):
    r = client.post('/api/v2/atualizar_dispositivos', json={'mac_id': SENSOR, 'lista': list(dispositivos)})
    assert r.status_code == 200


def _achados():
    conn = database.get_db()
    rows = {(r['dispositivo'], r['regra']): dict(r) for r in conn.execute('SELECT * FROM guard_achados').fetchall()}
    conn.close()
    return rows


def test_regras_leem_so_o_que_o_sensor_testou():
    assert nexoguard.avaliar({'portas_abertas': None}) is None                  # sem teste de portas: nada a dizer
    assert nexoguard.avaliar({'portas_abertas': '[]'}) == []
    regras = [r for r, _ in nexoguard.avaliar({'portas_abertas': '[23, 80, 8291]', 'tipo_equipamento': 'Roteador/Firewall',
                                               'upnp_igd': 1})]
    assert regras == ['telnet', 'upnp', 'http_gerencia', 'winbox']
    # HTTP sem HTTPS só conta em equipamento de infraestrutura identificado, e HTTPS presente apaga o achado
    assert nexoguard.avaliar({'portas_abertas': '[80]'}) == []
    assert nexoguard.avaliar({'portas_abertas': '[80, 443]', 'tipo_equipamento': 'Câmera IP'}) == []
    assert nexoguard.portas_da_rede([3389, 23, 23]) == '[23, 3389]'
    for ruim in ('23', [23, 'x'], [0], [70000], [True], list(range(1, 70))):
        assert nexoguard.portas_da_rede(ruim) is None


def test_achado_abre_resolve_sozinho_e_reabre(client):
    _preparar()
    _enviar(client, dict(ROTEADOR, portas_abertas=[23, 80, 443], upnp_igd=True), dict(SERVIDOR, portas_abertas=[445, 3389]))
    a = _achados()
    assert set(a) == {('08:55:31:00:00:01', 'telnet'), ('08:55:31:00:00:01', 'upnp'),
                      ('00:15:5D:00:00:20', 'smb'), ('00:15:5D:00:00:20', 'rdp')}
    assert all(x['estado'] == 'aberto' for x in a.values())
    assert a[('08:55:31:00:00:01', 'telnet')]['gravidade'] == 'alto'

    # sensor antigo (sem portas) e equipamento fora da rede não mudam nada
    _enviar(client, ROTEADOR)
    assert all(x['estado'] == 'aberto' for x in _achados().values())
    conn = database.get_db()
    assert conn.execute('SELECT portas_abertas FROM dispositivos WHERE mac = ?', (ROTEADOR['mac'],)).fetchone()[0] == '[23, 80, 443]'
    conn.close()

    # Telnet desligado: some o achado; o servidor sumiu da rede e fica como estava
    _enviar(client, dict(ROTEADOR, portas_abertas=[80, 443], upnp_igd=True))
    a = _achados()
    assert a[('08:55:31:00:00:01', 'telnet')]['estado'] == 'resolvido'
    assert a[('08:55:31:00:00:01', 'upnp')]['estado'] == 'aberto'
    assert a[('00:15:5D:00:00:20', 'rdp')]['estado'] == 'aberto'

    # Telnet voltou: o mesmo achado reabre (sem duplicar)
    _enviar(client, dict(ROTEADOR, portas_abertas=[23, 80, 443], upnp_igd=True))
    a = _achados()
    assert a[('08:55:31:00:00:01', 'telnet')]['estado'] == 'aberto' and a[('08:55:31:00:00:01', 'telnet')]['resolvido_em'] is None
    assert len(a) == 4


def test_so_a_md_ve_e_aceitar_risco_exige_motivo(client, login):
    _preparar()
    _enviar(client, dict(SERVIDOR, portas_abertas=[3389]))
    achado_id = _achados()[('00:15:5D:00:00:20', 'rdp')]['id']

    cliente = login(2, 'Cliente')
    assert cliente.get('/guard').status_code == 403
    assert cliente.post(f'/api/v2/guard/{achado_id}/aceitar', json={'motivo': 'x'}).status_code == 403

    md = login(1)
    pagina = md.get('/guard')
    assert pagina.status_code == 200
    html = pagina.get_data(as_text=True)
    assert 'Área de Trabalho Remota (RDP) aberta' in html and 'Escritório' in html and 'só a MD vê' in html
    assert md.post(f'/api/v2/guard/{achado_id}/aceitar', json={'motivo': '  '}).status_code == 400
    assert md.post(f'/api/v2/guard/{achado_id}/aceitar', json={'motivo': 'Servidor acessado só pela VPN'}).status_code == 200
    assert md.post(f'/api/v2/guard/{achado_id}/aceitar', json={'motivo': 'de novo'}).status_code == 400

    # a condição continua, mas o risco aceito não volta a ficar aberto
    _enviar(client, dict(SERVIDOR, portas_abertas=[3389]))
    a = _achados()[('00:15:5D:00:00:20', 'rdp')]
    assert a['estado'] == 'aceito' and a['motivo'] == 'Servidor acessado só pela VPN' and a['aceito_por'] == 'user1'
    assert 'Servidor acessado só pela VPN' in md.get('/guard?estado=aceito').get_data(as_text=True)
    assert md.post(f'/api/v2/guard/{achado_id}/reabrir').status_code == 200
    assert _achados()[('00:15:5D:00:00:20', 'rdp')]['estado'] == 'aberto'

    conn = database.get_db()
    acoes = [r['acao'] for r in conn.execute("SELECT acao FROM auditoria_acessos WHERE acao LIKE 'guard_%'").fetchall()]
    conn.close()
    assert acoes.count('guard_aceitar') == 1 and acoes.count('guard_reabrir') == 1


def test_sensor_manda_portas_so_quando_testou(monkeypatch):
    monkeypatch.setattr(descoberta, 'ATIVO', True)
    monkeypatch.setattr(descoberta, '_garantir_worker', lambda: None)
    agora = time.time()
    descoberta._cache.clear()
    descoberta._cache['00:15:5d:00:00:20'] = ({'portas': [3389, 445], 'portas_testadas': True,
                                              'upnp': {'tipo_upnp': 'InternetGatewayDevice'}}, agora)
    descoberta._cache['da:a1:19:00:00:01'] = ({'portas': [], 'portas_testadas': False}, agora)   # celular: não testado
    disp = [{'ip': '10.0.0.20', 'mac': '00:15:5d:00:00:20'}, {'ip': '10.0.0.30', 'mac': 'da:a1:19:00:00:01'}]
    descoberta.anotar(disp, ip_local='10.0.0.10', gateway_ip='10.0.0.1')
    assert disp[0]['portas_abertas'] == [3389, 445] and disp[0]['upnp_igd'] is True
    assert 'portas_abertas' not in disp[1]
    assert {21, 445, 3389, 5900} <= set(descoberta.PORTAS)
    descoberta._cache.clear(); descoberta._vistos.clear()
